#!/usr/bin/env python3
"""Dynamic engineering checker for single-part CCX workdirs.

The original single-part kits were handwritten, and several of them used
closed-form constants from the reference design rather than submitted
artifacts.  This checker is intentionally artifact-driven:

* solver values come from ``model.dat``;
* geometry and mass come from ``mesh.inp`` / ``model.inp`` plus density;
* non-FEA report fields can come from ``meta.json`` under
  ``engineering_metrics`` / ``metrics`` / ``report`` or as top-level keys.

If a requirement cannot be tied to one of those submitted sources, it fails
with an explicit missing/unsupported reason instead of passing a reference
constant.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from ccx_eval.engineering_requirements import (  # noqa: E402
    requirement_id,
    requirement_text,
    source_requirements,
)


NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = NUMBER_RE.search(value.replace(",", ""))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def limit_value(req: dict[str, Any]) -> tuple[str, float] | None:
    for key in (
        "limit",
        "threshold",
        "limit_MPa",
        "limit_mm",
        "limit_kg",
        "limit_g",
        "limit_Hz",
        "limit_N",
        "limit_kN",
        "limit_kNm",
        "limit_kg_m",
        "limit_kg_m2",
        "allowable",
        "minimum",
        "maximum",
        "min",
        "max",
        "lower_limit_mm",
        "upper_limit_mm",
    ):
        if key in req:
            val = as_float(req.get(key))
            if val is not None:
                return key, val
    return None


def operator_for(req: dict[str, Any], limit_key: str) -> str:
    op = str(req.get("operator") or "").strip().lower()
    if op in {"<", "<=", ">", ">=", "=="}:
        return op
    if op in {"less_than", "less_than_or_equal", "max", "maximum"}:
        return "<="
    if op in {"greater_than", "greater_than_or_equal", "min", "minimum"}:
        return ">="
    if limit_key in {"minimum", "min", "lower_limit_mm"}:
        return ">="
    return "<="


def compare(value: float, op: str, limit: float) -> bool:
    if op == "<":
        return value < limit
    if op == "<=":
        return value <= limit
    if op == ">":
        return value > limit
    if op == ">=":
        return value >= limit
    if op == "==":
        return math.isclose(value, limit, rel_tol=1e-3, abs_tol=1e-6)
    return False


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def flatten_metrics(meta: dict[str, Any]) -> dict[str, float]:
    """Return normalized numeric leaves from submitted metadata."""
    roots: list[Any] = [meta]
    for key in ("engineering_metrics", "metrics", "report", "requirements", "design_report"):
        value = meta.get(key)
        if isinstance(value, dict):
            roots.insert(0, value)

    out: dict[str, float] = {}

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, path + [str(k)])
            return
        if isinstance(value, list):
            for i, v in enumerate(value):
                walk(v, path + [str(i)])
            return
        number = as_float(value)
        if number is None or not path:
            return
        leaf = norm_key(path[-1])
        full = norm_key("_".join(path))
        out.setdefault(leaf, number)
        out.setdefault(full, number)

    for root in roots:
        walk(root, [])
    return out


def submitted_metric(meta_metrics: dict[str, float], req: dict[str, Any], rid: str) -> tuple[str, float] | None:
    metric = norm_key(str(req.get("metric") or ""))
    candidates = [
        norm_key(rid),
        metric,
        f"{norm_key(rid)}_{metric}" if metric else norm_key(rid),
        f"{metric}_{norm_key(rid)}" if metric else norm_key(rid),
    ]
    text = norm_key(requirement_text(req))
    if text:
        candidates.append(text)
    for key in candidates:
        if key in meta_metrics:
            return f"meta.json:{key}", meta_metrics[key]

    # Last-resort fuzzy match for report fields with unit suffixes.
    metric_tokens = [t for t in metric.split("_") if len(t) > 1]
    for key, value in meta_metrics.items():
        if metric_tokens and all(t in key for t in metric_tokens):
            return f"meta.json:{key}", value
    return None


def parse_density_tonne_per_mm3(meta: dict[str, Any], spec: dict[str, Any]) -> float | None:
    props = meta.get("material_properties")
    if isinstance(props, dict):
        val = as_float(props.get("density_tonne_per_mm3"))
        if val is not None:
            return val
        kg_m3 = as_float(props.get("density_kg_m3"))
        if kg_m3 is not None:
            return kg_m3 * 1.0e-12

    material = str(meta.get("material") or "")
    match = re.search(r"\*DENSITY\s+([0-9.Ee+-]+)", material.replace("\n", " "))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    def walk(value: Any) -> float | None:
        if isinstance(value, dict):
            for key, item in value.items():
                nk = norm_key(str(key))
                if nk in {"density_kg_m3", "density"}:
                    val = as_float(item)
                    if val is not None and val > 10.0:
                        return val * 1.0e-12
                found = walk(item)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found is not None:
                    return found
        return None

    return walk(spec)


def parse_density_from_inp(path: Path) -> float | None:
    if not path.exists():
        return None
    lines = path.read_text(errors="ignore").splitlines()
    for idx, raw in enumerate(lines):
        if raw.strip().upper().startswith("*DENSITY"):
            for candidate in lines[idx + 1: idx + 6]:
                stripped = candidate.strip()
                if not stripped or stripped.startswith("**"):
                    continue
                if stripped.startswith("*"):
                    break
                val = as_float(stripped.split(",")[0])
                if val is not None:
                    return val
    return None


@dataclass
class MeshMetrics:
    nodes: dict[int, tuple[float, float, float]]
    volume_mm3: float | None = None
    mass_kg: float | None = None
    bbox_spans_mm: tuple[float, float, float] | None = None
    bbox_area_m2: float | None = None
    section_area_mm2: float | None = None
    length_mm: float | None = None
    section_I_strong_mm4: float | None = None
    section_I_weak_mm4: float | None = None
    section_c_strong_mm: float | None = None
    section_c_weak_mm: float | None = None


def parse_mesh_metrics(mesh: Path, density_tonne_per_mm3: float | None) -> MeshMetrics:
    nodes: dict[int, tuple[float, float, float]] = {}
    elements: list[tuple[int, int, int, int]] = []
    section: str | None = None
    c3d = False

    if not mesh.exists():
        return MeshMetrics(nodes={})

    with mesh.open(errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("**"):
                continue
            if line.startswith("*"):
                upper = line.upper()
                if upper.startswith("*NODE"):
                    section = "node"
                    c3d = False
                elif upper.startswith("*ELEMENT"):
                    section = "element"
                    c3d = "TYPE=C3D" in upper
                else:
                    section = None
                    c3d = False
                continue
            parts = [p.strip() for p in line.split(",") if p.strip()]
            if section == "node" and len(parts) >= 4:
                try:
                    nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    continue
            elif section == "element" and c3d and len(parts) >= 5:
                try:
                    elements.append(tuple(int(p) for p in parts[1:5]))
                except ValueError:
                    continue

    metrics = MeshMetrics(nodes=nodes)
    if nodes:
        xs = [p[0] for p in nodes.values()]
        ys = [p[1] for p in nodes.values()]
        zs = [p[2] for p in nodes.values()]
        spans_xyz = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
        metrics.bbox_spans_mm = spans_xyz
        spans_sorted = sorted(spans_xyz, reverse=True)
        if len(spans_sorted) >= 2:
            metrics.bbox_area_m2 = spans_sorted[0] * spans_sorted[1] * 1.0e-6

    def sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    total_volume = 0.0
    weighted_centroid = [0.0, 0.0, 0.0]
    tets: list[tuple[float, tuple[float, float, float]]] = []
    for n1, n2, n3, n4 in elements:
        try:
            p1, p2, p3, p4 = nodes[n1], nodes[n2], nodes[n3], nodes[n4]
        except KeyError:
            continue
        volume = abs(dot(sub(p2, p1), cross(sub(p3, p1), sub(p4, p1)))) / 6.0
        if volume <= 0:
            continue
        centroid = (
            (p1[0] + p2[0] + p3[0] + p4[0]) / 4.0,
            (p1[1] + p2[1] + p3[1] + p4[1]) / 4.0,
            (p1[2] + p2[2] + p3[2] + p4[2]) / 4.0,
        )
        tets.append((volume, centroid))
        total_volume += volume
        for idx in range(3):
            weighted_centroid[idx] += volume * centroid[idx]

    if total_volume <= 0:
        return metrics

    centroid = tuple(v / total_volume for v in weighted_centroid)
    metrics.volume_mm3 = total_volume
    if density_tonne_per_mm3 is not None:
        metrics.mass_kg = total_volume * density_tonne_per_mm3 * 1000.0

    if metrics.bbox_spans_mm:
        spans = metrics.bbox_spans_mm
        axis = max(range(3), key=lambda i: spans[i])
        length = spans[axis]
        if length > 0:
            metrics.length_mm = length
            metrics.section_area_mm2 = total_volume / length

            cross_axes = [i for i in range(3) if i != axis]
            # For a prismatic member, integral(cross_coord^2 dV) / length
            # approximates the area second moment about the other cross axis.
            moments: list[tuple[float, int]] = []
            for coord_axis in cross_axes:
                moment = sum(volume * (c[coord_axis] - centroid[coord_axis]) ** 2 for volume, c in tets) / length
                moments.append((moment, coord_axis))
            moments.sort(reverse=True, key=lambda item: item[0])
            metrics.section_I_strong_mm4 = moments[0][0]
            metrics.section_I_weak_mm4 = moments[-1][0]

            half_spans = {i: spans[i] / 2.0 for i in cross_axes}
            # The c distance paired with I about one cross axis is the
            # half-span along the coordinate squared in that I integral.
            metrics.section_c_strong_mm = max(half_spans[moments[0][1]], 1.0)
            metrics.section_c_weak_mm = max(half_spans[moments[-1][1]], 1.0)

    return metrics


def von_mises(vals: list[float]) -> float:
    sxx, syy, szz, sxy, sxz, syz = vals
    return math.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy * sxy + sxz * sxz + syz * syz)
    )


def parse_dat_metrics(dat: Path) -> dict[str, Any]:
    metrics: dict[str, Any] = {
        "vm_by_step": {},
        "disp_by_step": {},
        "max_static_von_mises_MPa": None,
        "max_static_displacement_mm": None,
        "min_modal_frequency_Hz": None,
        "min_buckling_factor": None,
    }
    if not dat.exists():
        return metrics

    section: str | None = None
    cur_step: int | None = None
    static_output_done = False
    step_order = 0
    with dat.open(errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            low = line.lower()
            spaced = " ".join(line.split()).lower()

            if "e i g e n v a l u e o u t p u t" in spaced or "mode no" in low and "eigenvalue" in low:
                static_output_done = True
                section = "frequency_table"
                continue
            if "b u c k l i n g f a c t o r o u t p u t" in spaced or "mode no" in low and "buckling" in low:
                static_output_done = True
                section = "buckling_table"
                continue
            if "participation" in low:
                section = None
                continue

            if not static_output_done and low.startswith("stresses "):
                section = "stress"
                m = re.search(r"time\s+([0-9.E+\-]+)", line, re.I)
                if m:
                    try:
                        cur_step = int(round(float(m.group(1))))
                    except ValueError:
                        cur_step = None
                if cur_step is None:
                    step_order += 1
                    cur_step = step_order
                else:
                    step_order = max(step_order, cur_step)
                continue
            if not static_output_done and low.startswith("displacements "):
                section = "displacement"
                m = re.search(r"time\s+([0-9.E+\-]+)", line, re.I)
                if m:
                    try:
                        cur_step = int(round(float(m.group(1))))
                    except ValueError:
                        cur_step = None
                if cur_step is None:
                    step_order += 1
                    cur_step = step_order
                else:
                    step_order = max(step_order, cur_step)
                continue

            if not line or line.startswith("MODE") or line.startswith("FACTOR"):
                continue

            parts = line.split()
            if section == "stress" and cur_step is not None and len(parts) >= 8:
                try:
                    comps = [float(x) for x in parts[2:8]]
                except ValueError:
                    continue
                vm = von_mises(comps)
                current = metrics["vm_by_step"].get(cur_step)
                metrics["vm_by_step"][cur_step] = vm if current is None else max(current, vm)
                metrics["max_static_von_mises_MPa"] = max(metrics["max_static_von_mises_MPa"] or 0.0, vm)
            elif section == "displacement" and cur_step is not None and len(parts) >= 4:
                try:
                    comps = [float(x) for x in parts[1:4]]
                except ValueError:
                    continue
                disp = math.sqrt(sum(x * x for x in comps))
                current = metrics["disp_by_step"].get(cur_step)
                metrics["disp_by_step"][cur_step] = disp if current is None else max(current, disp)
                metrics["max_static_displacement_mm"] = max(metrics["max_static_displacement_mm"] or 0.0, disp)
            elif section == "frequency_table" and len(parts) >= 4:
                try:
                    int(parts[0])
                    freq = float(parts[3])
                except ValueError:
                    continue
                if freq > 1.0e-9:
                    current = metrics["min_modal_frequency_Hz"]
                    metrics["min_modal_frequency_Hz"] = freq if current is None else min(current, freq)
            elif section == "buckling_table" and len(parts) >= 2:
                try:
                    int(parts[0])
                    factor = float(parts[1])
                except ValueError:
                    continue
                current = metrics["min_buckling_factor"]
                metrics["min_buckling_factor"] = factor if current is None else min(current, factor)

    return metrics


def first_numeric(value: Any, *keys: str) -> float | None:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                out = as_float(value[key])
                if out is not None:
                    return out
    return None


def aisc_metric(metric: str, spec: dict[str, Any], mesh: MeshMetrics) -> tuple[str, float] | tuple[None, str]:
    if not mesh.section_area_mm2 or not mesh.section_I_strong_mm4 or not mesh.section_I_weak_mm4:
        return None, "missing mesh-derived prismatic section properties"
    prompt = spec.get("prompt") if isinstance(spec.get("prompt"), dict) else {}
    material = prompt.get("material") if isinstance(prompt.get("material"), dict) else {}
    geom = prompt.get("geometric_constraints") if isinstance(prompt.get("geometric_constraints"), dict) else {}
    load_cases = prompt.get("load_cases") if isinstance(prompt.get("load_cases"), list) else []

    fy = first_numeric(material, "Fy_MPa", "yield_MPa") or 345.0
    e_mod = (first_numeric(material, "E_MPa") or (first_numeric(material, "E_GPa") or 200.0) * 1000.0)
    phi_c = 0.90
    phi_b = 0.90
    area = mesh.section_area_mm2
    ix = mesh.section_I_strong_mm4
    iy = mesh.section_I_weak_mm4
    rx = math.sqrt(max(ix / area, 1.0e-9))
    ry = math.sqrt(max(iy / area, 1.0e-9))

    height_mm = (first_numeric(geom, "clear_height_m") or ((mesh.length_mm or 0.0) / 1000.0)) * 1000.0
    kx = first_numeric(geom, "strong_axis_K") or 1.0
    ky = first_numeric(geom, "weak_axis_K") or 1.0
    lb_mm = (first_numeric(geom, "weak_axis_Lb_m") or (height_mm / 1000.0)) * 1000.0

    def fcr(kl_over_r: float) -> float:
        fe = math.pi * math.pi * e_mod / max(kl_over_r * kl_over_r, 1.0e-9)
        if fe >= 0.44 * fy:
            return (0.658 ** (fy / fe)) * fy
        return 0.877 * fe

    fcr_x = fcr(kx * height_mm / max(rx, 1.0e-9))
    fcr_y = fcr(ky * lb_mm / max(ry, 1.0e-9))
    phi_pn_kN = phi_c * min(fcr_x, fcr_y) * area / 1000.0
    sx = ix / max(mesh.section_c_strong_mm or 1.0, 1.0)
    sy = iy / max(mesh.section_c_weak_mm or 1.0, 1.0)
    phi_mx_kNm = phi_b * fy * sx / 1.0e6
    phi_my_kNm = phi_b * fy * sy / 1.0e6

    if "phi_c_pn" in metric or "compression" in metric and "capacity" in metric:
        return "mesh:AISC_phi_c_Pn_kN", phi_pn_kN
    if "mnx" in metric or "strong_axis" in metric:
        return "mesh:AISC_phi_b_Mnx_kNm", phi_mx_kNm
    if "mny" in metric or "weak_axis" in metric:
        return "mesh:AISC_phi_b_Mny_kNm", phi_my_kNm
    if "h1" in metric or "unity" in metric:
        worst = 0.0
        for lc in load_cases:
            if not isinstance(lc, dict):
                continue
            pu = as_float(lc.get("Pu_kN")) or 0.0
            mux = as_float(lc.get("Mux_kNm")) or 0.0
            muy = as_float(lc.get("Muy_kNm")) or 0.0
            if pu < 0:
                phi_tpn = phi_c * fy * area / 1000.0
                ratio = abs(pu) / max(phi_tpn, 1.0e-9) + mux / max(phi_mx_kNm, 1.0e-9) + muy / max(phi_my_kNm, 1.0e-9)
            else:
                p_ratio = pu / max(phi_pn_kN, 1.0e-9)
                flex = mux / max(phi_mx_kNm, 1.0e-9) + muy / max(phi_my_kNm, 1.0e-9)
                ratio = p_ratio + (8.0 / 9.0) * flex if p_ratio >= 0.2 else p_ratio / 2.0 + flex
            worst = max(worst, ratio)
        return "mesh:AISC_H1_unity", worst
    if "deflection" in metric:
        wind_lc = next((lc for lc in load_cases if isinstance(lc, dict) and "wind" in str(lc.get("name", "")).lower()), None)
        mux = as_float((wind_lc or {}).get("Mux_kNm")) or 0.0
        delta = (mux * 1.0e6) * height_mm * height_mm / (2.0 * e_mod * max(ix, 1.0e-9))
        return "mesh:AISC_service_deflection_mm", delta
    if "weight_per_meter" in metric or "kg_m" in metric:
        if mesh.mass_kg is not None and mesh.length_mm:
            return "mesh:section_weight_kg_m", mesh.mass_kg / (mesh.length_mm / 1000.0)
    return None, "unsupported AISC metric"


def metric_value(
    req: dict[str, Any],
    rid: str,
    *,
    spec: dict[str, Any],
    meta_metrics: dict[str, float],
    dat_metrics: dict[str, Any],
    mesh: MeshMetrics,
    limit_key: str,
) -> tuple[str, float] | tuple[None, str]:
    submitted = submitted_metric(meta_metrics, req, rid)
    if submitted is not None:
        return submitted

    metric = norm_key(str(req.get("metric") or ""))
    text = norm_key(requirement_text(req))
    joined = f"{metric} {text}"

    if any(term in joined for term in ("phi_c", "phi_b", "aisc", "h1_unity", "service_wind", "section_weight_per_meter")):
        return aisc_metric(joined, spec, mesh)

    if "mass" in joined or "weight" in joined or limit_key in {"limit_kg", "limit_kg_m"}:
        if mesh.mass_kg is None:
            return None, "missing mesh-derived mass; provide density in spec/meta or valid mesh"
        if limit_key == "limit_g" or "_g" in metric:
            return "mesh:mass_g", mesh.mass_kg * 1000.0
        if limit_key == "limit_kg_m":
            if mesh.length_mm:
                return "mesh:mass_per_length_kg_m", mesh.mass_kg / (mesh.length_mm / 1000.0)
            return None, "missing mesh length for mass per meter"
        return "mesh:mass_kg", mesh.mass_kg

    if "areal_density" in joined:
        if mesh.mass_kg is not None and mesh.bbox_area_m2:
            return "mesh:areal_density_kg_m2", mesh.mass_kg / mesh.bbox_area_m2
        return None, "missing mesh mass or planform area"

    if "buckl" in joined or "load_factor" in joined or "eigenvalue" in joined:
        val = dat_metrics.get("min_buckling_factor")
        if val is not None:
            return "model.dat:min_buckling_factor", float(val)
        return None, "missing buckling factor output"

    if "frequency" in joined or "natural_frequency" in joined or "mode_hz" in joined:
        val = dat_metrics.get("min_modal_frequency_Hz")
        if val is not None:
            return "model.dat:min_modal_frequency_Hz", float(val)
        return None, "missing modal frequency output"

    if any(term in joined for term in ("stress", "von_mises", "yield", "tensile", "principal", "goodman")):
        applies = req.get("applies_to") if isinstance(req.get("applies_to"), list) else []
        step_values: list[float] = []
        for item in applies:
            match = re.search(r"LC\s*([0-9]+)|LC([0-9]+)", str(item), re.I)
            if match:
                step = int(next(g for g in match.groups() if g))
                val = dat_metrics.get("vm_by_step", {}).get(step)
                if val is not None:
                    step_values.append(float(val))
        if step_values:
            return "model.dat:max_von_mises_selected_steps_MPa", max(step_values)
        val = dat_metrics.get("max_static_von_mises_MPa")
        if val is not None:
            return "model.dat:max_static_von_mises_MPa", float(val)
        return None, "missing static stress output"

    if any(term in joined for term in ("deflection", "displacement", "intrusion", "drift", "sway", "deformation", "growth")):
        val = dat_metrics.get("max_static_displacement_mm")
        if val is not None:
            return "model.dat:max_static_displacement_mm", float(val)
        return None, "missing static displacement output"

    if "wheelbase" in joined:
        if mesh.bbox_spans_mm:
            return "mesh:largest_bbox_span_mm", max(mesh.bbox_spans_mm)
        return None, "missing mesh bbox for wheelbase"

    if "perimeter" in joined and mesh.bbox_spans_mm:
        a, b = sorted(mesh.bbox_spans_mm, reverse=True)[:2]
        return "mesh:bbox_planform_perimeter_mm", 2.0 * (a + b)

    if "height" in joined and mesh.bbox_spans_mm:
        return "mesh:largest_bbox_span_mm", max(mesh.bbox_spans_mm)

    if "tilt" in joined:
        if mesh.bbox_spans_mm:
            spans = sorted(mesh.bbox_spans_mm, reverse=True)
            track = spans[1] if len(spans) > 1 else spans[0]
            height = spans[2] if len(spans) > 2 else spans[0]
            theta = math.degrees(math.atan((track / 2.0) / max(height / 2.0, 1.0e-9)))
            return "mesh:bbox_tipover_angle_deg", theta
        return None, "missing mesh bbox for tilt estimate"

    return None, "unsupported/missing submitted engineering metric"


def main(workdir: str | Path) -> int:
    work = Path(workdir).resolve()
    spec = load_json(work / "spec.json")
    meta = load_json(work / "meta.json")
    meta_metrics = flatten_metrics(meta)
    density = parse_density_tonne_per_mm3(meta, spec) or parse_density_from_inp(work / "model.inp")
    mesh_path = work / "mesh.inp"
    if not mesh_path.exists():
        mesh_path = work / "model.inp"
    mesh = parse_mesh_metrics(mesh_path, density)
    dat_metrics = parse_dat_metrics(work / "model.dat")

    rows: list[tuple[str, str, str]] = []
    for index, req in enumerate(source_requirements(spec)):
        rid = requirement_id(req, index)
        lim = limit_value(req)
        if lim is None:
            rows.append((rid, "FAIL", "missing numeric declared limit"))
            continue
        limit_key, limit = lim
        op = operator_for(req, limit_key)

        # Range requirements such as lower/upper ID are handled as two-sided
        # checks when both bounds exist; otherwise they fall back to the single
        # limit/operator path.
        metric_name, value_or_reason = metric_value(
            req,
            rid,
            spec=spec,
            meta_metrics=meta_metrics,
            dat_metrics=dat_metrics,
            mesh=mesh,
            limit_key=limit_key,
        )
        if metric_name is None:
            rows.append((rid, "FAIL", str(value_or_reason)))
            continue
        value = float(value_or_reason)
        ok = compare(value, op, limit)
        lower = as_float(req.get("lower_limit_mm"))
        upper = as_float(req.get("upper_limit_mm"))
        if lower is not None and upper is not None:
            ok = lower <= value <= upper
            op_note = f"{lower:.6g} <= value <= {upper:.6g}"
        else:
            op_note = f"{op} {limit:.6g}"
        rows.append((
            rid,
            "PASS" if ok else "FAIL",
            f"metric={metric_name} value={value:.6g} requirement={op_note} source=dynamic_single_engineering_checker",
        ))

    for rid, verdict, source in rows:
        print(f"[{verdict}] {rid} class=ENGINEERING source={source}")

    n_pass = sum(1 for _, verdict, _ in rows if verdict == "PASS")
    n_fail = sum(1 for _, verdict, _ in rows if verdict == "FAIL")
    print(f"SUMMARY PASS={n_pass} FAIL={n_fail} SKIP=0 TOTAL={len(rows)}")
    return 0 if n_fail == 0 else 2


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: single_engineering_check.py <workdir>", file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
