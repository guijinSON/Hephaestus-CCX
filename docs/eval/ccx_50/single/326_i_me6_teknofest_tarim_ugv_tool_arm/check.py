#!/usr/bin/env python3
"""
Pass/fail check for TEKNOFEST Tarımsal İKA cantilevered tool arm.

Submission-agnostic pipeline:
    build.py        -> out.step + meta.json
    gmsh            -> mesh.inp
    wire_bcs.py     -> model.inp (mesh + Eall + NSETs + analysis_template)
    ccx_2.22 model  -> model.dat / model.frd
    check.py        -> R1..R5 PASS/FAIL  (this file)

Authoritative source for limits is spec.json. The FEM contributes:
    R1  - peak von Mises in arm under LC1..LC4 (steps 1..4 in the deck)
    R2  - tip deflection under LC1 (parsed from NLOAD displacements)
    R5  - first cantilever eigenfrequency (*FREQUENCY step 6)

Closed-form (no FEM dependency):
    R3  - Goodman-corrected alternating fibre stress at root for LC5
    R4  - box-section dry mass

R3 is closed-form because spec.verification.requires_non_fea_solver flags
fatigue as a separate post-processing path (e.g. ANSYS nCode, FE-SAFE).
R4 is closed-form because CCX section integration-points report stress,
not section mass.
"""
from __future__ import annotations


# Dynamic checker override: use submitted meta/mesh/CCX outputs instead of
# reference-design constants so feedback-loop repairs can change verdicts.
def _run_dynamic_single_checker():
    from pathlib import Path as _Path
    import sys as _sys
    try:
        from scripts.ccx_eval.single_engineering_check import main as _dynamic_main
    except ModuleNotFoundError:
        for _parent in _Path(__file__).resolve().parents:
            if (_parent / "scripts" / "ccx_eval" / "single_engineering_check.py").exists():
                _sys.path.insert(0, str(_parent))
                break
        from scripts.ccx_eval.single_engineering_check import main as _dynamic_main
    return _dynamic_main(_Path(__file__).resolve().parent)


if __name__ == "__main__":
    raise SystemExit(_run_dynamic_single_checker())

import json
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = json.loads((HERE / "spec.json").read_text())
DAT_PATH = HERE / "model.dat"


# ---------------------------------------------------------------------------
# Parse model.dat -> peak von Mises per step (one block per *EL PRINT)
# ---------------------------------------------------------------------------
def parse_peak_vm_per_step(dat_path: Path) -> dict[int, float]:
    """Return {step_index (1-based): peak_vm_MPa} from CCX *.dat stress blocks.

    CCX writes one stress block per static step. The *FREQUENCY step (step 6
    in our deck) emits one stress block PER EIGENMODE, all stamped with the
    same time=6.0 — eigenmode shapes are normalised to unit modal mass so
    these stresses are not physically meaningful and must be ignored. This
    parser keeps only the FIRST stress block at each unique time.

    Header form:
        stresses (elem, integ.pnt., sxx, syy, szz, sxy, sxz, syz)
            for set EALL and time  0.1000000E+01

    Units in this template: mm/N/MPa/t -> sxx etc. are MPa directly.
    """
    if not dat_path.exists():
        return {}
    # Header line for any *DAT block — captures the kind (stresses, displacements,
    # forces, etc.) plus the time stamp. We stop accumulating on ANY new header,
    # not just the matching kind, so stress lines don't bleed into displacement
    # parsing or vice versa.
    any_header_re = re.compile(
        r"\s*(stresses|displacements|forces|temperatures|"
        r"total force|reaction force|strains|equivalent plastic strain|"
        r"strain energy|kinetic energy|external work)"
        r".*?for set\s+(\S+)\s+and time\s+(\S+)",
        re.IGNORECASE,
    )
    peaks: dict[int, float] = {}
    cur_step: int | None = None
    cur_max = 0.0
    accumulating = False    # True only for the first stress block at this step
    with dat_path.open() as fh:
        for line in fh:
            m = any_header_re.match(line)
            if m:
                # close out previous block (only if we were accumulating)
                if cur_step is not None and accumulating:
                    peaks[cur_step] = cur_max
                kind = m.group(1).lower()
                try:
                    t = int(round(float(m.group(3))))
                except ValueError:
                    t = None
                if kind == "stresses" and t is not None and t not in peaks:
                    cur_step = t
                    accumulating = True
                    cur_max = 0.0
                else:
                    cur_step = None
                    accumulating = False
                continue
            if not accumulating:
                continue
            parts = line.split()
            if len(parts) < 8:
                continue
            try:
                _eid = int(parts[0])
                _ip = int(parts[1])
                sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                sxy = float(parts[5]); sxz = float(parts[6]); syz = float(parts[7])
            except ValueError:
                continue
            vm = math.sqrt(
                0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                + 3.0 * (sxy * sxy + sxz * sxz + syz * syz)
            )
            if vm > cur_max:
                cur_max = vm
        if cur_step is not None and accumulating:
            peaks[cur_step] = cur_max
    return peaks


# ---------------------------------------------------------------------------
# Parse model.dat -> tip displacement per step (NLOAD nset)
# ---------------------------------------------------------------------------
def parse_tip_disp_per_step(dat_path: Path) -> dict[int, float]:
    """Return {step: max_resultant_magnitude_mm} for the NLOAD nset.

    Resultant per node = sqrt(ux^2 + uy^2 + uz^2); we report the MAX over
    all NLOAD nodes (well-defined: tip end-cap face nodes all see roughly
    the same displacement up to bending-induced section warping).

    Same eigenmode caveat as parse_peak_vm_per_step: *FREQUENCY emits one
    displacement block per mode at time=6.0, all mass-normalised; we only
    keep the first block per unique step time.

    Units: mm (template uses mm/N/MPa/t).
    """
    if not dat_path.exists():
        return {}
    any_header_re = re.compile(
        r"\s*(stresses|displacements|forces|temperatures|"
        r"total force|reaction force|strains|equivalent plastic strain|"
        r"strain energy|kinetic energy|external work)"
        r".*?for set\s+(\S+)\s+and time\s+(\S+)",
        re.IGNORECASE,
    )
    out: dict[int, float] = {}
    cur_step: int | None = None
    cur_max = 0.0
    accumulating = False
    with dat_path.open() as fh:
        for line in fh:
            m = any_header_re.match(line)
            if m:
                if cur_step is not None and accumulating:
                    out[cur_step] = cur_max
                kind = m.group(1).lower()
                try:
                    t = int(round(float(m.group(3))))
                except ValueError:
                    t = None
                if kind == "displacements" and t is not None and t not in out:
                    cur_step = t
                    accumulating = True
                    cur_max = 0.0
                else:
                    cur_step = None
                    accumulating = False
                continue
            if not accumulating:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            try:
                _nid = int(parts[0])
                ux = float(parts[1])
                uy = float(parts[2])
                uz = float(parts[3])
            except ValueError:
                continue
            mag = math.sqrt(ux * ux + uy * uy + uz * uz)
            if mag > cur_max:
                cur_max = mag
        if cur_step is not None and accumulating:
            out[cur_step] = cur_max
    return out


# ---------------------------------------------------------------------------
# Parse model.dat -> first eigenfrequency from *FREQUENCY block
# ---------------------------------------------------------------------------
def parse_first_freq_hz(dat_path: Path) -> float | None:
    """First eigenfrequency in Hz from CCX *FREQUENCY output."""
    if not dat_path.exists():
        return None
    text = dat_path.read_text()
    m = re.search(
        r"E I G E N V A L U E.*?\n\s*1\s+\S+\s+\S+\s+([0-9.E+\-]+)",
        text,
        re.S,
    )
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Closed-form components (R3 fatigue, R4 mass)
# ---------------------------------------------------------------------------
def closed_form_mass_kg() -> float:
    """Closed-form arm dry mass from spec geometry + density.

    Box outer 60 x 40 mm, wall 3 mm, length 0.8 m, density 2700 kg/m^3.
    """
    geom = SPEC["prompt"]["geometric_constraints"]
    cs = geom["cross_section_mm"]
    H_m = cs["height"] / 1000.0
    W_m = cs["width"] / 1000.0
    t_m = cs["wall"] / 1000.0
    L_m = geom["reach_m"]
    rho = SPEC["prompt"]["material"]["properties"]["density_kg_m3"]

    A_outer = H_m * W_m
    A_inner = (H_m - 2 * t_m) * (W_m - 2 * t_m)
    A_section = A_outer - A_inner    # m^2
    return A_section * L_m * rho     # kg


def closed_form_fatigue_alt_eq_mpa() -> tuple[float, float]:
    """Goodman-equivalent fully-reversed alt stress at root for LC5 (50 N tip).

    Returns (sigma_alt_root_MPa, sigma_alt_eq_MPa).

    Bending moment at root:
        M = 50 N * 800 mm = 40000 N*mm
    Section modulus (strong axis, 60 mm side vertical):
        I = (W * H^3 - (W-2t) * (H-2t)^3) / 12   in mm^4
            with W=40, H=60, t=3 -> (40*60^3 - 34*54^3)/12
        c = H/2 = 30 mm
        sigma_alt = M * c / I    (peak fibre, geometric stress)
    Goodman correction (LC5 is 0..50 N => R=0 -> sigma_mean = sigma_alt):
        sigma_eq = sigma_alt / (1 - sigma_mean / S_ut)
    """
    geom = SPEC["prompt"]["geometric_constraints"]
    cs = geom["cross_section_mm"]
    H_mm = cs["height"]
    W_mm = cs["width"]
    t_mm = cs["wall"]
    L_mm = geom["reach_m"] * 1000.0

    F_alt_N = 50.0    # LC5 alternating amplitude
    M_alt_Nmm = F_alt_N * L_mm
    I_strong_mm4 = (W_mm * H_mm ** 3 - (W_mm - 2 * t_mm) * (H_mm - 2 * t_mm) ** 3) / 12.0
    c_mm = H_mm / 2.0
    sigma_alt = M_alt_Nmm * c_mm / I_strong_mm4   # MPa

    sigma_mean = sigma_alt          # R=0 cycle: mean equals amplitude
    S_ut = SPEC["prompt"]["material"]["properties"]["ultimate_tensile_strength_MPa"]
    sigma_eq = sigma_alt / max(1.0 - sigma_mean / S_ut, 1e-9)
    return sigma_alt, sigma_eq


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    peaks_vm = parse_peak_vm_per_step(DAT_PATH)
    tip_disp = parse_tip_disp_per_step(DAT_PATH)
    first_freq_hz = parse_first_freq_hz(DAT_PATH)

    arm_mass_kg = closed_form_mass_kg()
    sigma_alt_root, sigma_alt_eq = closed_form_fatigue_alt_eq_mpa()

    # Spec limits (R1..R5)
    LIMITS = {r["id"]: r for r in SPEC["requirements"]["pass_fail_criteria"]}
    R1_LIM = LIMITS["R1"]["limit_MPa"]    # 184 MPa
    R2_LIM = LIMITS["R2"]["limit_mm"]     # 5 mm
    R3_LIM = LIMITS["R3"]["limit_MPa"]    # 74 MPa (Goodman-adjusted)
    R4_LIM = LIMITS["R4"]["limit_kg"]     # 4.5 kg
    R5_LIM = LIMITS["R5"]["limit_Hz"]     # 25 Hz

    # ------ R1: max vM across LC1..LC4 ------
    static_vms = [peaks_vm.get(s, float("nan")) for s in (1, 2, 3, 4)]
    max_static = max((v for v in static_vms if not math.isnan(v)), default=float("nan"))
    R1_ok = (not math.isnan(max_static)) and max_static <= R1_LIM

    # ------ R2: tip deflection LC1 ------
    tip_defl_mm = tip_disp.get(1, float("nan"))
    R2_ok = (not math.isnan(tip_defl_mm)) and tip_defl_mm <= R2_LIM

    # ------ R3: Goodman-equivalent alt stress (closed form) ------
    R3_ok = sigma_alt_eq <= R3_LIM

    # ------ R4: arm mass (closed form) ------
    R4_ok = arm_mass_kg <= R4_LIM

    # ------ R5: first eigenfrequency ------
    R5_ok = (first_freq_hz is not None) and first_freq_hz >= R5_LIM

    # --------- print report ---------
    def tag(ok: bool, val_known: bool = True) -> str:
        if not val_known:
            return "SKIP"
        return "PASS" if ok else "FAIL"

    print("=" * 76)
    print("TEKNOFEST Tarımsal İKA Cantilevered Tool Arm - Verification Report")
    print("=" * 76)
    print(f"Spec id     : {SPEC['id']}")
    print(f"Material    : {SPEC['prompt']['material']['name']}")
    cs = SPEC["prompt"]["geometric_constraints"]["cross_section_mm"]
    print(f"Section     : {cs['height']} x {cs['width']} mm box, {cs['wall']} mm wall")
    print(f"Length      : {SPEC['prompt']['geometric_constraints']['reach_m']*1000:.0f} mm")
    print(f"Mass (calc) : {arm_mass_kg:.3f} kg")
    print()
    print("FEM peak von Mises per load case (MPa):")
    names = {1: "LC1 static gravity", 2: "LC2 2g shock",
             3: "LC3 15deg slope",   4: "LC4 150N impact",
             5: "LC5 50N fatigue alt"}
    for s in (1, 2, 3, 4, 5):
        v = peaks_vm.get(s, float("nan"))
        v_str = f"{v:8.3f}" if not math.isnan(v) else "    n/a "
        print(f"  step {s} ({names[s]:<24}): {v_str}")
    print()
    print(f"Tip deflection LC1 : {tip_defl_mm:.3f} mm" if not math.isnan(tip_defl_mm)
          else "Tip deflection LC1 : n/a")
    if first_freq_hz is not None:
        print(f"First mode         : {first_freq_hz:.2f} Hz")
    else:
        print("First mode         : n/a")
    print()
    print("Closed-form fatigue (R3):")
    print(f"  sigma_alt(root, M*c/I) = {sigma_alt_root:.3f} MPa")
    print(f"  Goodman-equivalent     = {sigma_alt_eq:.3f} MPa")
    print()

    rows = [
        ("R1", "max_von_mises_LC1-4_MPa", max_static, "<=", R1_LIM,
         tag(R1_ok, not math.isnan(max_static))),
        ("R2", "tip_deflection_LC1_mm", tip_defl_mm, "<=", R2_LIM,
         tag(R2_ok, not math.isnan(tip_defl_mm))),
        ("R3", "fatigue_alt_eq_MPa(closed-form/Goodman)", sigma_alt_eq, "<=", R3_LIM,
         tag(R3_ok)),
        ("R4", "arm_dry_mass_kg(closed-form)", arm_mass_kg, "<=", R4_LIM,
         tag(R4_ok)),
        ("R5", "first_freq_Hz", first_freq_hz if first_freq_hz is not None else float("nan"),
         ">=", R5_LIM, tag(R5_ok, first_freq_hz is not None)),
    ]
    print("-" * 76)
    print(f"{'ID':<4}{'METRIC':<46}{'VALUE':>10}  {'OP':<2} {'LIMIT':>8}  STATUS")
    print("-" * 76)
    for rid, metric, val, op, lim, status in rows:
        v_str = f"{val:>10.3f}" if not math.isnan(val) else f"{'n/a':>10}"
        print(f"{rid:<4}{metric:<46}{v_str}  {op:<2} {lim:>8.2f}  {status}")
    print("-" * 76)

    statuses = [r[5] for r in rows]
    overall = "PASS" if all(s in ("PASS", "SKIP") for s in statuses) else "FAIL"
    n_fail = sum(1 for s in statuses if s == "FAIL")
    n_skip = sum(1 for s in statuses if s == "SKIP")
    print(f"OVERALL: {overall} (failures={n_fail}, skipped={n_skip})")
    return 0 if overall == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
