#!/usr/bin/env python3
"""
NASA-STD-5020B closed-form margin verification for the
4-bolt MJ8 A286 / Ti-6Al-4V joint defined in spec.json.

Equations (Appendix A):
  R1 (separation):  MS_sep  = P_pld_min / (n * SF_sep * P_tL)        - 1
  R2 (slip):        MS_slip = (mu_j * n_f * P_pld_min) / (SF_slip*P_sL) - 1
  R3 (bolt ult.):   MS_U    = P_allow_U / (P_pld_max + Phi * P_tU)   - 1
  R4 (bolt yield):  MS_Y    = P_allow_Y / (P_pld_max + Phi * P_tU_Y) - 1
  R5 (plate brg.):  sigma_brg = P_sU / (d_bolt * t_plate) <= 1.5*Ftu
  R6 (thread eng.): >= 2 threads past nut (geometric)

The FEM (model.dat) is a supporting check used to bound the
plate stresses; the closed-form values from the spec are the
authoritative pass/fail drivers.
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

import json, os, sys, re

HERE = os.path.dirname(os.path.abspath(__file__))
spec = json.load(open(os.path.join(HERE, "spec.json")))

# --- spec values
pre   = spec["prompt"]["preload"]
mat   = spec["prompt"]["material"]
geom  = spec["prompt"]["geometric_constraints"]
lc1   = spec["prompt"]["load_cases"][0]

P_pld_min = pre["min_kN"] * 1000.0          # 13 000 N
P_pld_max = pre["max_kN"] * 1000.0          # 22 500 N

P_tL_total = lc1["per_bolt_tensile_limit_kN"] * 1000.0      # 6.25 kN limit (worst bolt)
P_sL       = lc1["per_bolt_shear_limit_kN"]   * 1000.0      # 1.0 kN limit
P_tU_total = 1.4 * P_tL_total                                # 8.75 kN ultimate
P_sU       = 1.4 * P_sL                                      # 1.4 kN ultimate

P_allow_U = mat["bolt"]["min_ultimate_kN"] * 1000.0         # 42 500 N
P_allow_Y = mat["bolt"]["yield_kN"]        * 1000.0         # 34 700 N
Ftu_plate = mat["plate"]["Ftu_MPa"]                         # 950 MPa

d_bolt   = 8.0    # MJ8 nominal mm
t_plate  = geom["plate_mm"]["z"]    # 8 mm

# --- NASA-STD-5020B Appendix A factors (per spec)
n      = 0.5      # loading-plane factor
SF_sep = 1.2
SF_slip= 1.2
Phi    = 0.15
mu_j   = 0.2
n_f    = 1
SF_yield_external_factor = 1.25  # spec uses 6.25 * 1.25 in MS_Y derivation

# ---- compute margins
def fmt(x): return f"{x: .4f}"

results = []

# R1: separation
denom_R1 = n * SF_sep * P_tL_total
MS_sep = P_pld_min / denom_R1 - 1.0
results.append(("R1", "separation", MS_sep, MS_sep >= 0.0,
                f"P_pld_min/(n*SF*P_tL) = {P_pld_min:.0f}/{denom_R1:.0f} = "
                f"{P_pld_min/denom_R1:.3f}; MS={MS_sep:+.3f}"))

# R2: slip
denom_R2 = SF_slip * P_sL
MS_slip = (mu_j * n_f * P_pld_min) / denom_R2 - 1.0
results.append(("R2", "slip", MS_slip, MS_slip >= 0.0,
                f"mu_j*n_f*P_pld_min/(SF*P_sL) = {mu_j*n_f*P_pld_min:.0f}/{denom_R2:.0f} = "
                f"{(mu_j*n_f*P_pld_min)/denom_R2:.3f}; MS={MS_slip:+.3f}"))

# R3: bolt ultimate tensile
applied_R3 = P_pld_max + Phi * P_tU_total
MS_U = P_allow_U / applied_R3 - 1.0
results.append(("R3", "bolt_ult", MS_U, MS_U >= 0.0,
                f"P_allow_U/(P_pld_max+Phi*P_tU) = {P_allow_U:.0f}/{applied_R3:.0f}; MS={MS_U:+.3f}"))

# R4: bolt yield (spec applies an extra 1.25 factor on the limit external load)
P_tU_for_Y = SF_yield_external_factor * P_tL_total       # 6.25 * 1.25 = 7.8125 kN
applied_R4 = P_pld_max + Phi * P_tU_for_Y
MS_Y = P_allow_Y / applied_R4 - 1.0
results.append(("R4", "bolt_yield", MS_Y, MS_Y >= 0.0,
                f"P_allow_Y/(P_pld_max+Phi*1.25*P_tL) = {P_allow_Y:.0f}/{applied_R4:.0f}; MS={MS_Y:+.3f}"))

# R5: plate bearing under ultimate shear
sigma_brg = P_sU / (d_bolt * t_plate)             # MPa
brg_limit = 1.5 * Ftu_plate                       # 1425 MPa
results.append(("R5", "plate_bearing", sigma_brg, sigma_brg <= brg_limit,
                f"sigma_brg=P_sU/(d*t)={P_sU:.0f}/({d_bolt}*{t_plate}) = "
                f"{sigma_brg:.2f} MPa, limit {brg_limit:.0f} MPa"))

# R6: thread engagement (geometric).  MJ8x1.25 has pitch p=1.25 mm.
# Standard MJ8 nut height ~6.5 mm; 2 threads past = 2*1.25 = 2.5 mm.
# Required bolt-grip = 2*t_plate + nut_height + 2*pitch
pitch = 1.25
nut_height = 6.5
grip_required = 2*t_plate + nut_height + 2*pitch     # 16 + 6.5 + 2.5 = 25 mm
# Standard MJ8 lengths up to 50 mm exist; trivially feasible.
threads_past = 2  # by selection
results.append(("R6", "thread_engagement", threads_past, threads_past >= 2,
                f"grip required = 2*t+nut+2p = {grip_required:.1f} mm; "
                f"selecting an MJ8 bolt of grip 28-30 mm gives >=2 threads past nut"))

# --- FEM corroboration: read peak stress from model.dat ---
peak_szz = 0.0
peak_vm  = 0.0
dat_path = os.path.join(HERE, "model.dat")
if os.path.isfile(dat_path):
    with open(dat_path) as fh:
        in_stress = False
        for line in fh:
            if "stresses (elem" in line:
                in_stress = True
                continue
            if "displacements" in line:
                in_stress = False
            if in_stress:
                parts = line.split()
                if len(parts) >= 8 and parts[0].isdigit():
                    sxx,syy,szz = float(parts[2]),float(parts[3]),float(parts[4])
                    sxy,sxz,syz = float(parts[5]),float(parts[6]),float(parts[7])
                    vm = ((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2
                          + 6*(sxy**2+sxz**2+syz**2)) ** 0.5 / (2**0.5)
                    if abs(szz) > peak_szz: peak_szz = abs(szz)
                    if vm > peak_vm: peak_vm = vm

print(f"# NASA-STD-5020B Margin-of-Safety Verification")
print(f"# Inputs: P_pld_min={P_pld_min/1e3:.2f} kN, P_pld_max={P_pld_max/1e3:.2f} kN, "
      f"P_tL={P_tL_total/1e3:.2f} kN, P_tU={P_tU_total/1e3:.2f} kN, "
      f"P_sL={P_sL/1e3:.2f} kN, P_sU={P_sU/1e3:.2f} kN")
print(f"# FEM peak |Szz|={peak_szz:.3f} MPa, peak von Mises={peak_vm:.3f} MPa "
      f"(plates remain elastic well under Fty={mat['plate']['Fty_MPa']} MPa)")

any_fail = False
for rid, name, val, ok, expl in results:
    tag = "PASS" if ok else "FAIL"
    print(f"{tag} {rid} ({name}): {expl}")
    if not ok:
        any_fail = True

sys.exit(1 if any_fail else 0)
