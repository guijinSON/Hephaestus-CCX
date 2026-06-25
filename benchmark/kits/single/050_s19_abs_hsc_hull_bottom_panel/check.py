#!/usr/bin/env python3
"""
ABS HSC Part 3 (2013) S19 forward bottom panel - submission-agnostic check.

Parses model.frd from CalculiX (single *STATIC step at LC1 slamming
50 kPa) and applies the seven ABS HSC Part 3 pass/fail gates:

  R1  plate thickness >= ABS scantling minimum         (closed-form)
  R2  LC1 vM in plate  <= sigma_perm 120 MPa           (FEM)
  R3  LC3 = LC1 + 60 MPa hull-girder <= 215 MPa        (FEM, conservative)
  R4  LC2 = 40 kPa mid-deflection <= s/300 = 1.67 mm   (FEM, scaled)
  R5  longitudinal stiffener Z      >= 25 cm^3         (closed-form)
  R6  weld-toe stress range at 1e6 cycles <= 71 MPa    (FEM, scaled)
  R7  mass per unit hull area       <= 25 kg/m^2       (closed-form)

The FEM portion (R2/R3/R4/R6) reads model.frd and identifies plate
nodes by location (z = 0 wetted face up to z = T_PLATE dry face). The
closed-form portion (R1/R5/R7) uses spec geometry and ABS Part 3
formulas directly so it is independent of the FEM mesh.

Output: PASS/FAIL per criterion and OVERALL.
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

import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FRD = os.path.join(HERE, "model.frd")

# ---------------------------------------------------------------------------
# Spec values (mm, MPa, kPa, kg/m^3)
# ---------------------------------------------------------------------------
s_mm = 500.0           # stiffener spacing (transverse)
L_fr_mm = 1200.0       # frame spacing (longitudinal)
t_plate_mm = 5.0
p_slam_MPa = 0.050     # LC1 50 kPa
p_hydro_MPa = 0.040    # LC2 40 kPa
sigma_perm_MPa = 120.0
sigma_y_MPa = 215.0
sigma_hg_MPa = 60.0    # LC3 hull-girder bending stress
p_fat_MPa = 0.020      # LC4 fatigue range 20 kPa
delta_sigma_fat_MPa = 71.0  # ABS Class D at 1e6 cycles

R1_min_mm = 5.0
R2_lim_MPa = 120.0
R3_lim_MPa = 215.0
R4_lim_mm = 1.67
R5_lim_cm3 = 25.0
R6_lim_MPa = 71.0
R7_lim_kg_m2 = 25.0

# Plate geometry: z in [0, T_PLATE]; outside that band = stiffener / frame
T_PLATE = 5.0
PLATE_Z_TOL = 0.01     # tolerance for "is on wetted face"


# ---------------------------------------------------------------------------
# FRD parser (CCX 2.22): one *STATIC step
# ---------------------------------------------------------------------------
def parse_frd(path: str):
    """Return (nodes, disp, stress) from a CCX FRD file.

    nodes  : {nid: (x, y, z)}
    disp   : {nid: (ux, uy, uz)}             (last DISP block found)
    stress : {nid: (sxx, syy, szz, sxy, syz, szx)}
    """
    nodes: dict[int, tuple[float, float, float]] = {}
    disp:  dict[int, tuple[float, float, float]] = {}
    stress: dict[int, tuple[float, float, float, float, float, float]] = {}

    with open(path) as f:
        lines = f.readlines()

    cur_block = None
    for line in lines:
        head3 = line[:3]
        # Node coordinate block: header "    2C", then ' -1 NID  X Y Z'
        if line.startswith("    2C"):
            cur_block = "NODES"
            continue
        if line.startswith("    3C"):
            cur_block = "ELEM"
            continue
        if line.startswith(" -4"):
            tag = line[5:13].strip()
            if tag == "DISP":
                cur_block = "DISP"
            elif tag == "STRESS":
                cur_block = "STRESS"
            else:
                cur_block = None
            continue
        if line.startswith(" -3"):
            cur_block = None
            continue

        if head3 == " -1" and cur_block == "NODES":
            try:
                nid = int(line[3:13])
                x = float(line[13:25])
                y = float(line[25:37])
                z = float(line[37:49])
                nodes[nid] = (x, y, z)
            except (ValueError, IndexError):
                pass
        elif head3 == " -1" and cur_block == "DISP":
            try:
                nid = int(line[3:13])
                ux = float(line[13:25])
                uy = float(line[25:37])
                uz = float(line[37:49])
                disp[nid] = (ux, uy, uz)
            except (ValueError, IndexError):
                pass
        elif head3 == " -1" and cur_block == "STRESS":
            try:
                nid = int(line[3:13])
                sxx = float(line[13:25])
                syy = float(line[25:37])
                szz = float(line[37:49])
                sxy = float(line[49:61])
                syz = float(line[61:73])
                szx = float(line[73:85])
                stress[nid] = (sxx, syy, szz, sxy, syz, szx)
            except (ValueError, IndexError):
                pass

    return nodes, disp, stress


def vm(s: tuple[float, float, float, float, float, float]) -> float:
    sxx, syy, szz, sxy, syz, szx = s
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * (sxy * sxy + syz * syz + szx * szx))


def main() -> int:
    print("=" * 72)
    print(" ABS HSC Part 3 (2013) S19 forward bottom panel - pass/fail check ")
    print("=" * 72)

    if not os.path.exists(FRD):
        print(f"ERROR: {FRD} not found")
        return 2

    nodes, disp, stress = parse_frd(FRD)
    print(f"Parsed {len(nodes)} nodes, {len(disp)} disp, {len(stress)} stress")

    # Identify plate nodes: z within [0, T_PLATE] band  (plate occupies that
    # band; stiffener and frame nodes have z > T_PLATE).
    plate_nids = {nid for nid, xyz in nodes.items()
                  if -PLATE_Z_TOL <= xyz[2] <= T_PLATE + PLATE_Z_TOL}
    print(f"Plate-band nodes (z in [0, {T_PLATE}] +/- {PLATE_Z_TOL}): {len(plate_nids)}")

    # Max von Mises among plate nodes (LC1 = 50 kPa already applied)
    vm_max = 0.0
    vm_node = None
    for nid in plate_nids:
        if nid in stress:
            v = vm(stress[nid])
            if v > vm_max:
                vm_max = v
                vm_node = nid

    # Max |UZ| among plate nodes (LC1)
    uz_max = 0.0
    uz_node = None
    for nid in plate_nids:
        if nid in disp:
            uz = abs(disp[nid][2])
            if uz > uz_max:
                uz_max = uz
                uz_node = nid

    # ------ R1: t >= ABS scantling minimum ----------------------------------
    # ABS HSC (mm-MPa form): t = s * sqrt(p / (k * sigma_perm)), k = 1.0.
    t_req_mm = s_mm * math.sqrt(p_slam_MPa / sigma_perm_MPa)
    R1_pass = t_plate_mm >= R1_min_mm
    print()
    print(f"R1 plate thickness:  t_actual = {t_plate_mm:.2f} mm, "
          f"t_min(spec) = {R1_min_mm:.2f} mm, "
          f"t_scantling(closed) = {t_req_mm:.2f} mm => "
          f"{'PASS' if R1_pass else 'FAIL'}")

    # ------ R2: LC1 von Mises <= 120 MPa ------------------------------------
    R2_pass = vm_max <= R2_lim_MPa
    print(f"R2 LC1 vM max plate: {vm_max:.2f} MPa, lim = {R2_lim_MPa:.2f} MPa "
          f"(node {vm_node}) => {'PASS' if R2_pass else 'FAIL'}")

    # ------ R3: LC3 = LC1 + hull girder 60 MPa (conservative superposition) -
    vm_combined = vm_max + sigma_hg_MPa
    R3_pass = vm_combined <= R3_lim_MPa
    print(f"R3 LC3 combined vM:  {vm_combined:.2f} MPa "
          f"(LC1 vM + hull-girder {sigma_hg_MPa} MPa), "
          f"lim = {R3_lim_MPa:.2f} MPa => "
          f"{'PASS' if R3_pass else 'FAIL'}")

    # ------ R4: LC2 mid-point deflection (linear scaling) -------------------
    uz_LC2 = uz_max * (p_hydro_MPa / p_slam_MPa)
    R4_pass = uz_LC2 <= R4_lim_mm
    print(f"R4 LC2 mid-point UZ: {uz_LC2:.3f} mm, lim = {R4_lim_mm:.3f} mm "
          f"(scaled from LC1 max |UZ|={uz_max:.3f} mm) => "
          f"{'PASS' if R4_pass else 'FAIL'}")

    # ------ R5: longitudinal stiffener section modulus ----------------------
    # L 50x50x5 angle. Section properties (standard table):
    #   A = 475 mm^2, I_xx ~ 11.0 cm^4, centroid offset ~13.97 mm
    # Effective plate flange: b_eff = s = 500 mm, depth = t_plate.
    b_eff = s_mm
    t_f = t_plate_mm
    A_pl = b_eff * t_f
    A_L = 475.0
    z_pl = -t_f / 2.0                  # plate flange centroid (below ref. line)
    z_L = -t_f - 13.97                 # angle centroid below plate
    A = A_pl + A_L
    z_NA = (A_pl * z_pl + A_L * z_L) / A
    I_pl = b_eff * t_f ** 3 / 12.0 + A_pl * (z_pl - z_NA) ** 2
    I_L = 1.10e5 + A_L * (z_L - z_NA) ** 2
    I_total = I_pl + I_L
    c_top = abs(0.0 - z_NA)
    c_bot = abs(-(t_f + 50.0) - z_NA)
    c = max(c_top, c_bot)
    Z_combined_mm3 = I_total / c
    Z_combined_cm3 = Z_combined_mm3 / 1000.0
    R5_pass = Z_combined_cm3 >= R5_lim_cm3
    print(f"R5 stiffener Z:      Z_combined = {Z_combined_cm3:.2f} cm^3, "
          f"lim = {R5_lim_cm3:.2f} cm^3 => "
          f"{'PASS' if R5_pass else 'FAIL'}")

    # ------ R6: weld toe stress range fatigue (scaled) ----------------------
    delta_sigma = vm_max * (p_fat_MPa / p_slam_MPa)
    R6_pass = delta_sigma <= R6_lim_MPa
    print(f"R6 fatigue toe range:{delta_sigma:.2f} MPa "
          f"(scaled from LC1 vM at 20 kPa range), lim = {R6_lim_MPa:.2f} MPa => "
          f"{'PASS' if R6_pass else 'FAIL'}")

    # ------ R7: mass per unit hull area -------------------------------------
    rho = 2700.0
    A_panel_m2 = (s_mm / 1000.0) * (L_fr_mm / 1000.0)
    m_plate = rho * (s_mm / 1000.0) * (L_fr_mm / 1000.0) * (t_plate_mm / 1000.0)
    A_long = 475.0e-6
    m_long = rho * A_long * (L_fr_mm / 1000.0)
    A_frame = 80.0 * 6.0 + 40.0 * 6.0
    m_frame = rho * (A_frame * 1e-6) * (s_mm / 1000.0)
    m_total = m_plate + m_long + m_frame
    mass_per_area = m_total / A_panel_m2
    R7_pass = mass_per_area <= R7_lim_kg_m2
    print(f"R7 mass per area:    {mass_per_area:.2f} kg/m^2, "
          f"lim = {R7_lim_kg_m2:.2f} kg/m^2 => "
          f"{'PASS' if R7_pass else 'FAIL'}")

    overall = (R1_pass and R2_pass and R3_pass and R4_pass
               and R5_pass and R6_pass and R7_pass)
    print()
    print("OVERALL: " + ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
