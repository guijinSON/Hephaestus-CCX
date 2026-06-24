#!/usr/bin/env python3
"""
Coma Wars JP6 - check.py

Parses model.dat from CalculiX, computes max von Mises stress and max
displacement, then evaluates each requirement (R1..R4) defined in spec.json.

Pass/Fail:
  R1: OD          <= 20 mm
  R2: height      <= 60 mm
  R3: concentricity <= 0.02 mm  (manufacturing tolerance, declared)
  R4: max_vm_stress <= yield (LC1, 50 N lateral)

The mass-properties (Izz, CG height) are computed analytically (the spec
explicitly requires this is *not* FEA), so they are reported but only
verified against the >=50 g review flag.
"""

import json, math, os, sys, re

WORKDIR = os.path.dirname(os.path.abspath(__file__))

def parse_dat(path):
    """Return (max_vm_MPa, max_disp_mm)."""
    max_vm = 0.0
    max_disp = 0.0
    section = None
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s:
                # blank lines do NOT reset section (CCX dat has blanks inside sections)
                continue
            low = s.lower()
            if "stresses" in low and "elem" in low:
                section = "S"
                continue
            if "displacements" in low:
                section = "U"
                continue
            # header lines (S T E P, INCREMENT, etc.) - treat as section reset
            if (s.startswith("S T E P") or s.startswith("INCREMENT")
                or s.startswith("STEP") or "step" in low and "increment" in low):
                section = None
                continue
            parts = s.split()
            if section == "S":
                # Layout: elem, ipt, sxx, syy, szz, sxy, sxz, syz   (8 columns)
                if len(parts) >= 8:
                    try:
                        sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                        sxy = float(parts[5]); sxz = float(parts[6]); syz = float(parts[7])
                        vm = math.sqrt(0.5 * (
                            (sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2
                            + 6.0*(sxy*sxy + sxz*sxz + syz*syz)
                        ))
                        if vm > max_vm:
                            max_vm = vm
                    except ValueError:
                        pass
            elif section == "U":
                # Layout: node, ux, uy, uz
                if len(parts) >= 4:
                    try:
                        ux = float(parts[1]); uy = float(parts[2]); uz = float(parts[3])
                        d = math.sqrt(ux*ux + uy*uy + uz*uz)
                        if d > max_disp:
                            max_disp = d
                    except ValueError:
                        pass
    return max_vm, max_disp

def main():
    spec_path = os.path.join(WORKDIR, "spec.json")
    mp_path   = os.path.join(WORKDIR, "mass_properties.json")
    dat_path  = os.path.join(WORKDIR, "model.dat")

    with open(spec_path) as f:
        spec = json.load(f)
    with open(mp_path) as f:
        mp = json.load(f)

    max_vm_MPa, max_disp_mm = parse_dat(dat_path)

    # Material yield: brass C36000 cold-drawn ~ 310 MPa (use this for R4)
    Sy_MPa = 310.0
    SF = (Sy_MPa / max_vm_MPa) if max_vm_MPa > 0 else float("inf")

    results = []

    # R1: OD <= 20 mm
    OD = mp["OD_mm"]
    results.append({
        "id": "R1", "metric": "OD_mm",
        "value": OD, "limit": 20.0,
        "status": "PASS" if OD <= 20.0 else "FAIL"
    })
    # R2: height <= 60 mm
    H = mp["height_mm"]
    results.append({
        "id": "R2", "metric": "height_mm",
        "value": H, "limit": 60.0,
        "status": "PASS" if H <= 60.0 else "FAIL"
    })
    # R3: concentricity <= 0.02 mm   (manufacturing-declared, not FEA)
    conc = mp["concentricity_mm_assumed"]
    results.append({
        "id": "R3", "metric": "concentricity_mm",
        "value": conc, "limit": 0.02,
        "status": "PASS" if conc <= 0.02 else "FAIL"
    })
    # R4: max VM stress <= yield (1.0 of yield)
    results.append({
        "id": "R4", "metric": "max_vm_stress_MPa",
        "value": max_vm_MPa, "limit": Sy_MPa,
        "status": "PASS" if max_vm_MPa <= Sy_MPa else "FAIL",
        "safety_factor": SF
    })

    # 50 g review flag (informational, not pass/fail)
    mass_flag = "OK" if mp["mass_g"] <= 50.0 else "REVIEW"

    summary = {
        "id": spec["id"],
        "load_case": "LC1 (50 N lateral at widest diameter)",
        "material_body": "Brass C36000 (E=100 GPa, nu=0.34, Sy=310 MPa)",
        "mesh": {"nodes": mp["n_nodes"], "elements": mp["n_elements"], "type": "C3D8"},
        "mass_properties": {
            "mass_g": mp["mass_g"],
            "CG_height_mm": mp["CG_height_mm"],
            "polar_moment_Izz_kg_mm2": mp["polar_moment_Izz_kg_mm2"],
            "concentricity_mm": mp["concentricity_mm_assumed"],
            "mass_review_flag": mass_flag,
        },
        "fea_results": {
            "max_vm_MPa": max_vm_MPa,
            "max_disp_mm": max_disp_mm,
            "yield_MPa": Sy_MPa,
            "safety_factor_vs_yield": SF,
        },
        "requirements": results,
        "overall": "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL",
    }

    print(json.dumps(summary, indent=2))
    with open(os.path.join(WORKDIR, "check_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

if __name__ == "__main__":
    main()
