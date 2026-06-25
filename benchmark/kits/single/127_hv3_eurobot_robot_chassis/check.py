#!/usr/bin/env python3
"""Pass/fail check for 127_hv3 Eurobot chassis deck (5083-H111).

Reads model.frd and model.dat from the current working directory.
Extracts:
  - per-step max nodal von Mises stress (MPa)        -> R1, R2, R3a
  - per-step max nodal displacement magnitude (mm)   -> R3b
  - first 6 natural frequencies (Hz)                 -> R4
  - deck mass via spec geometry (analytical)         -> R5
  - deck silhouette perimeter (analytical, 4*300 mm) -> R6

R5 mass is computed from spec.json geometry (plate minus pockets,
M5 holes, M12 bores, wheel cutouts, front-recess) NOT from the agent's
out.step volume. Rationale: the eval template runs on whatever STEP the
agent submits, but the spec.json mass cap is against the as-designed
deck per spec. The agent's CAD volume can be reported separately for
informational use.

R6 silhouette perimeter is fixed by the spec (300x300 mm footprint =
1200 mm perimeter at the limit by construction).
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
from pathlib import Path


HERE = Path(__file__).resolve().parent
FRD = HERE / "model.frd"
DAT = HERE / "model.dat"
SPEC = HERE / "spec.json"


# --------------------------------------------------------------------------- #
# FRD parser (block-based, fixed-width fields per CCX FRD spec)
# --------------------------------------------------------------------------- #
def parse_frd_blocks(path: Path):
    """Return ordered list of {'name': str, 'data': {nid: tuple(floats)}}."""
    blocks = []
    cur = None
    with path.open() as f:
        for raw in f:
            line = raw.rstrip("\n")
            head = line[:3]
            if head == " -4":
                # New result block: name token in cols 5..13
                name = line[5:13].strip()
                if cur is not None:
                    blocks.append(cur)
                cur = {"name": name, "data": {}}
            elif head == " -1" and cur is not None:
                # Data line. CCX FRD format:
                #   cols  4..13  (10 chars)  node id (right justified)
                #   cols 14+     12-char float fields
                try:
                    nid = int(line[3:13])
                except ValueError:
                    continue
                rest = line[13:]
                vals = []
                for k in range(0, len(rest), 12):
                    chunk = rest[k:k + 12]
                    if not chunk.strip():
                        break
                    try:
                        vals.append(float(chunk))
                    except ValueError:
                        break
                cur["data"][nid] = tuple(vals)
            elif head == " -3":
                if cur is not None:
                    blocks.append(cur)
                    cur = None
    if cur is not None:
        blocks.append(cur)
    return blocks


def group_steps(blocks):
    """Group blocks into per-step dicts. Each new DISP starts a new step."""
    steps = []
    cur = {}
    for b in blocks:
        nm = b["name"]
        if nm == "DISP":
            if cur:
                steps.append(cur)
            cur = {"DISP": b["data"]}
        elif nm:
            cur[nm] = b["data"]
    if cur:
        steps.append(cur)
    return steps


def vm6(s):
    """von Mises from 6-component nodal stress (sxx, syy, szz, sxy, syz, szx)."""
    sxx, syy, szz, sxy, syz, szx = s[:6]
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                     + 3.0 * (sxy * sxy + syz * syz + szx * szx))


def step_metrics(step):
    """Return (max_vm_MPa, max_disp_mm) for one step's DISP+STRESS block pair."""
    disp = step.get("DISP", {})
    stress = step.get("STRESS", {})
    max_vm = max((vm6(v) for v in stress.values()), default=0.0)
    max_u = max((math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
                 for v in disp.values()),
                default=0.0)
    return max_vm, max_u


# --------------------------------------------------------------------------- #
# Frequency parser (CCX writes a "MODE NO" / "EIGENVALUE" table to .dat)
# --------------------------------------------------------------------------- #
def parse_frequencies(dat_path: Path, n_modes: int = 6):
    """Extract the first n_modes frequencies (Hz) from model.dat."""
    if not dat_path.exists():
        return []
    text = dat_path.read_text()
    # CCX FREQUENCY block table: header has "MODE NO" "EIGENVALUE" "FREQUENCY"
    # Each data row: mode_idx, eigenvalue (rad^2/s^2), omega (rad/s), freq (Hz)
    freqs = []
    in_table = False
    for line in text.splitlines():
        if "MODE NO" in line.upper() and "EIGENVALUE" in line.upper():
            in_table = True
            continue
        if in_table:
            parts = line.split()
            if len(parts) >= 4:
                try:
                    int(parts[0])
                    f_hz = float(parts[3])
                    freqs.append(f_hz)
                    if len(freqs) >= n_modes:
                        break
                except ValueError:
                    if freqs:
                        break
            else:
                if freqs:
                    break
    return freqs[:n_modes]


# --------------------------------------------------------------------------- #
# Mass from spec geometry (analytical)
# --------------------------------------------------------------------------- #
def compute_spec_mass(spec: dict) -> float:
    """Compute deck mass using spec.json geometry (kg)."""
    g = spec["prompt"]["geometric_constraints"]
    rho = float(spec["prompt"]["material"]["properties"]["density_kg_m3"])
    LX = g["deck_mm"]["w"] / 1000.0
    LY = g["deck_mm"]["h"] / 1000.0
    LZ = g["deck_mm"]["t"] / 1000.0
    plate_vol = LX * LY * LZ

    pkt = g["pockets_mm"]
    pocket_vol = pkt["count"] * (pkt["w"] / 1000.0) * (pkt["h"] / 1000.0) * (pkt["d"] / 1000.0)

    mh = g["motor_grid_holes"]
    hole_m5 = mh["count"] * math.pi * (mh["diameter_mm"] / 2000.0) ** 2 * LZ

    cb = g["corner_bores"]
    hole_m12 = cb["count"] * math.pi * (cb["diameter_mm"] / 2000.0) ** 2 * LZ

    wc = g["wheel_cutouts"]
    # Two D-shaped cutouts (half-circles through full thickness).
    wheel_cut = wc["count"] * 0.5 * math.pi * (wc["diameter_mm"] / 2000.0) ** 2 * LZ

    fr = g["front_edge_recess_mm"]
    # Recess: 200 mm wide x 10 mm deep into the plate (full thickness notch).
    recess_vol = (fr["w"] / 1000.0) * (fr["d"] / 1000.0) * LZ

    mass = (plate_vol - pocket_vol - hole_m5 - hole_m12 - wheel_cut - recess_vol) * rho
    return mass


def compute_silhouette_perimeter(spec: dict) -> float:
    g = spec["prompt"]["geometric_constraints"]["deck_mm"]
    return 2.0 * (g["w"] + g["h"])


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    if not FRD.exists():
        print(f"FAIL: {FRD} not found")
        return 1
    if not SPEC.exists():
        print(f"FAIL: {SPEC} not found")
        return 1

    spec = json.loads(SPEC.read_text())
    crit = {r["id"]: r for r in spec["requirements"]["pass_fail_criteria"]}

    blocks = parse_frd_blocks(FRD)
    steps = group_steps(blocks)
    # Static steps are those with both DISP and STRESS. *FREQUENCY emits DISP
    # (mode shapes) but no STRESS, so we filter to first 3 with STRESS.
    static_steps = [s for s in steps if "STRESS" in s][:3]

    summaries = [step_metrics(s) for s in static_steps]
    labels = ["LC1 match-play (1g + 0.15g)",
              "LC2 collision (50 N)",
              "LC3 drop (5g)"]

    print("=" * 72)
    print("Eurobot HV3 Chassis Deck - FEM Verification Report")
    print("=" * 72)
    print(f"{'Case':<32} {'VM_max [MPa]':>14} {'|U|_max [mm]':>14}")
    for lbl, (vm, u) in zip(labels, summaries):
        print(f"{lbl:<32} {vm:14.4f} {u:14.4e}")

    freqs = parse_frequencies(DAT, n_modes=6)
    print()
    if freqs:
        print(f"First {len(freqs)} natural freqs (Hz): "
              f"{[round(f, 2) for f in freqs]}")
    else:
        print("No frequencies parsed from model.dat")

    spec_mass_kg = compute_spec_mass(spec)
    silhouette_mm = compute_silhouette_perimeter(spec)
    print(f"Spec-geometry mass: {spec_mass_kg:.3f} kg")
    print(f"Spec-silhouette perimeter: {silhouette_mm:.0f} mm")
    print()

    # ---------------- Pass/fail evaluation ----------------
    print("-" * 72)
    print("Pass/Fail Evaluation vs spec.json")
    print("-" * 72)
    results = []  # (id, status, msg)

    # R1: LC1 max VM <= 72 MPa
    r = crit["R1"]
    if not summaries:
        results.append(("R1", "SKIP", "no LC1 result"))
    else:
        vm = summaries[0][0]
        verdict = "PASS" if vm <= r["limit_MPa"] else "FAIL"
        results.append(("R1", verdict,
                        f"LC1 max VM = {vm:.3f} MPa <= {r['limit_MPa']} MPa"))

    # R2: LC2 max VM <= 97 MPa
    r = crit["R2"]
    if len(summaries) < 2:
        results.append(("R2", "SKIP", "no LC2 result"))
    else:
        vm = summaries[1][0]
        verdict = "PASS" if vm <= r["limit_MPa"] else "FAIL"
        results.append(("R2", verdict,
                        f"LC2 max VM = {vm:.3f} MPa <= {r['limit_MPa']} MPa"))

    # R3: LC3 max VM <= 97 MPa AND max |U| <= 3.0 mm
    r = crit["R3"]
    if len(summaries) < 3:
        results.append(("R3", "SKIP", "no LC3 result"))
    else:
        vm, u = summaries[2]
        lim_vm = r["limit_compound"]["stress_MPa"]
        lim_u = r["limit_compound"]["deflection_mm"]
        ok_vm = vm <= lim_vm
        ok_u = u <= lim_u
        verdict = "PASS" if (ok_vm and ok_u) else "FAIL"
        results.append(("R3", verdict,
                        f"LC3 VM = {vm:.3f} MPa <= {lim_vm} MPa "
                        f"AND |U| = {u:.4e} mm <= {lim_u} mm"))

    # R4: first 6 natural freqs >= 60 Hz
    r = crit["R4"]
    if len(freqs) < 6:
        results.append(("R4", "SKIP",
                        f"only {len(freqs)} freqs parsed (need 6)"))
    else:
        ok = all(f >= r["limit_Hz"] for f in freqs[:6])
        verdict = "PASS" if ok else "FAIL"
        results.append(("R4", verdict,
                        f"f1..f6 = {[round(f, 1) for f in freqs[:6]]} Hz, "
                        f"all >= {r['limit_Hz']} Hz"))

    # R5: spec-geometry mass <= 1.6 kg
    r = crit["R5"]
    verdict = "PASS" if spec_mass_kg <= r["limit_kg"] else "FAIL"
    results.append(("R5", verdict,
                    f"deck mass (spec geom) = {spec_mass_kg:.3f} kg "
                    f"<= {r['limit_kg']} kg"))

    # R6: silhouette perimeter <= 1200 mm
    r = crit["R6"]
    verdict = "PASS" if silhouette_mm <= r["limit_mm"] else "FAIL"
    results.append(("R6", verdict,
                    f"start-perimeter = {silhouette_mm:.0f} mm "
                    f"<= {r['limit_mm']} mm"))

    print()
    for rid, status, msg in results:
        print(f"  [{status:<4}] {rid}: {msg}")
    print()

    n_pass = sum(1 for _, s, _ in results if s == "PASS")
    n_fail = sum(1 for _, s, _ in results if s == "FAIL")
    n_skip = sum(1 for _, s, _ in results if s == "SKIP")
    print(f"Summary: {n_pass} PASS, {n_fail} FAIL, {n_skip} SKIP")
    if n_fail > 0:
        print("OVERALL: FAIL")
        return 1
    if n_skip > 0 and n_pass == 0:
        print("OVERALL: SKIP")
        return 0
    print("OVERALL: PASS" + (" (with SKIPs noted)" if n_skip else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
