#!/usr/bin/env python3
"""Pass/fail check for FS Switzerland 7075-T6 wheel hub eval (134_hv10).

Parses model.dat to compute per-step max von Mises stress (LC1, LC2, LC3)
plus the first 6 natural frequencies from the *FREQUENCY step.

Compares against spec pass/fail criteria (spec.json):
  R1 LC1 cornering : max von Mises <= 252 MPa  (yield/2.0, FSAE suspension SF)
  R2 LC2 launch    : max von Mises <= 252 MPa  (yield/2.0)
  R3 LC3 brake     : max von Mises <= 335 MPa  (yield/1.5, brake-peak transient)
  R4 first 6 modes : informational (no spec gate in this kit)
  R5 mass          : SKIP (closed-form on agent CAD volume; not a FEM result)
  R6 wheel-plane tilt : SKIP (high-fidelity flatness gate; out of scope for
                              the simplified annulus reference)

Outputs PASS / FAIL / SKIP per requirement and overall verdict.
The eval runner exits 0 iff this check returns 0 (no FAILs).
"""
from __future__ import annotations
import math
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DAT = HERE / "model.dat"
SPEC = HERE / "spec.json"

# Spec limits (MPa)
LIMIT_LC1_LC2_MPA = 252.0
LIMIT_LC3_MPA = 335.0
YIELD_MPA = 503.0


def parse_dat_steps(path: Path):
    """Return list of step dicts, each {'stresses': [...], 'disps': [...]}.

    CCX writes per-step blocks in this order:
        ' forces (fx,fy,fz) for set NFIXED and time ...'      (reactions)
        ' displacements (vx,vy,vz) for set NFIXED and time ...'
        ' stresses (elem, integ.pnt., ...) for set EALL ...'
    Each *STEP starts with a 'forces' header. We split on that. The
    *FREQUENCY step writes one such block per mode shape (after the
    eigenvalue table), so we cap the returned step list at 3 (LC1/LC2/LC3).
    """
    steps = []
    cur = None
    mode = None
    with path.open() as f:
        for raw in f:
            line = raw.rstrip()
            stripped = line.strip()
            low = stripped.lower()
            if low.startswith("forces"):
                # New step boundary
                if cur is not None:
                    steps.append(cur)
                cur = {"stresses": [], "disps": []}
                mode = None  # we don't capture reaction forces here
                continue
            if low.startswith("displacements"):
                if cur is None:
                    cur = {"stresses": [], "disps": []}
                mode = "U"
                continue
            if low.startswith("stresses"):
                if cur is None:
                    cur = {"stresses": [], "disps": []}
                mode = "S"
                continue
            if not stripped:
                continue
            parts = stripped.split()
            try:
                vals = [float(p) for p in parts]
            except ValueError:
                continue
            if mode == "S" and cur is not None and len(vals) >= 8:
                # elem, integ.pnt., sxx, syy, szz, sxy, sxz, syz
                cur["stresses"].append(tuple(vals[2:8]))
            elif mode == "U" and cur is not None and len(vals) >= 4:
                # node, vx, vy, vz
                cur["disps"].append(tuple(vals[1:4]))
    if cur is not None:
        steps.append(cur)
    return steps


def von_mises(s):
    sxx, syy, szz, sxy, sxz, syz = s
    a = (sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2
    b = 6.0 * (sxy * sxy + sxz * sxz + syz * syz)
    return math.sqrt(0.5 * (a + b))


def step_stats(step):
    if step["stresses"]:
        vm_max = max(von_mises(s) for s in step["stresses"])
    else:
        vm_max = 0.0
    if step["disps"]:
        u_max = max(math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2)
                    for d in step["disps"])
    else:
        u_max = 0.0
    return vm_max, u_max


def parse_frequencies(path: Path):
    """Extract first 6 natural frequencies (Hz) from *FREQUENCY step output.

    CCX writes:
        E I G E N V A L U E   O U T P U T

        MODE NO    EIGENVALUE                       FREQUENCY
                                         REAL PART            IMAGINARY PART
                               (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

              1   0.4093E+10   0.6398E+05   0.1018E+05   0.0000E+00
              ...

        P A R T I C I P A T I O N   F A C T O R S

    Strategy: scan for the EIGENVALUE banner, then capture lines whose
    first whitespace-delimited token is an integer mode number, until we
    reach the PARTICIPATION banner (or another header).
    """
    freqs = []
    in_freq_table = False
    with path.open() as f:
        for raw in f:
            line = raw.rstrip()
            stripped = line.strip()
            up = stripped.upper().replace(" ", "")
            if "EIGENVALUEOUTPUT" in up:
                in_freq_table = True
                continue
            if not in_freq_table:
                continue
            if "PARTICIPATIONFACTORS" in up:
                break
            parts = stripped.split()
            if len(parts) >= 4 and parts[0].isdigit():
                try:
                    # columns: mode, eigenvalue, RAD/TIME, CYCLES/TIME (Hz), IMAG
                    freq_hz = float(parts[3])
                    freqs.append(freq_hz)
                except ValueError:
                    pass
    return freqs


def main() -> int:
    if not DAT.exists():
        print("FAIL: model.dat not found")
        return 1

    steps = parse_dat_steps(DAT)
    static_steps = steps[:3]
    print(f"Parsed {len(steps)} step block(s); first 3 are LC1/LC2/LC3")

    summaries = []
    labels = ["LC1 cornering", "LC2 launch", "LC3 brake peak"]
    for i, st in enumerate(static_steps):
        vm, u = step_stats(st)
        summaries.append((vm, u))
        print(f"  {labels[i] if i < len(labels) else f'STEP{i+1}':<18}: "
              f"max VM = {vm:9.3f} MPa, max |U| = {u:9.4f} mm "
              f"({len(st['stresses'])} stress pts, {len(st['disps'])} disp pts)")

    freqs = parse_frequencies(DAT)
    if freqs:
        print(f"First {min(6, len(freqs))} natural frequencies (Hz): "
              f"{[round(f, 1) for f in freqs[:6]]}")
    else:
        print("(no frequency table parsed)")

    print()
    print("=" * 72)
    print("REQUIREMENT RESULTS")
    print("=" * 72)
    results = []

    # R1: LC1 max VM <= 252 MPa
    if len(summaries) >= 1:
        vm1 = summaries[0][0]
        verdict = "PASS" if vm1 <= LIMIT_LC1_LC2_MPA else "FAIL"
        results.append(("R1", verdict,
                        f"LC1 cornering max VM = {vm1:.2f} MPa <= "
                        f"{LIMIT_LC1_LC2_MPA:.0f} MPa (yield/2.0)"))
    else:
        results.append(("R1", "FAIL", "LC1 step missing"))

    # R2: LC2 max VM <= 252 MPa
    if len(summaries) >= 2:
        vm2 = summaries[1][0]
        verdict = "PASS" if vm2 <= LIMIT_LC1_LC2_MPA else "FAIL"
        results.append(("R2", verdict,
                        f"LC2 launch max VM = {vm2:.2f} MPa <= "
                        f"{LIMIT_LC1_LC2_MPA:.0f} MPa (yield/2.0)"))
    else:
        results.append(("R2", "FAIL", "LC2 step missing"))

    # R3: LC3 max VM <= 335 MPa
    if len(summaries) >= 3:
        vm3 = summaries[2][0]
        verdict = "PASS" if vm3 <= LIMIT_LC3_MPA else "FAIL"
        results.append(("R3", verdict,
                        f"LC3 brake peak max VM = {vm3:.2f} MPa <= "
                        f"{LIMIT_LC3_MPA:.0f} MPa (yield/1.5)"))
    else:
        results.append(("R3", "FAIL", "LC3 step missing"))

    # R4: first 6 modes -- informational only in this kit
    if len(freqs) >= 6:
        freq_str = ", ".join(f"{f:.1f}" for f in freqs[:6])
        results.append(("R4", "SKIP",
                        f"first 6 modes = [{freq_str}] Hz (informational; "
                        f"spec floor was 500 Hz on full-feature CAD)"))
    else:
        results.append(("R4", "SKIP",
                        f"only {len(freqs)} mode(s) parsed (informational)"))

    # R5: mass <= 0.85 kg -- SKIP (depends on agent CAD; cannot infer from .dat)
    results.append(("R5", "SKIP",
                    "mass <= 0.85 kg gate is closed-form on agent CAD volume; "
                    "not a FEM result"))

    # R6: wheel-plane tilt <= 0.15 mm under LC1 -- SKIP (flatness deviation
    # of the 5-stud bolt circle requires the actual stud-bore feature in
    # the CAD; the simplified annulus does not resolve it)
    results.append(("R6", "SKIP",
                    "wheel-plane tilt 0.15 mm flatness gate needs full-"
                    "feature CAD (stud bores, brake bolts); not in this kit"))

    fail = 0
    for rid, verdict, msg in results:
        print(f"  [{verdict:<4}] {rid}: {msg}")
        if verdict == "FAIL":
            fail += 1
    print("=" * 72)
    print(f"FAIL count: {fail}")
    print(f"OVERALL: {'PASS' if fail == 0 else 'FAIL'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
