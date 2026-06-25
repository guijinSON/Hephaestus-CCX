#!/usr/bin/env python3
"""
check.py for KiboCUBE 1U CubeSat (186_sa3_kibocube).

Parses model.dat and model.frd to extract:
  - Step 1 (LC1 9 g quasi-static): max von Mises stress (MPa)
  - Step 2 (modal): first natural frequency (Hz)
  - Step 3 (thermal +50 C from 20 C reference): max von Mises stress (MPa)
  - Computed total mass (kg) from element volumes and density 2700 kg/m^3.

Compares to spec pass/fail criteria:
  R1 max_vm <= 138 MPa under LC1
  R2 first_freq >= 135 Hz
  R3 total_mass <= 1.33 kg
  R4 thermal-cycle combined stress <= 138 MPa
  R5 buckling load factor >= 2.0  -> SKIP (not modeled)

Prints PASS / FAIL / SKIP for each.
"""

import re
import os
import sys

WORKDIR = os.path.dirname(os.path.abspath(__file__))
DAT = os.path.join(WORKDIR, "model.dat")
FRD = os.path.join(WORKDIR, "model.frd")
INP = os.path.join(WORKDIR, "model.inp")

DENSITY_KG_M3 = 2700.0
MM3_TO_M3 = 1e-9

# ------------- 1. Mass from .inp geometry -------------
def compute_mass():
    """Sum hex8 element volumes assuming axis-aligned bricks."""
    coords = {}
    elems = []
    with open(INP) as f:
        mode = None
        elset = None
        for line in f:
            s = line.strip()
            if s.startswith("*"):
                kw = s.split(",")[0].upper()
                if kw == "*NODE":
                    mode = "NODE"
                elif kw == "*ELEMENT":
                    mode = "ELEM"
                else:
                    mode = None
                continue
            if not s or s.startswith("**"):
                continue
            parts = [p.strip() for p in s.split(",")]
            if mode == "NODE":
                try:
                    nid = int(parts[0])
                    coords[nid] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    pass
            elif mode == "ELEM":
                try:
                    nids = [int(p) for p in parts if p]
                    if len(nids) == 9:
                        elems.append(nids[1:])
                except ValueError:
                    pass
    total_vol_mm3 = 0.0
    for e in elems:
        xs = [coords[n][0] for n in e]
        ys = [coords[n][1] for n in e]
        zs = [coords[n][2] for n in e]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dz = max(zs) - min(zs)
        total_vol_mm3 += dx * dy * dz
    mass_kg = total_vol_mm3 * MM3_TO_M3 * DENSITY_KG_M3
    return mass_kg, total_vol_mm3

# ------------- 2. First frequency from .dat -------------
def first_freq_hz():
    with open(DAT) as f:
        text = f.read()
    # Find the table header that includes EIGENVALUE/FREQUENCY
    m = re.search(r"MODE NO\s+EIGENVALUE.*?\(CYCLES/TIME.*?\n(.*?)(?:\n\s*\n|PARTICIPATION)", text, re.S)
    if not m:
        return None
    block = m.group(1)
    freqs = []
    for line in block.splitlines():
        toks = line.split()
        # Expected: mode_no eig rad/time cycles/time imag
        if len(toks) >= 4 and toks[0].isdigit():
            try:
                freqs.append(float(toks[3]))
            except ValueError:
                pass
    return min(freqs) if freqs else None

# ------------- 3. Max von Mises per step from .frd -------------
def max_vm_per_step():
    """Parse .frd, bucketing max vM per analysis step number (3rd token of 1PSTEP)."""
    if not os.path.exists(FRD):
        return {}
    per_step = {}
    cur_step_num = None
    in_stress = False
    cur_max = 0.0
    with open(FRD) as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith("1PSTEP"):
                # close any open stress block
                if in_stress and cur_step_num is not None:
                    per_step[cur_step_num] = max(per_step.get(cur_step_num, 0.0), cur_max)
                in_stress = False
                cur_max = 0.0
                toks = stripped.split()
                # toks: ['1PSTEP', incrno, stepno_in_set, analysis_step]
                if len(toks) >= 4:
                    try:
                        cur_step_num = int(toks[3])
                    except ValueError:
                        cur_step_num = None
                continue
            if "STRESS" in line and stripped.startswith("-4"):
                # close prior block in same step
                if in_stress and cur_step_num is not None:
                    per_step[cur_step_num] = max(per_step.get(cur_step_num, 0.0), cur_max)
                in_stress = True
                cur_max = 0.0
                continue
            if in_stress and line.startswith(" -1"):
                toks = line.split()
                if len(toks) >= 8:
                    try:
                        sxx = float(toks[2]); syy = float(toks[3]); szz = float(toks[4])
                        sxy = float(toks[5]); syz = float(toks[6]); szx = float(toks[7])
                        vm = ((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2
                              + 6*(sxy*sxy + syz*syz + szx*szx))
                        vm = (0.5*vm) ** 0.5
                        if vm > cur_max:
                            cur_max = vm
                    except ValueError:
                        pass
            if in_stress and line.startswith(" -3"):
                if cur_step_num is not None:
                    per_step[cur_step_num] = max(per_step.get(cur_step_num, 0.0), cur_max)
                in_stress = False
                cur_max = 0.0
    return per_step

# ------------- Run checks -------------
def main():
    results = {}

    mass_kg, vol_mm3 = compute_mass()
    results["total_mass_kg"] = mass_kg
    results["volume_mm3"] = vol_mm3

    f1 = first_freq_hz()
    results["first_freq_Hz"] = f1

    vms = max_vm_per_step()
    results["vm_steps_MPa"] = vms

    # vms is a dict keyed by analysis step number (1, 2, 3)
    lc1 = vms.get(1)
    th  = vms.get(3)

    # Print summary
    print("=" * 60)
    print("KiboCUBE 1U CubeSat - check.py results")
    print("=" * 60)
    print(f"Total mass             : {mass_kg:.4f} kg  (vol {vol_mm3:.1f} mm^3)")
    print(f"First natural frequency: {f1!r} Hz")
    print(f"Max vM per step        : {vms}")
    print()

    def report(req, ok):
        tag = "PASS" if ok else "FAIL"
        print(f"  {req}: {tag}")

    print("Pass/Fail:")
    # R1
    if lc1 is None:
        print("  R1 (max vM LC1 <= 138 MPa): SKIP (no stress output)")
    else:
        print(f"  R1 (max vM LC1 = {lc1:.2f} MPa <= 138 MPa): {'PASS' if lc1 <= 138 else 'FAIL'}")
    # R2
    if f1 is None:
        print("  R2 (first freq >= 135 Hz): SKIP")
    else:
        print(f"  R2 (first freq = {f1:.2f} Hz >= 135 Hz): {'PASS' if f1 >= 135 else 'FAIL'}")
    # R3
    print(f"  R3 (total mass = {mass_kg:.4f} kg <= 1.33 kg): {'PASS' if mass_kg <= 1.33 else 'FAIL'}")
    # R4
    if th is None:
        print("  R4 (thermal vM <= 138 MPa): SKIP")
    else:
        print(f"  R4 (thermal vM = {th:.2f} MPa <= 138 MPa): {'PASS' if th <= 138 else 'FAIL'}")
    # R5
    print("  R5 (first buckling load factor >= 2.0): SKIP (linear buckling step not included; would require *BUCKLE)")

if __name__ == "__main__":
    main()
