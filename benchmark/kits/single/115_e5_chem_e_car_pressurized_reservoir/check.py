#!/usr/bin/env python3
"""Check Chem-E-Car reservoir results against requirements R1..R7.

Parses CalculiX .dat (and .frd) for stresses, frequencies, buckling factors.
Computes Barlow's max stress as analytical sanity check.
Compares to allowables and reports PASS/FAIL/SKIP.
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
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DAT  = os.path.join(HERE, 'model.dat')
FRD  = os.path.join(HERE, 'model.frd')

# -----------------------------------------------------------------
# Geometry & material (from spec)
# -----------------------------------------------------------------
ID_mm   = 100.0
t_mm    = 3.0
L_mm    = 180.0          # cylinder straight
R_dome  = 50.0           # dome inner radius
rho_kg_m3 = 2700.0
E_GPa   = 68.9
SY_MPa  = 276.0          # yield
allow_R1_MPa = 138.0     # LC1 proof, yield/2
allow_R2_MPa = 92.0      # LC2 operating, yield/3
allow_R5_MPa = 183.0     # LC3 thermal, derated
modal_R3_min_Hz = 250.0
buckle_R4_min   = 3.0
mass_R6_max_kg  = 0.5
volume_R7_min_mL = 625.0

P_LC1_MPa = 1.034
P_LC2_MPa = 0.689
P_LC3_MPa = 0.900
P_ext_MPa = 0.101        # external collapse reference

# -----------------------------------------------------------------
# Analytical Barlow / thin-wall checks
# -----------------------------------------------------------------
r_mid = (ID_mm + t_mm) / 2.0  # 51.5 mm
def hoop(P): return P * r_mid / t_mm           # cylinder hoop
def long_(P): return P * r_mid / (2.0 * t_mm)  # cylinder longitudinal
def dome(P): return P * (R_dome + t_mm/2.0) / (2.0 * t_mm)  # sphere membrane

# von Mises in cyl wall under biaxial tension (sigma_h, sigma_l, 0)
def vm_cyl(P):
    sh = hoop(P); sl = long_(P)
    return math.sqrt(sh*sh + sl*sl - sh*sl)

# -----------------------------------------------------------------
# Mass & volume
# -----------------------------------------------------------------
# Wall volume of cylinder (annulus) + 2 hemispheres (shell)
import math as _m
ID = ID_mm/1000.0; t = t_mm/1000.0; L = L_mm/1000.0
OD = ID + 2*t
V_cyl_wall = _m.pi/4 * (OD**2 - ID**2) * L
R_in = R_dome/1000.0; R_out = R_in + t
V_hemi_wall = 2 * (2/3.0)*_m.pi*(R_out**3 - R_in**3)  # two hemi caps total
V_wall_total_m3 = V_cyl_wall + V_hemi_wall
mass_kg = V_wall_total_m3 * rho_kg_m3

# Internal volume
V_int_m3 = _m.pi/4 * ID**2 * L + 2*(2/3.0)*_m.pi*R_in**3
V_int_mL = V_int_m3 * 1e6

# -----------------------------------------------------------------
# Parse .dat for max von Mises per static step
# -----------------------------------------------------------------
def parse_dat_stress_vm(dat_path):
    """Return dict step -> max vM from element stress prints (S xx,yy,zz,xy,xz,yz).

    Sections begin with a header line containing 'stresses' and 'sxx' and a
    'time  0.NNNNE+01' suffix giving the integer step number.
    """
    if not os.path.exists(dat_path):
        return {}
    results = {}
    cur_step = None
    in_block = False
    rows_in_block = 0
    with open(dat_path) as f:
        for line in f:
            if 'stresses' in line and 'sxx' in line:
                m = re.search(r'time\s+([-\d.E+]+)', line)
                cur_step = int(round(float(m.group(1)))) if m else None
                in_block = True
                rows_in_block = 0
                continue
            if in_block:
                if line.strip() == '':
                    # blank line: header gap if before any data, otherwise end of block
                    if rows_in_block > 0:
                        in_block = False
                    continue
                parts = line.split()
                if len(parts) < 8:
                    if rows_in_block > 0:
                        in_block = False
                    continue
                try:
                    int(parts[0]); int(parts[1])
                    sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                    sxy = float(parts[5]); sxz = float(parts[6]); syz = float(parts[7])
                except ValueError:
                    if rows_in_block > 0:
                        in_block = False
                    continue
                rows_in_block += 1
                vm = math.sqrt(0.5*((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2)
                               + 3*(sxy*sxy + sxz*sxz + syz*syz))
                if cur_step is not None:
                    if cur_step not in results or vm > results[cur_step]:
                        results[cur_step] = vm
    return results

# -----------------------------------------------------------------
# Parse FRD for stresses (more reliable than .dat for shells)
# -----------------------------------------------------------------
def parse_frd_max_vm(frd_path):
    """Walk FRD looking for STRESS records. Returns list of (step_idx, max_vm)."""
    if not os.path.exists(frd_path):
        return []
    out = []  # list of dicts {step, incr, max_vm}
    with open(frd_path) as f:
        cur_step = None
        cur_max  = None
        in_stress = False
        # FRD format uses "  100C..." header lines for fields
        for line in f:
            s = line.rstrip('\n')
            # block header: " 100CL  101..." with stepnumber? In CCX, time is in header card "1PSTEP".
            if s.startswith('    1PSTEP'):
                # finalize previous
                if cur_step is not None and cur_max is not None:
                    out.append((cur_step, cur_max))
                cur_step = (cur_step or 0) + 1
                cur_max  = None
                in_stress = False
                continue
            if ' STRESS ' in s.upper() or s.strip().startswith('-4  STRESS'):
                in_stress = True
                continue
            if in_stress and s.startswith(' -3'):
                in_stress = False
                continue
            if in_stress and s.startswith(' -1'):
                # data line: " -1  nodeid  sxx syy szz sxy syz szx"
                parts = s.split()
                # parts: ['-1', nodeid, sxx, syy, szz, sxy, syz, szx]
                if len(parts) >= 8:
                    try:
                        sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                        sxy = float(parts[5]); syz = float(parts[6]); szx = float(parts[7])
                    except ValueError:
                        continue
                    vm = math.sqrt(0.5*((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2)
                                   + 3*(sxy*sxy + syz*syz + szx*szx))
                    if cur_max is None or vm > cur_max:
                        cur_max = vm
        if cur_step is not None and cur_max is not None:
            out.append((cur_step, cur_max))
    return out

# -----------------------------------------------------------------
# Parse FRD for natural frequencies
# -----------------------------------------------------------------
def parse_frd_freqs(frd_path):
    """Return list of natural frequencies (Hz) found in PMODE/PSTEP cards."""
    freqs = []
    if not os.path.exists(frd_path):
        return freqs
    with open(frd_path) as f:
        for line in f:
            # CCX writes "100CL101..." record with frequency in card '1PMODE' or in header.
            # Easier path: lines beginning with "    1PSTEP" carry stepnum, eigenvalue (rad/s)^2 etc.
            # But the actual eigenfrequency in Hz is on the 100C..D...records at column positions.
            # Use pattern: lines like "    1P GK     <stepnum>  <freq_rad/s_squared>  <freq_Hz>"
            if line.startswith('    1PGM'):
                # 1PGM = generalized mass
                pass
            if line.startswith('    1PHID'):
                # PHID = frequency in Hz?
                parts = line.split()
                # parts: ['1PHID', stepnum, freq]
                if len(parts) >= 3:
                    try:
                        freqs.append(float(parts[2]))
                    except ValueError:
                        pass
    return freqs

# -----------------------------------------------------------------
# Parse .dat for eigenfrequencies (CCX writes them under 'EIGENVALUE OUTPUT')
# -----------------------------------------------------------------
def parse_dat_freqs(dat_path):
    """Parse frequencies (cycles/time = Hz) from CCX *FREQUENCY output.

    The header is:
       MODE NO    EIGENVALUE                       FREQUENCY
                                         REAL PART            IMAGINARY PART
                               (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

    Data rows have 5 columns: mode, eigenvalue, omega_rad, freq_Hz, imag_part.
    Rigid body modes have negative eigenvalues and zero freq.
    """
    if not os.path.exists(dat_path):
        return []
    freqs = []
    with open(dat_path) as f:
        text = f.read()
    # locate the eigenvalue table header
    headers = list(re.finditer(r'MODE NO\s+EIGENVALUE\s+FREQUENCY', text))
    for hdr in headers:
        # data lines follow after the units line; read until blank line / next text
        start = hdr.end()
        # skip header lines (3 of them: REAL/IMAG, (RAD..), blank)
        rest = text[start:]
        # accumulate lines that match the 5-column data pattern
        for line in rest.splitlines():
            s = line.strip()
            if not s:
                # tolerate blank lines inside header section
                if freqs:  # if we've started capturing, blank ends table
                    break
                continue
            if s.startswith('REAL') or s.startswith('('):
                continue
            mm = re.match(r'(\d+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s+([-\d.E+]+)\s*$', s)
            if mm:
                try:
                    eigenval = float(mm.group(2))
                    fhz = float(mm.group(4))
                    if eigenval > 0 and fhz > 1e-3:
                        freqs.append(fhz)
                except ValueError:
                    pass
            else:
                # not a data row; if we already collected some, stop
                if freqs:
                    break
        if freqs:
            break
    return freqs

# -----------------------------------------------------------------
# Parse .dat for buckling factors
# -----------------------------------------------------------------
def parse_dat_buckle(dat_path):
    """Parse buckling factors from CCX *BUCKLE output.

    Output table:
      MODE NO       BUCKLING
                     FACTOR
          1   0.9483552E+00
          2   ...
    """
    if not os.path.exists(dat_path):
        return []
    factors = []
    with open(dat_path) as f:
        text = f.read()
    m = re.search(r'MODE NO\s+BUCKLING\s+FACTOR(.*?)(?:\n\s*\n[^\d]|\Z)', text, re.DOTALL)
    if not m:
        return factors
    block = m.group(1)
    for line in block.splitlines():
        mm = re.match(r'\s*(\d+)\s+([-\d.E+]+)\s*$', line)
        if mm:
            try:
                factors.append(float(mm.group(2)))
            except ValueError:
                pass
    return factors

# -----------------------------------------------------------------
# Run analyses
# -----------------------------------------------------------------
print('=' * 70)
print('Chem-E-Car Pressurized Reservoir Verification')
print('=' * 70)

print()
print('-- Geometry / mass / volume (analytical) --')
print(f'Cylinder ID/t/L      : {ID_mm} / {t_mm} / {L_mm} mm')
print(f'Mid-surface radius   : {r_mid:.2f} mm')
print(f'Wall mass (analyt.)  : {mass_kg*1000:.1f} g  (= {mass_kg:.4f} kg)')
print(f'Internal volume      : {V_int_mL:.1f} mL')

print()
print('-- Barlow / thin-wall analytical stresses --')
for label, P in [('LC1 proof 1.034 MPa', P_LC1_MPa),
                 ('LC2 operating 0.689 MPa', P_LC2_MPa),
                 ('LC3 thermal 0.900 MPa', P_LC3_MPa)]:
    print(f'  {label}: hoop={hoop(P):.2f}  long={long_(P):.2f}  dome={dome(P):.2f}  vM_cyl={vm_cyl(P):.2f}  [MPa]')

# Parse FEA
print()
print('-- Parsing CalculiX results --')

dat_vm = parse_dat_stress_vm(DAT)
print(f'.dat per-step max vM (incl. modal/buckle steps): {dat_vm}')

frd_vm_list = parse_frd_max_vm(FRD)
print(f'.frd per-step max vM (sequential): {frd_vm_list}')

dat_freqs = parse_dat_freqs(DAT)
print(f'.dat parsed freqs (Hz): {dat_freqs[:10]}')

frd_freqs = parse_frd_freqs(FRD)
print(f'.frd parsed freqs (Hz): {frd_freqs[:10]}')

dat_buckle = parse_dat_buckle(DAT)
print(f'.dat buckling factors: {dat_buckle[:5]}')

# Pick best stress source per step
# step 1=LC1, step 2=LC2, step 3=LC3
def vm_for_step(stepnum):
    if stepnum in dat_vm:
        return dat_vm[stepnum]
    return None

vm_LC1 = vm_for_step(1)
vm_LC2 = vm_for_step(2)
vm_LC3 = vm_for_step(3)

print(f'vM LC1 = {vm_LC1}  vM LC2 = {vm_LC2}  vM LC3 = {vm_LC3}')

# Frequencies: prefer dat
freqs_use = dat_freqs if dat_freqs else frd_freqs
freqs_use = [f for f in freqs_use if f > 1.0]  # drop 0/rigid-body modes
freqs_use = sorted(set(round(f, 3) for f in freqs_use))
first5 = freqs_use[:5] if freqs_use else []

# Buckling first factor (positive smallest). The submission-agnostic
# template runs 3x*STATIC + 1x*FREQUENCY only (no *BUCKLE) because shell
# vs solid buckle eigenvalues for closed pressure vessels are unreliable
# in CCX 2.22; R4 is evaluated closed-form via Windenburg-Trilling /
# Bresse below.
buckle_factors = [f for f in dat_buckle if abs(f) > 1e-6]
first_buckle_FEA = None
if buckle_factors:
    pos = [f for f in buckle_factors if f > 0]
    first_buckle_FEA = min(pos) if pos else None

# Analytical buckling check using Windenburg-Trilling for short cylinder + Bresse for long
# (CCX shell buckling for closed pressure vessels can be unreliable; analytical is the
# primary R4 acceptance criterion and FEA is reported as cross-check.)
# Windenburg-Trilling (US Navy DTMB) formula for collapse of cylinder w/ closed ends:
#   P_cr = 2.6 * E * (t/Do)^2.5 / (L/Do - 0.45*(t/Do)^0.5)
# Valid for thin-wall, short-to-medium L/Do. Units: E in same as P_cr.
Do_mm = ID_mm + 2*t_mm
E_MPa = E_GPa * 1e3
ratio_t_Do = t_mm / Do_mm
ratio_L_Do = L_mm / Do_mm
denom = ratio_L_Do - 0.45 * math.sqrt(ratio_t_Do)
P_cr_WT_MPa = 2.6 * E_MPa * (ratio_t_Do)**2.5 / denom

# Bresse (long-tube) limit:
nu = 0.33
P_cr_Bresse_MPa = 2.0 * E_MPa / (1 - nu**2) * (t_mm / Do_mm)**3

# Use the smaller (governing)
P_cr_analytical_MPa = min(P_cr_WT_MPa, P_cr_Bresse_MPa) if P_cr_Bresse_MPa < P_cr_WT_MPa else P_cr_WT_MPa
# For external pressure 0.101 MPa, the SF:
SF_buckle_analytical = P_cr_analytical_MPa / P_ext_MPa

# Use FEA if positive and reasonable; else fall back to analytical
if first_buckle_FEA is not None and 0.1 < first_buckle_FEA * (P_ref_for_buckle:=1.0) / P_ext_MPa < 1e6:
    # FEA was run with P_ref=1.0 MPa external; eigenvalue * P_ref / P_ext gives SF
    SF_buckle_FEA = first_buckle_FEA * P_ref_for_buckle / P_ext_MPa
else:
    SF_buckle_FEA = None

# Choose primary: prefer analytical (more trustworthy for shell + closed vessel)
first_buckle = SF_buckle_analytical
buckle_source = 'analytical (Windenburg-Trilling)'

print()
print('-- Pass / fail evaluation --')
results = []

def report(rid, desc, value, op, limit, units=''):
    if value is None:
        results.append((rid, desc, 'SKIP', value, limit, units, 'no FEA data'))
        return
    if op == '<=':
        ok = value <= limit
    elif op == '>=':
        ok = value >= limit
    else:
        ok = False
    results.append((rid, desc, 'PASS' if ok else 'FAIL', value, limit, units, op))

report('R1', 'LC1 proof peak vM <= 138 MPa',          vm_LC1,        '<=', allow_R1_MPa, 'MPa')
report('R2', 'LC2 operating peak vM <= 92 MPa',       vm_LC2,        '<=', allow_R2_MPa, 'MPa')
report('R5', 'LC3 thermal peak vM <= 183 MPa',        vm_LC3,        '<=', allow_R5_MPa, 'MPa')
report('R3', 'first 5 nat. freqs >= 250 Hz',
       (min(first5) if first5 else None), '>=', modal_R3_min_Hz, 'Hz')
report('R4', 'first buckling factor >= 3.0',          first_buckle,  '>=', buckle_R4_min, '-')
report('R6', 'mass <= 0.5 kg',                        mass_kg,       '<=', mass_R6_max_kg, 'kg')
report('R7', 'internal volume >= 625 mL',             V_int_mL,      '>=', volume_R7_min_mL, 'mL')

print()
print(f'{"Req":<5} {"Status":<6} {"Value":>14}  {"Op":<3} {"Limit":>10}  {"Description"}')
print('-' * 80)
for r in results:
    rid, desc, st, v, lim, u, op = r
    vs = f'{v:.4g} {u}' if v is not None else 'N/A'
    ls = f'{lim:.4g} {u}'
    print(f'{rid:<5} {st:<6} {vs:>14}  {op:<3} {ls:>10}  {desc}')

print()
print(f'First 5 nat. freqs (Hz): {first5}')
print(f'Analytical P_cr (Windenburg-Trilling) = {P_cr_WT_MPa:.3f} MPa')
print(f'Analytical P_cr (Bresse long-tube)    = {P_cr_Bresse_MPa:.3f} MPa')
print(f'Governing P_cr_analytical             = {P_cr_analytical_MPa:.3f} MPa')
print(f'External pressure scenario            = {P_ext_MPa:.3f} MPa')
print(f'SF (analytical)                       = {SF_buckle_analytical:.2f}  [used for R4]')
print(f'CCX FEA buckle 1st factor (raw)       = {first_buckle_FEA}')
print(f'CCX FEA SF (if usable)                = {SF_buckle_FEA}')

# Overall
fails = [r for r in results if r[2] == 'FAIL']
skips = [r for r in results if r[2] == 'SKIP']
if fails:
    overall = 'FAIL'
elif skips:
    overall = 'PARTIAL'
else:
    overall = 'PASS'
print()
print(f'OVERALL: {overall}')
sys.exit(0 if overall == 'PASS' else (2 if overall == 'FAIL' else 1))
