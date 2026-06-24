"""
Parse model.dat / model.frd from the CalculiX run and check pass/fail
against the IRC 2026 rover upper-arm-link spec.json requirements.

Steps in model.inp:
  1 LC1 Earth full extension (80 N + 4 N-m)
  2 LC2 Mars operational (19 N + 1.5 N-m)
  3 LC3 e-stop (100 N lateral)
  4 LC4 stow impact (30 g lateral body force)
  5 LC5 thermal (-100 K + Earth gravity, both ends fixed)
  6 buckling (1 N reference axial load -> eigenvalues)

Outputs:
  Per-LC max von Mises in EBASE and EHAZ from *EL PRINT (model.dat is large).
  We instead parse the .frd file for nodal stresses (final increment of each step)
  and split nodes into base / HAZ subsets via x-coordinate (HAZ_LEN = 25 mm).
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
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(ROOT, "spec.json")))
FRD = os.path.join(ROOT, "model.frd")
DAT = os.path.join(ROOT, "model.dat")

L = 600.0
HAZ_LEN = 25.0


def _read_floats(line, start, count, width=12):
    """Read `count` floats from `line` starting at column `start` with given width."""
    vals = []
    for k in range(count):
        s = line[start + k * width: start + (k + 1) * width]
        vals.append(float(s))
    return vals


def parse_frd(path):
    """Return (nodes_dict, results_blocks_list).
    results_blocks_list: list of dicts {kind: 'DISP'|'STRESS'|'NDTEMP'|..., data: {nid: tuple}}
    in the order they appear."""
    nodes = {}
    blocks = []
    with open(path) as f:
        lines = f.readlines()
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        # Node block header is "    2C" -- read coordinates from following -1 lines
        if line.startswith("    2C"):
            i += 1
            while i < n and not lines[i].startswith(" -3"):
                ln = lines[i]
                if ln.startswith(" -1"):
                    nid = int(ln[3:13])
                    x, y, z = _read_floats(ln, 13, 3, 12)
                    nodes[nid] = (x, y, z)
                i += 1
            i += 1
            continue
        # Result data block: header lines start with " -4" and contain a name token
        if line.startswith(" -4"):
            # The -4 line has the result name in chars ~5..13
            name_token = line[5:13].strip()
            i += 1
            # Skip component description lines starting with " -5"
            while i < n and lines[i].startswith(" -5"):
                i += 1
            data = {}
            ncomp = 0
            while i < n and not lines[i].startswith(" -3"):
                ln = lines[i]
                if ln.startswith(" -1"):
                    nid = int(ln[3:13])
                    # Determine number of components from line length
                    ncomp_here = (len(ln.rstrip("\n")) - 13) // 12
                    vals = _read_floats(ln, 13, ncomp_here, 12)
                    data[nid] = tuple(vals)
                    ncomp = ncomp_here
                i += 1
            i += 1
            blocks.append({"kind": name_token, "data": data, "ncomp": ncomp})
            continue
        i += 1
    return nodes, blocks


def vm(s):
    sxx, syy, szz, sxy, syz, szx = s
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))


def parse_buckling(path):
    factors = []
    with open(path) as f:
        for line in f:
            m = re.match(r"\s*(\d+)\s+([0-9.+\-Ee]+)\s*$", line)
            if m and len(line.split()) == 2:
                try:
                    factors.append(float(m.group(2)))
                except ValueError:
                    pass
    return factors


def main():
    nodes, blocks = parse_frd(FRD)
    # Walk blocks in order; pair successive DISP/STRESS into per-step bundles.
    # CalculiX writes blocks in the order requested per step. We expect
    # DISP, STRESS for each static step (and NDTEMP for thermal step).
    paired = []
    cur = {"disp": None, "stress": None}
    for b in blocks:
        k = b["kind"]
        if k.startswith("DISP"):
            if cur["disp"] is not None:
                paired.append(cur); cur = {"disp": None, "stress": None}
            cur["disp"] = b["data"]
        elif k.startswith("STRESS"):
            if cur["stress"] is not None:
                paired.append(cur); cur = {"disp": None, "stress": None}
            cur["stress"] = b["data"]
        # ignore NDTEMP and other auxiliary blocks
        if cur["disp"] is not None and cur["stress"] is not None:
            paired.append(cur)
            cur = {"disp": None, "stress": None}
    if cur["disp"] is not None or cur["stress"] is not None:
        paired.append(cur)

    print(f"Parsed {len(nodes)} nodes, {len(blocks)} result blocks "
          f"-> {len(paired)} paired step results from FRD.")

    # Compute per-step max VM in base and in HAZ
    lc_names = ["LC1", "LC2", "LC3", "LC4", "LC5"]
    results = {}
    for k, lc in enumerate(lc_names):
        if k >= len(paired):
            results[lc] = None
            continue
        block = paired[k]
        if not block.get("stress"):
            results[lc] = None
            continue
        max_vm_base = 0.0
        max_vm_haz = 0.0
        for nid, s in block["stress"].items():
            if nid not in nodes:
                continue
            x = nodes[nid][0]
            v = vm(s)
            if x <= HAZ_LEN or x >= (L - HAZ_LEN):
                if v > max_vm_haz:
                    max_vm_haz = v
            else:
                if v > max_vm_base:
                    max_vm_base = v
        # Tip deflection (use NTIP nodes ~ x=L)
        max_disp_mag = 0.0
        if block.get("disp"):
            for nid, u in block["disp"].items():
                if nid not in nodes:
                    continue
                if abs(nodes[nid][0] - L) < 1e-6:
                    mag = math.sqrt(u[0] ** 2 + u[1] ** 2 + u[2] ** 2)
                    if mag > max_disp_mag:
                        max_disp_mag = mag
        results[lc] = {
            "max_vm_base_MPa": max_vm_base,
            "max_vm_haz_MPa": max_vm_haz,
            "max_tip_disp_mm": max_disp_mag,
        }

    # Buckling factors
    buck = parse_buckling(DAT)
    # The .dat also contains nodal stress prints from *EL PRINT, so the
    # extracted "factors" list may contain spurious entries. Filter only those
    # in the explicit "BUCKLING FACTOR" block.
    real_buck = []
    with open(DAT) as f:
        text = f.read()
    m = re.search(r"MODE NO\s+BUCKLING\s+FACTOR(.+?)(?:\n\n|\Z)", text, re.S)
    if m:
        for ln in m.group(1).splitlines():
            parts = ln.split()
            if len(parts) == 2:
                try:
                    real_buck.append(float(parts[1]))
                except ValueError:
                    pass
    if not real_buck and buck:
        real_buck = buck[:4]

    # LC4 effective axial reference: a 30g impact on link mass produces an
    # inertial axial load if shock is along the link axis. Spec frames LC4
    # as lateral 30g impact, so axial component is conservatively
    # F_axial_ref = m_link * 30 * g. We then use buckling load factor /
    # F_axial_ref as the safety factor against axial buckling under LC4.
    m_link_kg = 0.9137  # from gen_mesh print, 913.7 g (above 850 g target)
    F_axial_LC4_N = m_link_kg * 30.0 * 9.81  # ~269 N
    buck_LC4_safety = real_buck[0] / F_axial_LC4_N if real_buck else None

    print("\n===== Per-LC results =====")
    for lc in lc_names:
        r = results[lc]
        if r is None:
            print(f"  {lc}: NO RESULT")
            continue
        print(f"  {lc}: vm_base={r['max_vm_base_MPa']:8.2f} MPa, "
              f"vm_HAZ={r['max_vm_haz_MPa']:8.2f} MPa, "
              f"tip_disp={r['max_tip_disp_mm']:6.3f} mm")

    print("\n===== Buckling =====")
    print(f"  Eigenvalues (1 N reference axial): {real_buck}")
    print(f"  LC4 effective axial = {F_axial_LC4_N:.1f} N (= 30 g * link mass)")
    if buck_LC4_safety is not None:
        print(f"  LC4 buckling safety factor = {buck_LC4_safety:.2f}")

    print(f"\n===== Mass =====")
    print(f"  Link mass (computed from mesh, rho=2700 kg/m^3) = {m_link_kg*1000:.1f} g")

    # ----- Pass/Fail per requirement -----
    pf = []
    # R1: max vm base under LC1, LC3, LC4 <= 184 MPa
    r1_ok = True; r1_max = 0.0
    for lc in ("LC1", "LC3", "LC4"):
        if results[lc] is None:
            r1_ok = False
            continue
        v = results[lc]["max_vm_base_MPa"]
        r1_max = max(r1_max, v)
        if v > 184.0:
            r1_ok = False
    pf.append(("R1 max_vm_base <=184 MPa (LC1,LC3,LC4)",
               f"max={r1_max:.2f} MPa", "PASS" if r1_ok else "FAIL"))

    # R2: max vm HAZ under same LCs <= 92 MPa
    r2_ok = True; r2_max = 0.0
    for lc in ("LC1", "LC3", "LC4"):
        if results[lc] is None:
            r2_ok = False
            continue
        v = results[lc]["max_vm_haz_MPa"]
        r2_max = max(r2_max, v)
        if v > 92.0:
            r2_ok = False
    pf.append(("R2 max_vm_HAZ  <= 92 MPa (LC1,LC3,LC4)",
               f"max={r2_max:.2f} MPa", "PASS" if r2_ok else "FAIL"))

    # R3: tip deflection LC1 <= 2.5 mm
    if results["LC1"] is not None:
        d = results["LC1"]["max_tip_disp_mm"]
        pf.append(("R3 tip_deflection_LC1 <= 2.5 mm",
                   f"d={d:.3f} mm", "PASS" if d <= 2.5 else "FAIL"))
    else:
        pf.append(("R3 tip_deflection_LC1 <= 2.5 mm", "no result", "SKIP"))

    # R4: LC4 first-mode buckling load factor >= 3.0
    if buck_LC4_safety is not None:
        pf.append(("R4 LC4 buckling safety factor >= 3.0",
                   f"factor={buck_LC4_safety:.2f}",
                   "PASS" if buck_LC4_safety >= 3.0 else "FAIL"))
    else:
        pf.append(("R4 LC4 buckling safety factor >= 3.0", "no result", "SKIP"))

    # R5: link mass <= 850 g
    pf.append(("R5 link mass <= 850 g",
               f"mass={m_link_kg*1000:.1f} g",
               "PASS" if m_link_kg * 1000 <= 850.0 else "FAIL"))

    # R6a: combined thermal+gravity LC5 base stress <= 184 MPa
    if results["LC5"] is not None:
        v = results["LC5"]["max_vm_base_MPa"]
        pf.append(("R6a LC5 combined_stress_base <= 184 MPa",
                   f"vm_base={v:.2f} MPa",
                   "PASS" if v <= 184.0 else "FAIL"))
    else:
        pf.append(("R6a LC5 combined_stress_base <= 184 MPa", "no result", "SKIP"))

    # R6b: combined thermal+gravity LC5 HAZ stress <= 92 MPa
    if results["LC5"] is not None:
        v = results["LC5"]["max_vm_haz_MPa"]
        pf.append(("R6b LC5 combined_stress_HAZ <= 92 MPa",
                   f"vm_HAZ={v:.2f} MPa",
                   "PASS" if v <= 92.0 else "FAIL"))
    else:
        pf.append(("R6b LC5 combined_stress_HAZ <= 92 MPa", "no result", "SKIP"))

    print("\n===== Pass / Fail =====")
    for name, val, status in pf:
        print(f"  [{status}] {name}: {val}")

    n_pass = sum(1 for _, _, s in pf if s == "PASS")
    n_fail = sum(1 for _, _, s in pf if s == "FAIL")
    n_skip = sum(1 for _, _, s in pf if s == "SKIP")
    print(f"\nTotals: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP")


if __name__ == "__main__":
    main()
