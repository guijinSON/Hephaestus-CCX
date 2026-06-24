#!/usr/bin/env python3
"""Generic engineering-value checker for multipart CCX workdirs.

This is intentionally stricter than the generated multipart coverage checker:
it compares parsed solver/mesh values against declared requirement limits where
the generic CCX deck exposes enough information. Requirements that need
case-specific load binding, material-region selectors, contact interfaces, weld
DCR formulas, fatigue/random dynamics, or composite laminate post-processing are
reported as FAIL rather than silently counted as passes.

Usage:
    multi_engineering_check.py <workdir>
"""

from __future__ import annotations

import json
import math
import re
import sys
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


def limit_value(req: dict[str, Any]) -> tuple[str, float] | None:
    for key in (
        "limit",
        "threshold",
        "limit_MPa",
        "limit_mm",
        "limit_kg",
        "limit_Hz",
        "limit_N",
        "limit_kN",
        "limit_kg_m2",
        "allowable",
        "minimum",
        "maximum",
        "min",
        "max",
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
    if limit_key in {"minimum", "min"}:
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
        return math.isclose(value, limit, rel_tol=1e-6, abs_tol=1e-9)
    return False


def von_mises(vals: list[float]) -> float:
    sxx, syy, szz, sxy, sxz, syz = vals
    return math.sqrt(
        0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
        + 3.0 * (sxy * sxy + sxz * sxz + syz * syz)
    )


def parse_dat_metrics(dat: Path) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {
        "max_static_von_mises_MPa": None,
        "max_static_shear_MPa": None,
        "max_static_displacement_mm": None,
        "min_modal_frequency_Hz": None,
        "min_buckling_factor": None,
    }
    if not dat.exists():
        return metrics

    section: str | None = None
    static_output_done = False
    with dat.open(errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            low = line.lower()
            spaced = " ".join(line.split())

            if "e i g e n v a l u e o u t p u t" in spaced.lower():
                static_output_done = True
                section = "frequency_table"
                continue
            if "b u c k l i n g f a c t o r o u t p u t" in spaced.lower():
                static_output_done = True
                section = "buckling_table"
                continue
            if "p a r t i c i p a t i o n" in spaced.lower():
                section = None
                continue
            if "e f f e c t i v e" in spaced.lower():
                section = None
                continue
            if "e i g e n v a l u e n u m b e r" in spaced.lower():
                section = None
                continue

            if not static_output_done and low.startswith("stresses "):
                section = "stress"
                continue
            if not static_output_done and low.startswith("displacements "):
                section = "displacement"
                continue

            if not line or line.startswith("MODE") or line.startswith("FACTOR"):
                continue

            parts = line.split()
            if section == "stress" and len(parts) >= 8:
                try:
                    comps = [float(x) for x in parts[2:8]]
                except ValueError:
                    continue
                vm = von_mises(comps)
                shear = max(abs(comps[3]), abs(comps[4]), abs(comps[5]))
                metrics["max_static_von_mises_MPa"] = max(
                    metrics["max_static_von_mises_MPa"] or 0.0,
                    vm,
                )
                metrics["max_static_shear_MPa"] = max(
                    metrics["max_static_shear_MPa"] or 0.0,
                    shear,
                )
            elif section == "displacement" and len(parts) >= 4:
                try:
                    comps = [float(x) for x in parts[1:4]]
                except ValueError:
                    continue
                disp = math.sqrt(sum(x * x for x in comps))
                metrics["max_static_displacement_mm"] = max(
                    metrics["max_static_displacement_mm"] or 0.0,
                    disp,
                )
            elif section == "frequency_table" and len(parts) >= 4:
                try:
                    int(parts[0])
                    freq = float(parts[3])
                except ValueError:
                    continue
                metrics["min_modal_frequency_Hz"] = (
                    freq
                    if metrics["min_modal_frequency_Hz"] is None
                    else min(metrics["min_modal_frequency_Hz"] or freq, freq)
                )
            elif section == "buckling_table" and len(parts) >= 2:
                try:
                    int(parts[0])
                    factor = float(parts[1])
                except ValueError:
                    continue
                metrics["min_buckling_factor"] = (
                    factor
                    if metrics["min_buckling_factor"] is None
                    else min(metrics["min_buckling_factor"] or factor, factor)
                )

    return metrics


def parse_density_tonne_per_mm3(meta: dict[str, Any]) -> float | None:
    props = meta.get("material_properties")
    if isinstance(props, dict):
        val = as_float(props.get("density_tonne_per_mm3"))
        if val is not None:
            return val
    material = str(meta.get("material") or "")
    match = re.search(r"\*DENSITY\s+([0-9.Ee+-]+)", material.replace("\n", " "))
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def parse_mesh_metrics(mesh: Path, density_tonne_per_mm3: float | None) -> dict[str, float | None]:
    metrics: dict[str, float | None] = {
        "volume_mm3": None,
        "mass_kg": None,
        "bbox_area_m2": None,
    }
    if not mesh.exists():
        return metrics

    nodes: dict[int, tuple[float, float, float]] = {}
    elements: list[tuple[int, int, int, int]] = []
    section: str | None = None
    c3d4 = False
    with mesh.open(errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("*"):
                upper = line.upper()
                if upper.startswith("*NODE"):
                    section = "node"
                elif upper.startswith("*ELEMENT"):
                    section = "element"
                    c3d4 = "TYPE=C3D4" in upper
                else:
                    section = None
                    c3d4 = False
                continue
            parts = [p.strip() for p in line.split(",")]
            if section == "node" and len(parts) >= 4:
                try:
                    nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    continue
            elif section == "element" and c3d4 and len(parts) >= 5:
                try:
                    elements.append(tuple(int(p) for p in parts[1:5]))
                except ValueError:
                    continue

    if nodes:
        xs = [p[0] for p in nodes.values()]
        ys = [p[1] for p in nodes.values()]
        zs = [p[2] for p in nodes.values()]
        spans = sorted([max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)], reverse=True)
        metrics["bbox_area_m2"] = (spans[0] * spans[1]) * 1.0e-6 if len(spans) >= 2 else None

    def sub(a, b):
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def cross(a, b):
        return (
            a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0],
        )

    def dot(a, b):
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    volume = 0.0
    for n1, n2, n3, n4 in elements:
        try:
            p1, p2, p3, p4 = nodes[n1], nodes[n2], nodes[n3], nodes[n4]
        except KeyError:
            continue
        volume += abs(dot(sub(p2, p1), cross(sub(p3, p1), sub(p4, p1)))) / 6.0
    if volume > 0:
        metrics["volume_mm3"] = volume
        if density_tonne_per_mm3 is not None:
            metrics["mass_kg"] = volume * density_tonne_per_mm3 * 1000.0
    return metrics


def find_planform_area_m2(spec: dict[str, Any]) -> float | None:
    def walk(value: Any) -> float | None:
        if isinstance(value, dict):
            if "planform_mm" in value and isinstance(value["planform_mm"], dict):
                pf = value["planform_mm"]
                x = as_float(pf.get("x"))
                y = as_float(pf.get("y"))
                if x and y:
                    return x * y * 1.0e-6
            for item in value.values():
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


def metric_value(
    req: dict[str, Any],
    dat_metrics: dict[str, float | None],
    mesh_metrics: dict[str, float | None],
    area_m2: float | None,
) -> tuple[str, float] | tuple[None, str]:
    text = requirement_text(req)
    metric = str(req.get("metric") or "").lower()

    if any(term in text for term in ("fatigue", "miner", "random", "psd", "srs", "shock")):
        return None, "unsupported: fatigue/random/shock requirement needs case-specific dynamic processing"
    if any(term in text for term in ("tsai", "composite", "ply", "first-ply", "failure_index")):
        return None, "unsupported: composite/laminate failure needs material axes and ply data"
    if any(term in text for term in ("contact pressure", "gap", "contact_pressure", "dcr", "weld", "joint", "slip", "preload", "bolt interface")):
        if "stress" not in metric and "tensile" not in metric:
            return None, "unsupported: contact/connection/DCR requirement needs interface selectors and formulas"

    if "areal_density" in metric:
        mass = mesh_metrics.get("mass_kg")
        area = area_m2 or mesh_metrics.get("bbox_area_m2")
        if mass is not None and area:
            return "areal_density_kg_m2", mass / area
        return None, "missing mesh mass or planform area for areal density"
    if any(term in metric for term in ("mass", "weight", "dry_mass")):
        mass = mesh_metrics.get("mass_kg")
        if mass is not None:
            return "mass_kg", mass
        return None, "missing mesh-derived mass"
    if any(term in text for term in ("buckl", "load_factor", "eigenvalue")):
        val = dat_metrics.get("min_buckling_factor")
        if val is not None:
            return "min_buckling_factor", val
        return None, "missing buckling factor output"
    if any(term in text for term in ("frequency", "natural_frequency", "modal", "mode_hz")):
        val = dat_metrics.get("min_modal_frequency_Hz")
        if val is not None:
            return "min_modal_frequency_Hz", val
        return None, "missing modal frequency output"
    if any(term in text for term in ("shear",)):
        val = dat_metrics.get("max_static_shear_MPa")
        if val is not None:
            return "max_static_shear_MPa", val
        return None, "missing static shear stress output"
    if any(term in text for term in ("deflection", "displacement", "intrusion", "drift", "sway", "out-of-roundness", "ovalization", "growth")):
        val = dat_metrics.get("max_static_displacement_mm")
        if val is not None:
            return "max_static_displacement_mm", val
        return None, "missing static displacement output"
    if any(term in text for term in ("stress", "von_mises", "yield", "tensile", "principal")):
        val = dat_metrics.get("max_static_von_mises_MPa")
        if val is not None:
            return "max_static_von_mises_MPa", val
        return None, "missing static stress output"

    return None, "unsupported: requirement metric is not mapped to a generic engineering value"


def main(workdir: str) -> int:
    work = Path(workdir).resolve()
    spec_path = work / "spec.json"
    if not spec_path.exists():
        print("[FAIL] SPEC class=ENGINEERING source=spec.json missing")
        print("SUMMARY PASS=0 FAIL=1 SKIP=0 TOTAL=1")
        return 2

    spec = json.loads(spec_path.read_text())
    meta = json.loads((work / "meta.json").read_text()) if (work / "meta.json").exists() else {}
    dat_metrics = parse_dat_metrics(work / "model.dat")
    density = parse_density_tonne_per_mm3(meta)
    mesh_metrics = parse_mesh_metrics(work / "mesh.inp", density)
    area_m2 = find_planform_area_m2(spec)

    rows: list[tuple[str, str, str]] = []
    for index, req in enumerate(source_requirements(spec)):
        rid = requirement_id(req, index)
        lim = limit_value(req)
        if lim is None:
            rows.append((rid, "FAIL", "missing numeric declared limit"))
            continue
        limit_key, limit = lim
        op = operator_for(req, limit_key)
        metric_name, value_or_reason = metric_value(req, dat_metrics, mesh_metrics, area_m2)
        if metric_name is None:
            rows.append((rid, "FAIL", str(value_or_reason)))
            continue
        value = float(value_or_reason)
        ok = compare(value, op, limit)
        rows.append((
            rid,
            "PASS" if ok else "FAIL",
            f"metric={metric_name} value={value:.6g} requirement={op} {limit:.6g} source=generic_multi_engineering_checker",
        ))

    for rid, verdict, source in rows:
        print(f"[{verdict}] {rid} class=ENGINEERING source={source}")

    n_pass = sum(1 for _, verdict, _ in rows if verdict == "PASS")
    n_fail = sum(1 for _, verdict, _ in rows if verdict == "FAIL")
    print(f"SUMMARY PASS={n_pass} FAIL={n_fail} SKIP=0 TOTAL={len(rows)}")
    return 0 if n_fail == 0 else 2


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
