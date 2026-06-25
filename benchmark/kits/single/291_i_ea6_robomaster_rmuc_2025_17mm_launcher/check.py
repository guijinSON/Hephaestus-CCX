#!/usr/bin/env python3
"""
check.py - Pass/fail evaluator for the RMUC 2025 17 mm launcher.

Pipeline produced by grade_ccx.py:
    build.py          -> out.step + meta.json
    gmsh              -> mesh.inp
    wire_bcs.py       -> model.inp (mesh + Eall + NSETs + template)
    ccx_2.22 model    -> model.dat
    check.py          -> read this file

Authoritative source for limits is spec.json. The FEM contributes
peak von Mises in the housing under LC1, LC3, LC4 (steps 1..3 in
the analysis_template). R2/R3/R6/R7 are closed-form because:

  * LC2 (flywheel spin) is a centrifugal load that the lumped-disc
    FEM block does not need to carry; the rim stress and radial
    growth of an unconstrained 2024-T3 thin disc are well-defined
    closed-form expressions.
  * R6 mass uses the geometry the agent submitted via build.py
    plus the 2024-T3 flywheel rims and the 6061-T6 barrel sleeve
    that the spec mandates as part of the assembly mass budget.
  * R7 is the thermal-expansion of the bore (alpha * dT * D).
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

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, "spec.json")))
DAT = os.path.join(HERE, "model.dat")


# ------------------------------------------------------------------
# Spec values
# ------------------------------------------------------------------
PROMPT = SPEC["prompt"]
GEOM = PROMPT["geometric_constraints"]
MAT_HOUSE = PROMPT["material"]["housing"]    # 6061-T6
MAT_FLY = PROMPT["material"]["flywheel"]     # 2024-T3
LCs = {lc["id"]: lc for lc in PROMPT["load_cases"]}

LIMITS = {r["id"]: r for r in SPEC["requirements"]["pass_fail_criteria"]}


# ------------------------------------------------------------------
# Parse model.dat -> peak von Mises per step
# ------------------------------------------------------------------
def parse_peak_vm_per_step(dat_path: str) -> dict[str, float]:
    """Return {time_str: peak_vm_MPa} from CalculiX *.dat stress blocks.

    CCX writes one stress block per step under the header:
        stresses (elem, integ.pnt.,sxx,...) for set EALL and time  0.1000000E+01
    """
    if not os.path.exists(dat_path):
        return {}
    header_re = re.compile(
        r"\s*stresses.*?for set\s+(\S+)\s+and time\s+(\S+)",
        re.IGNORECASE,
    )
    peaks: dict[str, float] = {}
    cur_time: str | None = None
    cur_max = 0.0
    with open(dat_path) as fh:
        for line in fh:
            m = header_re.match(line)
            if m:
                if cur_time is not None:
                    peaks[cur_time] = max(peaks.get(cur_time, 0.0), cur_max)
                cur_time = m.group(2)
                cur_max = 0.0
                continue
            if cur_time is None:
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
            vm = math.sqrt(0.5 * (
                (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
                + 6.0 * (sxy * sxy + sxz * sxz + syz * syz)
            ))
            if vm > cur_max:
                cur_max = vm
        if cur_time is not None:
            peaks[cur_time] = max(peaks.get(cur_time, 0.0), cur_max)
    return peaks


# ------------------------------------------------------------------
# Closed-form computations
# ------------------------------------------------------------------
def flywheel_centrifugal() -> tuple[float, float]:
    """Return (peak_vm_MPa, radial_growth_mm) for a 2024-T3 disc at 10 000 rpm.

    Thin solid disc (Lame, plane-stress, free outer rim):
        sigma_r(r=0) = sigma_t(r=0) = (3+nu)/8 * rho * omega^2 * R^2
        u_r(r=R)     = (1-nu)/4 * rho * omega^2 * R^3 / E   (free disc upper bound)
    """
    rpm = LCs["LC2"]["rpm"]
    omega = rpm * 2.0 * math.pi / 60.0
    R = (GEOM["flywheel_OD_mm"] / 2.0) / 1000.0       # m
    nu = 0.33
    rho = MAT_FLY["density_kg_m3"]
    E = MAT_FLY["youngs_modulus_GPa"] * 1e9

    sigma_peak_Pa = (3.0 + nu) / 8.0 * rho * omega * omega * R * R
    u_r_m = (1.0 - nu) / 4.0 * rho * omega * omega * R ** 3 / E

    return sigma_peak_Pa / 1e6, u_r_m * 1000.0       # MPa, mm


def assembly_mass_g() -> float:
    """Closed-form launcher-assembly mass.

    Components:
      * Housing: 6061-T6 box outer envelope minus a 3 mm-walled
        internal pocket (matches the agent's submission and the
        spec's 'machined' housing).
      * 2 flywheels: 2024-T3 60 OD x 15 with a lightening pocket
        leaving an 8 mm rim and 4 mm web.
      * Barrel sleeve: 6061-T6 22 mm OD x 17.5 mm ID x 80 mm long.
    """
    rho_h = MAT_HOUSE["density_kg_m3"]
    rho_f = MAT_FLY["density_kg_m3"]

    # Housing in m^3 (envelope from spec)
    L = GEOM["envelope_mm"]["L"] / 1000.0
    W = GEOM["envelope_mm"]["W"] / 1000.0
    H = 0.040  # half-height shell
    Vouter = L * W * H
    Vinner = max(0.0, (L - 0.006) * (W - 0.006) * (H - 0.006))
    V_house = Vouter - Vinner
    m_house = V_house * rho_h

    # 2 flywheels: rim (D=60, d=50) x 15 mm + web (D=50, d=12) x 4 mm
    V_rim = math.pi * (0.030 ** 2 - 0.025 ** 2) * (GEOM["flywheel_width_mm"] / 1000.0)
    V_web = math.pi * (0.025 ** 2 - 0.006 ** 2) * 0.004
    V_fly_one = V_rim + V_web
    m_fly = 2.0 * V_fly_one * rho_f

    # Barrel sleeve: 22 OD, 17.5 ID, 80 long
    R_o = 0.011
    R_i = (GEOM["barrel_ID_mm"] / 2.0) / 1000.0
    L_b = GEOM["barrel_length_mm"] / 1000.0
    V_bar = math.pi * (R_o * R_o - R_i * R_i) * L_b
    m_bar = V_bar * rho_h

    return (m_house + m_fly + m_bar) * 1000.0


def barrel_id_mm_under_lc3() -> float:
    """ID under uniform thermal expansion (closed form)."""
    alpha = 2.36e-5
    dT = LCs["LC3"]["delta_T_K"]
    D0 = GEOM["barrel_ID_mm"]
    return D0 * (1.0 + alpha * dT)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main() -> int:
    peaks = parse_peak_vm_per_step(DAT)
    times = sorted(peaks.keys())
    print(f"# parsed stress blocks: {len(peaks)} time-steps -> {times}")

    # Step ordering inside analysis_template.inp:
    #   t=1.0 -> LC1 (shot reaction)
    #   t=2.0 -> LC3 (thermal soak)
    #   t=3.0 -> LC4 (lateral 20 g)
    def at(idx: int) -> float:
        if idx < len(times):
            return peaks[times[idx]] / 1.0  # already MPa (mm/N/MPa units)
        return float("nan")

    vm_lc1 = at(0)
    vm_lc3 = at(1)
    vm_lc4 = at(2)

    # Closed-form
    vm_fly, rg_fly = flywheel_centrifugal()
    m_tot = assembly_mass_g()
    new_id = barrel_id_mm_under_lc3()

    R1 = vm_lc1 <= LIMITS["R1"]["limit_MPa"]
    R2 = vm_fly <= LIMITS["R2"]["limit_MPa"]
    R3 = rg_fly <= LIMITS["R3"]["limit_mm"]
    R4 = (vm_lc1 + vm_lc3) <= LIMITS["R4"]["limit_MPa"]    # linear superposition
    R5 = vm_lc4 <= LIMITS["R5"]["limit_MPa"]
    R6 = m_tot <= LIMITS["R6"]["limit_g"]
    R7 = (LIMITS["R7"]["lower_limit_mm"] <= new_id <= LIMITS["R7"]["upper_limit_mm"])

    def tag(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    print()
    print(f"R1 LC1 housing  vM = {vm_lc1:8.2f} MPa  (<= {LIMITS['R1']['limit_MPa']}) -> {tag(R1)}")
    print(f"R2 LC2 flywheel vM = {vm_fly:8.2f} MPa  (<= {LIMITS['R2']['limit_MPa']}) -> {tag(R2)}  [closed-form]")
    print(f"R3 LC2 fly radial growth = {rg_fly:8.4f} mm  (<= {LIMITS['R3']['limit_mm']}) -> {tag(R3)}  [closed-form]")
    print(f"R4 LC1+LC3 housing vM = {vm_lc1+vm_lc3:8.2f} MPa  "
          f"(LC1={vm_lc1:.2f}, LC3={vm_lc3:.2f}; <= {LIMITS['R4']['limit_MPa']}) -> {tag(R4)}")
    print(f"R5 LC4 housing  vM = {vm_lc4:8.2f} MPa  (<= {LIMITS['R5']['limit_MPa']}) -> {tag(R5)}")
    print(f"R6 mass = {m_tot:8.1f} g  (<= {LIMITS['R6']['limit_g']}) -> {tag(R6)}  [closed-form]")
    print(f"R7 barrel ID under LC3 = {new_id:8.4f} mm  "
          f"({LIMITS['R7']['lower_limit_mm']}..{LIMITS['R7']['upper_limit_mm']}) -> {tag(R7)}  [closed-form]")

    overall = all([R1, R2, R3, R4, R5, R6, R7])
    print()
    print(f"OVERALL: {tag(overall)}")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
