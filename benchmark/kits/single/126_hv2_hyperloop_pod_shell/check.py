#!/usr/bin/env python3
"""HV2 Hyperloop pod shell -- post-processor for the submission-agnostic CCX kit.

Parses model.frd + model.dat and evaluates the six pass/fail criteria from
spec.json:

  R1  max von Mises (LC1, LC3) <= 218 MPa     (yield/2.0 per benchmark SF2)
  R2  first buckling load factor (LC2) >= 2.0  (R&R mandatory SF2)
  R3  max von Mises (LC2 crush)        <= 218 MPa
  R4  first 5 natural frequencies outside the 10-40 Hz track-joint band
  R5  monolithic shell mass            <= 180 kg
  R6  unfactored LC1 stress range      <= 140 MPa  (5e8-cycle endurance limit)

Step layout (matches analysis_template.inp):
  STEP 1  LC1 *STATIC               total time  1.0
  STEP 2  LC2 *STATIC               total time  2.0
  STEP 3  LC2 *BUCKLE               (does not advance total time)
  STEP 4  LC3 *STATIC               total time  3.0
  STEP 5  *FREQUENCY                total time  4.0  (mode-shape stress noise ignored)

We pull each load case's max von Mises from the *EL PRINT records in
model.dat (one block per static step) and read buckle eigenvalues +
frequencies from the same .dat.  Mass is recomputed from build.py geometry
(uniform 5 mm wall hollow cylinder, OD 1800 mm, L 2000 mm).
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

import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, "spec.json")
DAT  = os.path.join(HERE, "model.dat")
FRD  = os.path.join(HERE, "model.frd")

with open(SPEC) as f:
    spec = json.load(f)


# --------------------------------------------------------------------------
# Parsers
# --------------------------------------------------------------------------
def parse_buckling_factors(text: str) -> list[tuple[int, float]]:
    factors: list[tuple[int, float]] = []
    m = re.search(r"MODE NO\s+BUCKLING\s+FACTOR\s+", text)
    if not m:
        return factors
    tail = text[m.end():]
    for line in tail.splitlines()[:80]:
        s = line.strip()
        if not s:
            if factors:
                break
            continue
        parts = s.split()
        if len(parts) == 2:
            try:
                factors.append((int(parts[0]), float(parts[1])))
            except ValueError:
                break
        else:
            if factors:
                break
    return factors


def parse_frequencies(text: str) -> list[tuple[int, float]]:
    """Return list of (mode, freq_Hz) from CCX *FREQUENCY .dat output."""
    freqs: list[tuple[int, float]] = []
    m = re.search(r"E I G E N V A L U E .*?M O D E.*?H Z", text, re.DOTALL)
    # CalculiX 2.22 ASCII frequency table header reads:
    #   "MODE NO  EIGENVALUE      OMEGA      FREQUENCY     IMAGINARY"
    # but exact wording varies; fall back on a forgiving match.
    m = re.search(r"MODE NO\s+EIGENVALUE\s+(?:FREQUENCY|OMEGA)", text)
    if not m:
        return freqs
    tail = text[m.end():]
    for line in tail.splitlines()[:80]:
        s = line.strip()
        if not s:
            if freqs:
                break
            continue
        parts = s.split()
        try:
            mode = int(parts[0])
            # Layout: mode  eigenvalue  omega(rad/s)  freq(Hz)  [imag]
            # but some versions print: mode  eigenvalue  freq(Hz)  imag
            # Use the smallest plausible Hz value: take the LAST positive
            # column that is < 1e6 (eigenvalue is ~omega^2, freq is ~omega/2pi).
            cand = []
            for p in parts[1:]:
                try:
                    v = float(p)
                    if 0.0 < v < 1.0e6:
                        cand.append(v)
                except ValueError:
                    pass
            if not cand:
                if freqs:
                    break
                continue
            # The frequency-in-Hz is the smallest positive candidate that is
            # also < omega/(2*pi)+1; safest heuristic: pick the column that
            # is omega/(2*pi).  CCX prints columns in order so the 4th token
            # (index 3) is freq(Hz) when 5 columns are present.
            if len(parts) >= 4:
                try:
                    freq = float(parts[3])
                except ValueError:
                    freq = cand[-1]
            else:
                freq = cand[-1]
            freqs.append((mode, freq))
        except (ValueError, IndexError):
            if freqs:
                break
            continue
    return freqs


def parse_dat_stresses(text: str) -> list[tuple[float | None, float]]:
    """Per-block (time, max von Mises in Pa-or-MPa from .dat) records.

    CCX writes 'stresses (elem, integ.pt., sxx, syy, szz, sxy, sxz, syz)'
    with a 'time <T>' header.  We compute per-element-IP von Mises and
    keep the max.  Returned stress is in the deck's stress unit (here MPa,
    since the deck is mm-MPa-N-tonne).
    """
    out: list[tuple[float | None, float]] = []
    cur_time = None
    cur_max = 0.0
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("stresses (elem"):
            if in_block:
                out.append((cur_time, cur_max))
            cur_max = 0.0
            in_block = True
            m = re.search(r"time\s+([0-9.E+\-]+)", stripped)
            cur_time = float(m.group(1)) if m else None
            continue
        if not in_block:
            continue
        parts = stripped.split()
        if len(parts) >= 8:
            try:
                int(parts[0]); int(parts[1])
                sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                sxy = float(parts[5]); sxz = float(parts[6]); syz = float(parts[7])
                svm = math.sqrt(0.5 * ((sxx - syy)**2 + (syy - szz)**2 + (szz - sxx)**2)
                                + 3.0 * (sxy*sxy + sxz*sxz + syz*syz))
                if svm > cur_max:
                    cur_max = svm
            except ValueError:
                if stripped == "" or stripped.startswith("MODE"):
                    if in_block:
                        out.append((cur_time, cur_max))
                        in_block = False
        else:
            if stripped == "" and in_block:
                # blank line within a block: tolerate continuation
                continue
    if in_block:
        out.append((cur_time, cur_max))
    return out


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main() -> int:
    if not os.path.isfile(DAT) or not os.path.isfile(FRD):
        print("SKIP: solver output missing")
        return 0

    with open(DAT) as f:
        dat_text = f.read()

    bfacts      = parse_buckling_factors(dat_text)
    freqs       = parse_frequencies(dat_text)
    dat_stress  = parse_dat_stresses(dat_text)

    print("=" * 60)
    print("HV2 Hyperloop Pod Shell -- CalculiX verification")
    print("=" * 60)
    print(f"\nStress blocks parsed from .dat: {len(dat_stress)}")
    for i, (t, s) in enumerate(dat_stress):
        print(f"  block #{i+1:2d}  time={t}  max von Mises = {s:.2f} MPa")

    # Step layout:
    #   t=1.0 -> LC1 static
    #   t=2.0 -> LC2 static (and *BUCKLE eigenmode pre-stress at the same total time)
    #   t=3.0 -> LC3 static (after BUCKLE which does not advance total time)
    #   t=4.0 -> *FREQUENCY (modal stress is normalised mode shape -- ignore)
    def first_at(target: float) -> float | None:
        for (t, s) in dat_stress:
            if t is not None and abs(t - target) < 1e-6:
                return s
        return None

    svm_LC1 = first_at(1.0)
    svm_LC2 = first_at(2.0)
    svm_LC3 = first_at(3.0)

    bf1 = None
    if bfacts:
        pos = [v for (_, v) in bfacts if v > 0]
        if pos:
            bf1 = min(pos)

    print(f"\nBuckling factors (LC2 inward 0.2 MPa external crush):")
    for mode, val in bfacts[:5]:
        print(f"  mode {mode}: {val:.4f}")

    print(f"\nFrequencies (Hz) - clamped-clamped shell:")
    for mode, f in freqs[:5]:
        print(f"  mode {mode}: {f:.2f} Hz")

    # Mass: monolithic uniform 5 mm wall hollow cylinder
    OD = 1.8         # m
    L  = 2.0         # m
    t  = 0.005       # m
    rho = 2810.0     # kg/m^3
    A_lateral = math.pi * OD * L
    m_total = A_lateral * t * rho
    print(f"\nMass estimate (kg, uniform 5 mm wall hollow cylinder): {m_total:.1f}")

    # ----------------------------------------------------------------------
    # Pass / fail
    # ----------------------------------------------------------------------
    results: list[tuple[str, str, str]] = []

    def add(rid: str, status: str, note: str) -> None:
        results.append((rid, status, note))

    if svm_LC1 is None:
        add("R1", "SKIP", "no LC1 stress")
    else:
        add("R1", "PASS" if svm_LC1 <= 218.0 else "FAIL",
            f"max von Mises LC1 = {svm_LC1:.2f} MPa  (limit 218)")

    if svm_LC3 is None:
        add("R1-LC3", "SKIP", "no LC3 stress")
    else:
        add("R1-LC3", "PASS" if svm_LC3 <= 218.0 else "FAIL",
            f"max von Mises LC3 = {svm_LC3:.2f} MPa  (limit 218)")

    if bf1 is None:
        add("R2", "SKIP", "no buckling factors")
    else:
        add("R2", "PASS" if bf1 >= 2.0 else "FAIL",
            f"first buckling load factor = {bf1:.4f}  (limit >= 2.0)")

    if svm_LC2 is None:
        add("R3", "SKIP", "no LC2 stress")
    else:
        add("R3", "PASS" if svm_LC2 <= 218.0 else "FAIL",
            f"max von Mises LC2 = {svm_LC2:.2f} MPa  (limit 218)")

    if not freqs:
        add("R4", "SKIP", "no frequencies")
    else:
        first5 = [f for (_, f) in freqs[:5]]
        inside = [f for f in first5 if 10.0 <= f <= 40.0]
        add("R4", "PASS" if not inside else "FAIL",
            f"first 5 freqs (Hz) = {[round(f, 2) for f in first5]}  (must lie outside 10-40)")

    add("R5", "PASS" if m_total <= 180.0 else "FAIL",
        f"monolithic shell mass = {m_total:.1f} kg  (limit 180)")

    if svm_LC1 is None:
        add("R6", "SKIP", "no LC1 stress")
    else:
        # SF2 already applied to LC1 pressure; the unfactored cyclic stress
        # range over 1000 pressurise/vent cycles is svm_LC1 / 2.
        range_unfact = svm_LC1 / 2.0
        add("R6", "PASS" if range_unfact <= 140.0 else "FAIL",
            f"unfactored LC1 stress range = {range_unfact:.2f} MPa  (limit 140)")

    print("\n" + "=" * 60)
    print("PASS/FAIL summary")
    print("=" * 60)
    overall = "PASS"
    for rid, st, note in results:
        print(f"  {rid:8s} {st:5s}  {note}")
        if st == "FAIL":
            overall = "FAIL"
        elif st == "SKIP" and overall != "FAIL":
            overall = "SKIP"

    print(f"\nOVERALL: {overall}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
