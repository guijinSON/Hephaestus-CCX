#!/usr/bin/env python3
"""Pass/fail check for FIA Art 253 (2013) rollcage CalculiX run.

This eval kit is submission-agnostic: agents provide out.step + meta.json.
Geometry parameters used in the closed-form structural verification are
read from spec.json (FIA permitted tube OD x wall, hoop dimensions,
mount-foot dimensions).

Per FIA Appendix J Article 253-8.3.2 the homologation tests are
*quasi-static* loads applied separately:
  LC1 vertical  7.5 W = 88,290 N on main-hoop top (W = 1200 kg * 9.81)
  LC2 rearward  3.5 W = 41,202 N on front-hoop top (rearward direction)
  LC3 lateral   3.5 W = 41,202 N on main-hoop at driver shoulder ~915 mm
With deformation <= 50 mm at the load point under each load.

Pass/fail criteria (see spec.json -> requirements.pass_fail_criteria):
  R1, R2, R3 - max von Mises stress under each LC <= 235 MPa (Fty proxy)
  R4         - deformation at the load patch <= 50 mm under each LC
  R5         - tube OD x wall is in the FIA permitted table
  R6         - mount foot geometric compliance (count, area, thickness, bolts)
  R7         - rollcage total mass <= 45 kg

Approach:
  * R1..R4 are computed CLOSED-FORM from beam theory using the spec tube
    sections (OD x wall) and the hoop dimensions. This is mesh-independent
    and matches FIA homologation practice (analytical sizing vs. yield).
  * The FEM model.dat is read for cross-check displacements at the load
    patches. Because *CLOAD on a solid mesh applies the value PER NODE
    in the NSET, the FEM run uses a small representative per-node load
    (see analysis_template.inp); FEM displacements are reported in the log
    but are not the gating result.
  * R5, R6, R7 are binary geometric / mass checks against spec.json.
"""

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
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
FRD = os.path.join(ROOT, "model.frd")
DAT = os.path.join(ROOT, "model.dat")
INP = os.path.join(ROOT, "model.inp")
SPEC = os.path.join(ROOT, "spec.json")

VM_LIMIT = 235.0   # MPa (Fty)
U_LIMIT = 50.0     # mm

# FIA permitted tube tables for cold-drawn seamless non-alloy carbon steel
# (>=350 N/mm^2 UTS, <=0.3% C). Reference: FIA Appendix J Article 253 (2013)
# Drawing 253-37 / Table 253. Common permitted entries for main hoop/bracing:
#   45.0 x 2.5,  50.0 x 2.0,  38.0 x 2.5  (mm).
FIA_PERMITTED_OD_WALL = {(45.0, 2.5), (50.0, 2.0), (38.0, 2.5)}

# Material constants (cold-drawn seamless carbon steel, S235-equivalent)
E_MOD = 210000.0       # MPa
RHO = 7850.0           # kg/m^3
G_ACCEL = 9.81         # m/s^2

# Vehicle and FIA homologation constants
GVW_KG = 1200.0
W_NEWTONS = GVW_KG * G_ACCEL          # 11,772 N
F_LC1 = 7.5 * W_NEWTONS               # 88,290 N (vertical, MH top)
F_LC2 = 3.5 * W_NEWTONS               # 41,202 N (rearward, FH top)
F_LC3 = 3.5 * W_NEWTONS               # 41,202 N (lateral, MH shoulder)


# ---------------------------------------------------------------------------
# FRD parser: nodal DISP / STRESS extracted by step
# ---------------------------------------------------------------------------
def parse_frd(path):
    """Return dict step -> {'DISP': {nid: (ux,uy,uz)}, 'STRESS': {nid: (...)} }."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path) as f:
        lines = f.readlines()
    cur_step = None
    cur_block = None
    for ln in lines:
        s = ln.strip()
        if s.startswith("100CL"):
            toks = s.split()
            try:
                cur_step = int(toks[5])
            except (ValueError, IndexError):
                cur_step = None
        elif s.startswith("-4"):
            toks = s.split()
            name = toks[1] if len(toks) > 1 else ""
            cur_block = None
            if cur_step is not None and name in ("DISP", "STRESS"):
                cur_block = name
                out.setdefault(cur_step, {}).setdefault(name, {})
        elif s.startswith("-1") and cur_block is not None and cur_step is not None:
            toks = s.split()
            try:
                nid = int(toks[1])
                vals = [float(x) for x in toks[2:]]
            except ValueError:
                continue
            if cur_block == "DISP":
                out[cur_step]["DISP"][nid] = tuple(vals[:3])
            else:
                out[cur_step]["STRESS"][nid] = tuple(vals[:6])
        elif s.startswith("-3"):
            cur_block = None
    return out


def vmises(s):
    sxx, syy, szz, sxy, syz, szx = s
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * (sxy * sxy + syz * syz + szx * szx))


# ---------------------------------------------------------------------------
# DAT parser: NODE PRINT (DISP per NSET) and EL PRINT blocks
# ---------------------------------------------------------------------------
def parse_dat(path):
    out = {}
    if not os.path.exists(path):
        return out
    cur_step = 0
    cur_kind = None
    cur_set = None
    with open(path) as f:
        for raw in f:
            stripped = raw.strip()
            if not stripped:
                continue
            up = stripped.upper()
            if up.startswith("S T E P"):
                toks = stripped.split()
                try:
                    cur_step = int(toks[-1])
                except ValueError:
                    cur_step = cur_step + 1
                cur_kind = None
                cur_set = None
                continue
            if stripped.startswith("displacements"):
                parts = stripped.split()
                try:
                    idx = parts.index("set")
                    cur_set = parts[idx + 1]
                except ValueError:
                    cur_set = None
                cur_kind = "DISP"
                out.setdefault(cur_step, {}).setdefault("DISP", {})
                if cur_set is not None:
                    out[cur_step]["DISP"].setdefault(cur_set, {})
                continue
            if stripped.startswith("stresses"):
                cur_kind = "STRESS"
                continue
            toks = stripped.split()
            if cur_kind == "DISP" and cur_set is not None:
                try:
                    nid = int(toks[0])
                    ux, uy, uz = float(toks[1]), float(toks[2]), float(toks[3])
                    out[cur_step]["DISP"][cur_set][nid] = (ux, uy, uz)
                except (ValueError, IndexError):
                    pass
    return out


# ---------------------------------------------------------------------------
# Tube section properties (hollow round)
# ---------------------------------------------------------------------------
def tube_section(OD, wall):
    """Return (A, I, S, c) for a hollow round tube."""
    D = float(OD)
    d = D - 2.0 * float(wall)
    A = math.pi / 4.0 * (D * D - d * d)
    I = math.pi / 64.0 * (D ** 4 - d ** 4)
    c = D / 2.0
    S = I / c
    return A, I, S, c


# ---------------------------------------------------------------------------
# Closed-form structural verification (R1..R4)
# ---------------------------------------------------------------------------
def closed_form_R1_to_R4(geom):
    """Compute closed-form max vM stress and load-point deflection per LC.

    Tube sizing per spec geometric_constraints:
      main_hoop, front_hoop : OD x wall (45 x 2.5 default)
      connecting, diagonal  : OD x wall (38 x 2.5 default)

    Engineering models:
      LC1  vertical 88290 N applied at midspan of MH crossbar.
        - The crossbar is a simply-supported beam of length L=shoulder
          width with midspan force F. M_max = F*L/4.
        - Each MH leg also carries axial F/2 in compression; combined
          stress is sigma_axial + |M_leg/S| but for the MH crossbar the
          midspan bending governs.
        - Deflection at midspan of crossbar: delta = F*L^3/(48*E*I).

      LC2  rearward 41202 N applied at FH top, near MH-FH connection.
        - Each FH leg cantilevers from base (fixed) up to z=H_hoop.
        - Per-leg force F/2; M_max = (F/2)*L_leg at base.
        - Tip deflection: delta = (F/2)*L^3/(3*E*I) per leg.

      LC3  lateral 41202 N applied at z = z_shoulder on the driver-side
        MH leg.
        - Cantilever fixed at base, force at z_shoulder.
        - M_max = F * z_shoulder; delta = F*z_shoulder^3/(3*E*I).

    Returns dict lc_id -> {sigma_MPa, u_mm}.
    """
    OD_mh = float(geom["main_hoop_tube_mm"]["OD"])
    wl_mh = float(geom["main_hoop_tube_mm"]["wall"])
    OD_fh = float(geom["front_hoop_tube_mm"]["OD"])
    wl_fh = float(geom["front_hoop_tube_mm"]["wall"])
    H_hoop = float(geom["main_hoop_height_mm"])
    W_hoop = float(geom["main_hoop_width_mm"])
    z_shoulder = 915.0   # FIA driver shoulder reference height (mm)

    A_mh, I_mh, S_mh, c_mh = tube_section(OD_mh, wl_mh)
    A_fh, I_fh, S_fh, c_fh = tube_section(OD_fh, wl_fh)

    # LC1: simply-supported MH crossbar, midspan point load
    M_lc1 = F_LC1 * W_hoop / 4.0
    sigma_lc1 = M_lc1 / S_mh
    u_lc1 = (F_LC1 * W_hoop ** 3) / (48.0 * E_MOD * I_mh)

    # LC2: FH cantilever leg, force at top, two-leg sharing
    F_lc2_leg = F_LC2 / 2.0
    M_lc2 = F_lc2_leg * H_hoop
    sigma_lc2 = M_lc2 / S_fh
    u_lc2 = (F_lc2_leg * H_hoop ** 3) / (3.0 * E_MOD * I_fh)

    # LC3: MH cantilever leg, force at shoulder height
    M_lc3 = F_LC3 * z_shoulder
    sigma_lc3 = M_lc3 / S_mh
    u_lc3 = (F_LC3 * z_shoulder ** 3) / (3.0 * E_MOD * I_mh)

    return {
        "LC1": {"sigma_MPa": sigma_lc1, "u_mm": u_lc1, "M_Nmm": M_lc1,
                "S_mm3": S_mh, "I_mm4": I_mh, "F_N": F_LC1},
        "LC2": {"sigma_MPa": sigma_lc2, "u_mm": u_lc2, "M_Nmm": M_lc2,
                "S_mm3": S_fh, "I_mm4": I_fh, "F_N": F_LC2},
        "LC3": {"sigma_MPa": sigma_lc3, "u_mm": u_lc3, "M_Nmm": M_lc3,
                "S_mm3": S_mh, "I_mm4": I_mh, "F_N": F_LC3},
    }


# ---------------------------------------------------------------------------
# R5: tube table compliance
# ---------------------------------------------------------------------------
def check_R5(geom):
    tubes = []
    all_ok = True
    for key in ("main_hoop_tube_mm", "front_hoop_tube_mm",
                "connecting_tubes_mm", "diagonal_tube_mm"):
        od = float(geom[key]["OD"])
        wall = float(geom[key]["wall"])
        ok = (od, wall) in FIA_PERMITTED_OD_WALL
        all_ok &= ok
        tubes.append((key, od, wall, ok))
    return tubes, all_ok


# ---------------------------------------------------------------------------
# R6: mount feet compliance
# ---------------------------------------------------------------------------
def check_R6(geom):
    feet = geom["mount_feet"]
    return (
        feet["count"] >= 4
        and feet["plate_thickness_mm"] >= 3
        and feet["area_cm2_min"] >= 120
        and feet["bolts_per_foot_min"] >= 3
    )


# ---------------------------------------------------------------------------
# R7: total rollcage tube mass (using spec OD x wall and topology lengths)
# ---------------------------------------------------------------------------
def compute_R7_mass(geom):
    OD_mh = float(geom["main_hoop_tube_mm"]["OD"])
    wl_mh = float(geom["main_hoop_tube_mm"]["wall"])
    OD_38 = float(geom["connecting_tubes_mm"]["OD"])
    wl_38 = float(geom["connecting_tubes_mm"]["wall"])
    H = float(geom["main_hoop_height_mm"])
    W = float(geom["main_hoop_width_mm"])
    Lwb = float(geom["front_to_main_mm"])

    A_mh, _, _, _ = tube_section(OD_mh, wl_mh)
    A_38, _, _, _ = tube_section(OD_38, wl_38)

    # Main hoop: 2 legs + crossbar
    L_mh = 2 * H + W
    # Front hoop: 2 legs + crossbar
    L_fh = 2 * H + W
    # 2 upper longitudinal connectors, each = wheelbase-from-main
    L_conn = 2 * Lwb
    # MH diagonal: from top corner to opposite bottom (3D distance)
    L_diag = math.sqrt(W ** 2 + H ** 2)
    # 2 lower side bars at z=0
    L_side = 2 * Lwb

    rho = RHO * 1e-9   # kg/mm^3
    mass = rho * (A_mh * L_mh + A_mh * L_fh
                  + A_38 * L_conn + A_38 * L_diag + A_38 * L_side)
    return mass


# ---------------------------------------------------------------------------
# FEM cross-check: read load-patch displacements from .dat
# ---------------------------------------------------------------------------
def fem_cross_check(fem_dat):
    """For each LC, find max |U| at the load-patch NSET."""
    cross = {}
    for step, lc in [(1, "LC1"), (2, "LC2"), (3, "LC3")]:
        u_at_load = 0.0
        nsets = fem_dat.get(step, {}).get("DISP", {})
        nset_name = "N" + lc      # NLC1 / NLC2 / NLC3
        if nset_name in nsets:
            for nid, u in nsets[nset_name].items():
                mag = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
                u_at_load = max(u_at_load, mag)
        cross[lc] = u_at_load
    return cross


def fem_global_max(fem_frd):
    """Per-step global max von Mises and global max |U| from FRD."""
    out = {}
    for step in (1, 2, 3):
        block = fem_frd.get(step, {})
        stress_nodal = block.get("STRESS", {})
        if stress_nodal:
            vm_max = max(vmises(s) for s in stress_nodal.values())
        else:
            vm_max = float("nan")
        disp_frd = block.get("DISP", {})
        if disp_frd:
            u_global = max(math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
                           for u in disp_frd.values())
        else:
            u_global = float("nan")
        out[step] = (vm_max, u_global)
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    spec = json.load(open(SPEC))
    geom = spec["prompt"]["geometric_constraints"]

    # Closed-form R1..R4
    cf = closed_form_R1_to_R4(geom)
    # R5
    tube_checks, all_tubes_ok = check_R5(geom)
    # R6
    foot_ok = check_R6(geom)
    # R7
    mass_kg = compute_R7_mass(geom)

    # FEM cross-check (optional)
    fem_frd = parse_frd(FRD)
    fem_dat = parse_dat(DAT)
    fem_disp_load = fem_cross_check(fem_dat)
    fem_global = fem_global_max(fem_frd)

    print("=" * 78)
    print("FIA Appendix J Article 253 (2013) rollcage - verification report")
    print("Spec:", spec["id"])
    print("Solver: CalculiX 2.22 (3 *STEP linear-static; representative per-node load)")
    print("Structural gates R1..R4 verified CLOSED-FORM from spec OD x wall sections.")
    print("=" * 78)

    print("\n[Closed-form structural verification]")
    print(f"  Material: cold-drawn seamless carbon steel, Fty = {VM_LIMIT:.0f} MPa, "
          f"E = {E_MOD/1000:.0f} GPa")
    print(f"  Geometry: MH/FH height={geom['main_hoop_height_mm']} mm, "
          f"shoulder width={geom['main_hoop_width_mm']} mm, "
          f"front-to-main={geom['front_to_main_mm']} mm")
    OD_mh = geom['main_hoop_tube_mm']['OD']
    wl_mh = geom['main_hoop_tube_mm']['wall']
    OD_fh = geom['front_hoop_tube_mm']['OD']
    wl_fh = geom['front_hoop_tube_mm']['wall']
    print(f"  Tubes: MH = {OD_mh} x {wl_mh} mm, FH = {OD_fh} x {wl_fh} mm")
    print(f"  Loads: LC1 = {F_LC1:>7.0f} N (7.5 W), "
          f"LC2 = {F_LC2:>7.0f} N (3.5 W), "
          f"LC3 = {F_LC3:>7.0f} N (3.5 W)")

    print()
    print(f"  {'LC':<5} {'sigma_b[MPa]':>14s} {'M[kN.m]':>10s} "
          f"{'S[mm^3]':>10s} {'u_load[mm]':>12s}")
    for lc_id in ("LC1", "LC2", "LC3"):
        s = cf[lc_id]
        print(f"  {lc_id:<5} {s['sigma_MPa']:>14.1f} {s['M_Nmm']/1e6:>10.2f} "
              f"{s['S_mm3']:>10.0f} {s['u_mm']:>12.2f}")

    print("\n[Geometric compliance]")
    print("R5 - tube OD x wall vs FIA permitted table:")
    for (k, od, wall, ok) in tube_checks:
        print(f"  {k:<24s} {od:.1f} x {wall:.1f}  -> {'PASS' if ok else 'FAIL'}")
    print(f"R5 overall: {'PASS' if all_tubes_ok else 'FAIL'}")
    print(f"R6 - mount feet (count>=4, plate>=3 mm, area>=120 cm^2, bolts>=3): "
          f"{'PASS' if foot_ok else 'FAIL'}")
    print(f"R7 - rollcage mass = {mass_kg:.2f} kg (target <= 45 kg): "
          f"{'PASS' if mass_kg <= 45.0 else 'FAIL'}")

    print("\n[FEM sanity-check (small per-node load; cross-reference only)]")
    if fem_global:
        print(f"  {'step':<5} {'vM_max[MPa]':>14s} {'U_load[mm]':>12s} {'U_glob[mm]':>12s}")
        for (step, lc_id) in [(1, "LC1"), (2, "LC2"), (3, "LC3")]:
            vm_max, u_glob = fem_global.get(step, (float('nan'), float('nan')))
            u_load = fem_disp_load.get(lc_id, 0.0)
            print(f"  {step:<5d} {vm_max:>14.2f} {u_load:>12.4f} {u_glob:>12.4f}")
    else:
        print("  (no model.frd found - FEM sanity-check skipped)")

    print("\n[Pass/fail per criterion]")
    crits = []
    crits.append(("R1 max sigma LC1 <= 235 MPa (closed-form, spec MH section)",
                  cf["LC1"]["sigma_MPa"] <= VM_LIMIT))
    crits.append(("R2 max sigma LC2 <= 235 MPa (closed-form, spec FH section)",
                  cf["LC2"]["sigma_MPa"] <= VM_LIMIT))
    crits.append(("R3 max sigma LC3 <= 235 MPa (closed-form, spec MH section)",
                  cf["LC3"]["sigma_MPa"] <= VM_LIMIT))
    crits.append(("R4 max u at load <= 50 mm (closed-form, all LCs)",
                  all(cf[lc]["u_mm"] <= U_LIMIT for lc in ("LC1", "LC2", "LC3"))))
    crits.append(("R5 tube OD x wall == FIA table", all_tubes_ok))
    crits.append(("R6 mount foot geometric compliance", foot_ok))
    crits.append(("R7 rollcage mass <= 45 kg", mass_kg <= 45.0))
    for name, ok in crits:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    overall = all(ok for _, ok in crits)
    print("\n" + "=" * 78)
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    print("=" * 78)

    print("\nNotes:")
    print(" - The minimum FIA Art 253 lattice (MH+FH+upper conn+1 diag+side bars)")
    print("   is a CATALOG-permitted topology. Closed-form vM stresses for the")
    print("   permitted 45x2.5 / 38x2.5 sections under the homologation loads")
    print("   exceed the 235 MPa yield - real homologated cages add door bars,")
    print("   X-bracing, harness bar, and windscreen pillar tubes per Art 253-8.3.")
    print(" - R5/R6/R7 are binary geometric/mass gates and PASS for the spec.")
    print(" - FEM is run end-to-end (build -> gmsh -> wire_bcs -> ccx -> check)")
    print("   so the kit verifies the full submission-agnostic toolchain.")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
