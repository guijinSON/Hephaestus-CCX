"""
Check pass/fail for Misumi HBLFSNB8-class corner bracket verification.

Combines:
 - FEA results from CalculiX .frd (von Mises stress, displacement on bracket)
 - Closed-form catalog/beam calculations for cantilever extrusion (R4, R5)
 - Catalog allowable comparison for per-bracket force/moment, T-nut load
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

import os
import re
import math

HERE = os.path.dirname(os.path.abspath(__file__))
FRD = os.path.join(HERE, "model.frd")


def parse_frd(path):
    """Return (max_uy_mag, max_disp_mag, max_vm_mises) from .frd

    FRD uses fixed-width fields after `-1 NNNNN`:
      ' -1  ' (5) + node id (5) + N * 12-char floats (E-format).
    Values are concatenated so we MUST slice instead of split() (negative
    signs collide with the previous field).
    """
    disps = []
    stresses = []
    block = None
    with open(path) as f:
        for raw in f:
            line = raw.rstrip("\n")
            if " -4  DISP" in line:
                block = "U"; continue
            if " -4  STRESS" in line:
                block = "S"; continue
            if line.startswith(" -3"):
                block = None; continue
            if block in ("U", "S") and line.startswith(" -1"):
                # node id sits in cols 4..12 (1-indexed cols 5..13 in CCX docs);
                # data fields each 12 chars starting at col 13 (index 13)
                try:
                    nid = int(line[3:13].strip())
                except Exception:
                    continue
                vals = []
                pos = 13
                while pos + 12 <= len(line):
                    chunk = line[pos:pos + 12].strip()
                    if not chunk:
                        break
                    try:
                        vals.append(float(chunk))
                    except Exception:
                        break
                    pos += 12
                if block == "U" and len(vals) >= 3:
                    disps.append((nid, vals[0], vals[1], vals[2]))
                elif block == "S" and len(vals) >= 6:
                    stresses.append((nid, vals[0], vals[1], vals[2], vals[3], vals[4], vals[5]))
    if not disps or not stresses:
        return None
    max_uy = max(abs(d[2]) for d in disps)
    max_dmag = max(math.sqrt(d[1] ** 2 + d[2] ** 2 + d[3] ** 2) for d in disps)
    def vm(s):
        sxx, syy, szz, sxy, syz, sxz = s[1], s[2], s[3], s[4], s[5], s[6]
        return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
                                + 6 * (sxy ** 2 + syz ** 2 + sxz ** 2)))
    max_vm = max(vm(s) for s in stresses)
    return max_uy, max_dmag, max_vm


# ---- Spec constants ----
# Catalog allowables (HBLFSNB8-class, per spec.json + Misumi catalog)
LIM_BR_STATIC = 1470.0   # N per bracket
LIM_BR_DYN = 490.0       # N per bracket
LIM_BR_M = 29.4          # N*m per bracket
LIM_TIP_DEFL = 1.33      # mm (L/300, L=400)
LIM_EXT_STRESS = 85.0    # MPa (170/2.0)
LIM_TNUT = 1200.0        # N per nut (2400/2.0)

# Geometry / material for closed-form
E_MPA = 68900.0          # 6063-T5
IX_MM4 = 90000.0         # spec catalog Ix
L_MM = 400.0             # cantilever length
F_VERT = 300.0
F_HORIZ = 150.0

# Section modulus for HFS8-4040 (square 40x40 outer): conservative bending
# z = Ix / c, with c = 20 mm
Z_MM3 = IX_MM4 / 20.0    # = 4500 mm^3

# ---- Closed-form cantilever ----
# Tip deflection: delta = F L^3 / (3 E I)
delta_v = F_VERT * L_MM ** 3 / (3.0 * E_MPA * IX_MM4)
delta_h = F_HORIZ * L_MM ** 3 / (3.0 * E_MPA * IX_MM4)
delta_total = math.sqrt(delta_v ** 2 + delta_h ** 2)

# Bending moments at root
M_root_v = F_VERT * L_MM      # N*mm = 120,000 N*mm = 120 N*m
M_root_h = F_HORIZ * L_MM     # 60,000 N*mm = 60 N*m
M_root_resultant = math.sqrt(M_root_v ** 2 + M_root_h ** 2)  # N*mm

# Extrusion bending stress at root: sigma = M / Z (use vertical M for x-axis)
sigma_v = M_root_v / Z_MM3   # MPa
sigma_h = M_root_h / Z_MM3
sigma_total = math.sqrt(sigma_v ** 2 + sigma_h ** 2)  # combined

# Per-bracket moment & force (two brackets, 40 mm effective arm)
N_BRACKETS = 2
ARM_M = 40.0  # mm (effective lever between bracket bolt rows)
# moment couple => force per bracket = M / arm (couple form), shared over N_BRACKETS pairs not legs
# Spec formula: per_bracket_force = M / (2 * arm) = 120e3 / (2*40) = 1500 N
per_bracket_force_static = M_root_v / (N_BRACKETS * ARM_M)  # N
per_bracket_moment = M_root_v / (N_BRACKETS * 1000.0)        # N*m, share moment between 2 brackets

# Dynamic load = full reversal at fatigue, take same value for stated payload
per_bracket_force_dyn = per_bracket_force_static

# T-nut pull-out: 2 nuts per leg, 1 leg engaged in tension per bracket
# Per-nut load = per_bracket_force / 2
per_tnut_load = per_bracket_force_static / 2.0

# ---- FEA results ----
fea = parse_frd(FRD)
if fea is None:
    print("ERROR: could not parse FRD")
    raise SystemExit(1)
max_uy, max_dmag, max_vm = fea


def line(tag, val, op, lim, unit):
    if op == "<=":
        ok = val <= lim
    else:
        ok = val >= lim
    return f"[{ 'PASS' if ok else 'FAIL'}] {tag}: {val:.3f} {unit} {op} {lim:.3f} {unit}"


print("=" * 70)
print("Misumi HBLFSNB8-class corner bracket verification (LC1 service)")
print("=" * 70)
print()
print("--- FEA (CalculiX) results on bracket C3D8 model ---")
print(f"  Max |Uy| on bracket:        {max_uy:.4f} mm")
print(f"  Max disp magnitude:         {max_dmag:.4f} mm")
print(f"  Max von Mises stress:       {max_vm:.2f} MPa")
print()
print("--- Closed-form (catalog/beam) computations ---")
print(f"  Cantilever tip vertical deflection (F=300 N):   {delta_v:.4f} mm")
print(f"  Cantilever tip horizontal deflection (F=150 N): {delta_h:.4f} mm")
print(f"  Combined tip deflection magnitude:              {delta_total:.4f} mm")
print(f"  Extrusion root bending moment (vertical):       {M_root_v/1000:.2f} N*m")
print(f"  Extrusion root bending moment (horizontal):     {M_root_h/1000:.2f} N*m")
print(f"  Extrusion root combined moment:                 {M_root_resultant/1000:.2f} N*m")
print(f"  Extrusion bending stress (vertical):            {sigma_v:.2f} MPa")
print(f"  Extrusion bending stress (horizontal):          {sigma_h:.2f} MPa")
print(f"  Per-bracket force (M/(N*arm), N=2,arm=40 mm):   {per_bracket_force_static:.1f} N")
print(f"  Per-bracket moment share (vertical):            {per_bracket_moment:.2f} N*m")
print(f"  Per T-nut load (2 nuts / engaged leg):          {per_tnut_load:.1f} N")
print()
print("--- Pass/Fail vs Misumi catalog & SF ---")
results = []
results.append(line("R1 per_bracket_force_static",  per_bracket_force_static, "<=", LIM_BR_STATIC, "N"))
results.append(line("R2 per_bracket_force_dynamic", per_bracket_force_dyn,    "<=", LIM_BR_DYN,    "N"))
results.append(line("R3 per_bracket_moment",        per_bracket_moment,       "<=", LIM_BR_M,      "N*m"))
results.append(line("R4 cantilever_tip_deflection", delta_total,              "<=", LIM_TIP_DEFL,  "mm"))
results.append(line("R5 max_extrusion_bending_stress", sigma_total,           "<=", LIM_EXT_STRESS,"MPa"))
results.append(line("R6 per_tnut_pullout_load",     per_tnut_load,            "<=", LIM_TNUT,      "N"))
for r in results:
    print(r)

n_fail = sum(1 for r in results if r.startswith("[FAIL]"))
n_pass = sum(1 for r in results if r.startswith("[PASS]"))
print()
print(f"SUMMARY: {n_pass} PASS / {n_fail} FAIL out of {len(results)}")
print()
print("FEA bracket-level cross-check:")
# Bracket-level pass/fail: bracket FEA von Mises must be < 6063-T5 yield/SF=170/2=85 MPa
print(f"  Bracket peak vM ({max_vm:.1f} MPa) vs 6063-T5 yield/SF (85 MPa): "
      f"{'PASS' if max_vm <= 85.0 else 'FAIL (bracket undersized; agent expected to flag)'}")
print()
print("ENGINEERING CONCLUSION:")
print("  Per the spec, the agent is expected to flag undersized bracket selection.")
print("  Computed per-bracket force (1500 N) > catalog static (1470 N).")
print("  Computed per-bracket moment (60 N*m) > catalog (29.4 N*m).")
print("  Mitigation: add brackets, switch to diagonal brace, or shorten cantilever.")
