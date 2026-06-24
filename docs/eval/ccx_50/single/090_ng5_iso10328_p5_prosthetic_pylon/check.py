#!/usr/bin/env python3
"""Parse CalculiX results (model.frd, model.dat) and evaluate against
ISO 10328 P5 pass/fail criteria from spec.json.

Criteria:
  R1  max von Mises stress (LC1, LC2) <= 276 MPa (Al 6061-T6 yield)
  R2  principal static toe   force >= 3360 N (verified via reaction force)
  R3  principal static heel  force >= 4000 N (verified via reaction force)
  R4  fatigue life cycles >= 3e6 (LC3, LC4)  -> SKIP (CalculiX has no S-N solver)
  R5  assembly mass <= 400 g (sum of pylon-tube mass + adapter allowance)
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

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = json.load(open(os.path.join(HERE, "spec.json")))
FRD  = os.path.join(HERE, "model.frd")
DAT  = os.path.join(HERE, "model.dat")


def parse_frd_stress(frd_path):
    """Return list of dicts per step: max von Mises (MPa) and max |U| (mm).

    The .frd ASCII format groups results in -4 STRESS and -4 DISP blocks
    preceded by 100C lines giving the step time. We read all stress
    components and compute von Mises per node, taking the max within each
    step block.
    """
    steps = []
    with open(frd_path, "r") as f:
        lines = f.readlines()

    i = 0
    cur_block = None
    cur_step_time = None
    while i < len(lines):
        ln = lines[i]
        # Step header: "    100CL  101 ... <time> ..." starts a new result block
        if ln.startswith("    1PSTEP"):
            # ignore
            pass
        if ln.lstrip().startswith("100CL"):
            # parse time field (5th token)
            toks = ln.split()
            try:
                cur_step_time = float(toks[2])
            except (IndexError, ValueError):
                cur_step_time = None
        # Detect data block type
        m = re.match(r"\s*-4\s+(\S+)", ln)
        if m:
            blk = m.group(1)
            cur_block = blk
            data = []
            j = i + 1
            # skip -5 header lines
            while j < len(lines) and lines[j].lstrip().startswith("-5"):
                j += 1
            # read -1 nodal records
            while j < len(lines) and lines[j].lstrip().startswith("-1"):
                # FRD records are fixed-width: " -1<10sNODE><F1><F2>..." where
                # each Fk is 12 chars (E12.5). Adjacent negative numbers can
                # run together when split by whitespace, so slice columns.
                ln_j = lines[j].rstrip("\n")
                # column 0..2 = ' -1', 3..12 = node id (10 chars), then 12-wide
                vals = []
                pos = 13
                while pos + 12 <= len(ln_j):
                    tok = ln_j[pos:pos+12].strip()
                    if tok:
                        try:
                            vals.append(float(tok))
                        except ValueError:
                            break
                    pos += 12
                data.append(vals)
                j += 1
            steps.append({"block": blk, "time": cur_step_time, "data": data})
            i = j
            continue
        i += 1

    # Aggregate per step time: collect STRESS and DISP blocks
    by_time = {}
    for s in steps:
        t = s["time"]
        by_time.setdefault(t, {})[s["block"]] = s["data"]

    summary = []
    for t in sorted(by_time.keys() if all(k is not None for k in by_time.keys()) else by_time):
        b = by_time[t]
        vm_max = None
        if "STRESS" in b:
            # CalculiX FRD STRESS: SXX SYY SZZ SXY SYZ SZX
            vm_max = 0.0
            for v in b["STRESS"]:
                sxx, syy, szz, sxy, syz, szx = v[:6]
                vm = math.sqrt(0.5 * ((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2
                                       + 6.0 * (sxy*sxy + syz*syz + szx*szx)))
                if vm > vm_max:
                    vm_max = vm
        u_max = None
        if "DISP" in b:
            u_max = 0.0
            for v in b["DISP"]:
                u = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
                if u > u_max:
                    u_max = u
        summary.append({"time": t, "vm_max_MPa": vm_max, "u_max_mm": u_max})
    return summary


def parse_reactions(dat_path):
    """Return list of (step_time, Fx, Fy, Fz) from *NODE PRINT TOTALS=ONLY."""
    out = []
    with open(dat_path, "r") as f:
        text = f.read()
    # Find all 'total force' blocks
    pat = re.compile(r"total force.*?time\s+([0-9eE+\-.]+)\s*\n\s*\n\s*"
                     r"([0-9eE+\-.]+)\s+([0-9eE+\-.]+)\s+([0-9eE+\-.]+)")
    for m in pat.finditer(text):
        t = float(m.group(1))
        fx, fy, fz = float(m.group(2)), float(m.group(3)), float(m.group(4))
        out.append((t, fx, fy, fz))
    return out


def main():
    print("=" * 70)
    print("ISO 10328 P5 Prosthetic Pylon -- CalculiX verification")
    print("=" * 70)

    if not os.path.exists(FRD):
        print(f"FAIL: {FRD} not found (CalculiX run did not produce results)")
        sys.exit(1)

    steps = parse_frd_stress(FRD)
    rxn = parse_reactions(DAT)

    # Map step index to load case
    LC = ["LC1 (TC I, toe, 3360 N)", "LC2 (TC II, heel, 4000 N)"]
    F_target = [3360.0, 4000.0]

    print("\nPer-step results:")
    print(f"{'Step':<28s} {'time':<6s} {'vm_max [MPa]':<14s} {'u_max [mm]':<12s} {'|Fz| [N]':<10s}")
    for k, s in enumerate(steps):
        name = LC[k] if k < len(LC) else f"step{k+1}"
        fz = abs(rxn[k][3]) if k < len(rxn) else float("nan")
        vm = s["vm_max_MPa"]
        um = s["u_max_mm"]
        vm_s = f"{vm:.2f}" if vm is not None else "n/a"
        um_s = f"{um:.4f}" if um is not None else "n/a"
        print(f"{name:<28s} {s['time']:<6.2f} {vm_s:<14s} {um_s:<12s} {fz:<10.1f}")

    # ---------- Pass/Fail evaluation ----------
    results = []

    # Material & spec
    yield_MPa = SPEC["prompt"]["material"]["preferred"]["yield_strength_MPa"]   # 276
    target_mass = SPEC["requirements"]["pass_fail_criteria"][4]["limit_g"]      # 400

    # R1: von Mises <= yield for both LC1 and LC2
    vm_LC1 = steps[0]["vm_max_MPa"] if len(steps) >= 1 else None
    vm_LC2 = steps[1]["vm_max_MPa"] if len(steps) >= 2 else None
    if vm_LC1 is not None and vm_LC2 is not None:
        vm_max_overall = max(vm_LC1, vm_LC2)
        r1 = "PASS" if vm_max_overall <= yield_MPa else "FAIL"
        results.append(("R1", r1,
                        f"max VM = {vm_max_overall:.1f} MPa (LC1={vm_LC1:.1f}, "
                        f"LC2={vm_LC2:.1f}); limit = {yield_MPa} MPa"))
    else:
        results.append(("R1", "SKIP", "No stress data parsed"))

    # R2: principal static toe force >= 3360 N
    if len(rxn) >= 1:
        Fz1 = abs(rxn[0][3])
        r2 = "PASS" if Fz1 >= 3360.0 - 1e-3 else "FAIL"
        results.append(("R2", r2, f"|Fz| LC1 = {Fz1:.1f} N >= 3360 N"))
    else:
        results.append(("R2", "SKIP", "No reaction data"))

    # R3: principal static heel force >= 4000 N
    if len(rxn) >= 2:
        Fz2 = abs(rxn[1][3])
        r3 = "PASS" if Fz2 >= 4000.0 - 1e-3 else "FAIL"
        results.append(("R3", r3, f"|Fz| LC2 = {Fz2:.1f} N >= 4000 N"))
    else:
        results.append(("R3", "SKIP", "No reaction data"))

    # R4: fatigue 3e6 cycles -- requires non-FEA S-N solver (excluded by spec)
    results.append(("R4", "SKIP",
                    "Spec requires non-FEA S-N fatigue solver "
                    "(fe-safe/nCode); not in CalculiX 2.22 scope"))

    # R5: assembly mass <= 400 g.  Pylon tube only is computed from geometry;
    # adapter mass is approximated as a typical commercial pyramid+clamp pair.
    Do = 30.0; t = 2.5; L = 250.0
    Ro = Do/2; Ri = Ro - t
    vol_mm3 = math.pi * (Ro**2 - Ri**2) * L
    rho_g_per_mm3 = 2.7e-3
    m_pylon_g = vol_mm3 * rho_g_per_mm3
    m_pyramid_g = 60.0  # typical commercial 30mm Al pyramid male adapter
    m_clamp_g   = 90.0  # typical commercial 30mm Al female pyramid clamp
    m_total_g   = m_pylon_g + m_pyramid_g + m_clamp_g
    r5 = "PASS" if m_total_g <= target_mass else "FAIL"
    results.append(("R5", r5,
                    f"pylon={m_pylon_g:.1f} g + adapters~{m_pyramid_g+m_clamp_g:.0f} g "
                    f"= {m_total_g:.1f} g <= {target_mass} g"))

    print("\n" + "-" * 70)
    print("Pass/Fail summary")
    print("-" * 70)
    for rid, status, note in results:
        print(f"  {rid}: {status:5s} -- {note}")

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")
    print(f"\nTotals: PASS={n_pass}, FAIL={n_fail}, SKIP={n_skip} (of {len(results)})")
    overall = "FAIL" if n_fail else ("PASS" if n_pass else "SKIP")
    print(f"OVERALL: {overall}")


if __name__ == "__main__":
    main()
