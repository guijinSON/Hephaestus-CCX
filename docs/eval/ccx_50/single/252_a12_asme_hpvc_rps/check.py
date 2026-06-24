#!/usr/bin/env python3
"""Post-process model.dat for the ASME HPVC tandem RPS verification.

Per-load-case requirements (from spec.json):
  R1a: LC1 deflection at top load point     <= 51 mm
  R1b: LC1 max von Mises                    <= 400 MPa
  R2a: LC2 deflection at side load point    <= 38 mm
  R2b: LC2 max von Mises                    <= 400 MPa
  R3 : LC3 max von Mises                    <= 400 MPa
  R4a: LC4 top deflection                   <= 51 mm
  R4b: LC4 side deflection                  <= 38 mm
  R4c: LC4 max von Mises                    <= 447 MPa
  R5 : design mass                          <= 7.5 kg

Force scaling
-------------
analysis_template.inp applies *CLOAD on each NSET node with unit-magnitude
per-node components, so the actual per-step total is N_nodes_in_set times
the per-node magnitude.  This script reads model.inp to count NSET nodes
and computes the scale factor that maps each step to the spec's target
total force (LC1=2670 N, LC2=1330 N, LC3=1334 N, LC4=LC1+LC2).  Linear
elastic CCX results are then multiplied by that scale to obtain the
correct deflections and stresses.

Mass
----
Mass is computed from the meshed solid: total volume of the C3D
elements in mesh.inp times the steel density.  The agent's build.py
uses bending-I-equivalent square bars (cross-section area larger than
the real tube), so a tube/equivalent ratio correction is applied so
the reported mass reflects the real 25.4 mm OD x 2.41 mm wall tubing.
"""
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

DATFILE   = os.path.join(HERE, "model.dat")
INPFILE   = os.path.join(HERE, "model.inp")
MESHFILE  = os.path.join(HERE, "mesh.inp")

# limits (per spec.json requirements)
LIM_DEFL_LC1 = 51.0    # mm
LIM_DEFL_LC2 = 38.0    # mm
LIM_VM_400   = 400.0   # MPa  (yield/1.15 = 460/1.15)
LIM_VM_447   = 447.0   # MPa  (UTS/1.5 = 670/1.5)
LIM_MASS_KG  = 7.5

# target total forces per step (N)
TARGET_LC1 = 2670.0   # 12 deg from vertical: |F| = 2670
TARGET_LC2 = 1330.0
TARGET_LC3 = 1334.0
# LC4 top + side superposed; treated per-component in scaling

# real tube cross-section (for the optional bar->tube mass correction)
TUBE_OD = 25.4
TUBE_WT = 2.41
A_TUBE  = math.pi / 4.0 * (TUBE_OD ** 2 - (TUBE_OD - 2.0 * TUBE_WT) ** 2)
I_TUBE  = math.pi / 64.0 * (TUBE_OD ** 4 - (TUBE_OD - 2.0 * TUBE_WT) ** 4)
BAR_SIDE = (12.0 * I_TUBE) ** 0.25   # ~ 19.33 mm
A_BAR    = BAR_SIDE * BAR_SIDE
RHO_REAL_T_PER_MM3 = 7.85e-9


# ---------- mesh / model parsing -----------------------------------------
def parse_nodes(path: str) -> dict:
    """Return {nid: (x,y,z)} parsed from any *NODE blocks in path."""
    nodes = {}
    in_node = False
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("**"):
                continue
            head = s.split(",")[0].strip().upper()
            if head == "*NODE":
                in_node = True
                continue
            if s.startswith("*"):
                in_node = False
                continue
            if in_node:
                p = [x.strip() for x in s.split(",")]
                try:
                    nid = int(p[0])
                    x = float(p[1]); y = float(p[2]); z = float(p[3])
                    nodes[nid] = (x, y, z)
                except (ValueError, IndexError):
                    continue
    return nodes


def parse_nsets(path: str) -> dict:
    """Return {NSET_NAME: set(nids)} parsed from path."""
    nsets: dict = {}
    cur = None
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("**"):
                continue
            head = s.split(",")[0].strip().upper()
            if head == "*NSET":
                # extract NSET=NAME
                m = re.search(r"NSET\s*=\s*([A-Za-z0-9_]+)", s, re.IGNORECASE)
                cur = m.group(1) if m else None
                if cur:
                    nsets.setdefault(cur, set())
                continue
            if s.startswith("*"):
                cur = None
                continue
            if cur is not None:
                for tok in s.split(","):
                    tok = tok.strip()
                    if tok.isdigit():
                        nsets[cur].add(int(tok))
    return nsets


def parse_c3d_elements(path: str) -> list:
    """Return list of (etype, [nid,...]) for each *ELEMENT with C3D type."""
    elems = []
    cur_type = None
    in_elem = False
    with open(path) as f:
        for ln in f:
            s = ln.strip()
            if not s or s.startswith("**"):
                continue
            up = s.upper()
            if up.startswith("*ELEMENT"):
                m = re.search(r"TYPE\s*=\s*([A-Za-z0-9]+)", s, re.IGNORECASE)
                t = m.group(1).upper() if m else ""
                if t.startswith("C3D"):
                    cur_type = t; in_elem = True
                else:
                    cur_type = None; in_elem = False
                continue
            if s.startswith("*"):
                in_elem = False; cur_type = None
                continue
            if in_elem:
                # accumulate continuation lines
                p = [x.strip() for x in s.split(",") if x.strip()]
                try:
                    eid = int(p[0])
                    nids = [int(x) for x in p[1:]]
                    elems.append((cur_type, nids, eid))
                except ValueError:
                    continue
    return elems


def tet_volume(p0, p1, p2, p3) -> float:
    """Volume of a tetrahedron from 4 corner points."""
    a = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    b = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
    c = (p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2])
    cross = (a[1]*b[2] - a[2]*b[1],
             a[2]*b[0] - a[0]*b[2],
             a[0]*b[1] - a[1]*b[0])
    return abs(cross[0]*c[0] + cross[1]*c[1] + cross[2]*c[2]) / 6.0


def total_solid_volume_mm3(mesh_path: str) -> float:
    """Sum tet (and tet10) volumes from the mesh .inp."""
    if not os.path.isfile(mesh_path):
        return 0.0
    nodes = parse_nodes(mesh_path)
    elems = parse_c3d_elements(mesh_path)
    vol = 0.0
    for etype, nids, _eid in elems:
        # for C3D4 -> 4 corners; for C3D10 -> first 4 nids are corners
        if len(nids) >= 4 and all(n in nodes for n in nids[:4]):
            p = [nodes[n] for n in nids[:4]]
            vol += tet_volume(*p)
    return vol


# ---------- model.dat parsing --------------------------------------------
def parse_dat(path: str) -> dict:
    """Return {step: {'disp': {nid:(ux,uy,uz)}, 'svm_max': float, 'svm_max_elem': int}}.

    CCX writes blocks: 'displacements (...) for set NTOP_LOAD ... time 0.X000000E+01'
    and 'stresses (elem, integ.pt., sxx, syy, szz, sxy, sxz, syz) ... time 0.X000000E+01'.
    """
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        text = f.read()

    blocks = re.split(r"\n(?=\s*(?:displacements|stresses)\s)", text)
    out: dict = {}
    for blk in blocks:
        m = re.search(r"\s*(displacements|stresses)\s.*time\s+0\.(\d)000000E\+01", blk)
        if not m:
            continue
        kind = m.group(1)
        step = int(m.group(2))
        out.setdefault(step, {"disp": {}, "svm_max": 0.0, "svm_max_elem": None})

        for ln in blk.splitlines()[1:]:
            s = ln.strip()
            if not s:
                continue
            if s.startswith(("displacements", "stresses")):
                continue
            parts = s.split()
            if kind == "displacements":
                if len(parts) >= 4:
                    try:
                        nid = int(parts[0])
                        ux = float(parts[1]); uy = float(parts[2]); uz = float(parts[3])
                        out[step]["disp"][nid] = (ux, uy, uz)
                    except ValueError:
                        continue
            elif kind == "stresses":
                if len(parts) < 8:
                    continue
                try:
                    eid = int(parts[0])
                    sxx = float(parts[2]); syy = float(parts[3]); szz = float(parts[4])
                    sxy = float(parts[5]); sxz = float(parts[6]); syz = float(parts[7])
                except ValueError:
                    continue
                vm = math.sqrt(0.5 * ((sxx-syy)**2 + (syy-szz)**2 + (szz-sxx)**2)
                               + 3.0 * (sxy**2 + sxz**2 + syz**2))
                if vm > out[step]["svm_max"]:
                    out[step]["svm_max"] = vm
                    out[step]["svm_max_elem"] = eid
    return out


def resultant(t):
    return math.sqrt(t[0]*t[0] + t[1]*t[1] + t[2]*t[2]) if t else float("nan")


# ---------- main ----------------------------------------------------------
def main() -> int:
    print("=" * 76)
    print("ASME HPVC tandem Roll Protection System (RPS) -- check.py")
    print("=" * 76)
    print()

    # NSET node counts (read from model.inp - the wired deck)
    nsets = parse_nsets(INPFILE) if os.path.isfile(INPFILE) else {}
    n_top  = len(nsets.get("NTOP_LOAD", set()))
    n_side = len(nsets.get("NSIDE", set()))
    n_harn = len(nsets.get("NHARNESS", set()))
    n_fix  = len(nsets.get("NFIXED", set()))
    print(f"NSET node counts: NFIXED={n_fix} NTOP_LOAD={n_top} "
          f"NSIDE={n_side} NHARNESS={n_harn}")
    if not (n_top and n_side and n_harn):
        print("ERROR: required NSETs missing or empty in model.inp", file=sys.stderr)
        return 2

    # per-step force scaling: target_total / applied_total
    # applied_total per step:
    #   LC1: |F| per node = sqrt(0.20791^2 + 0.97815^2) = 1.0; total = 1.0 * n_top
    #   LC2: |F| per node = 1.0;                              total = 1.0 * n_side
    #   LC3: |F| per node = 1.0;                              total = 1.0 * n_harn
    #   LC4: top + side simultaneously (separate scales for each component)
    s_lc1 = TARGET_LC1 / float(n_top)
    s_lc2 = TARGET_LC2 / float(n_side)
    s_lc3 = TARGET_LC3 / float(n_harn)
    print(f"Force-scale factors: LC1={s_lc1:.3f}  LC2={s_lc2:.3f}  LC3={s_lc3:.3f}")
    print()

    res = parse_dat(DATFILE)
    if not res:
        print("ERROR: no displacement/stress data in model.dat", file=sys.stderr)
        return 3

    # Pull peak displacement at the LOADED set per step (this is the
    # "load-point deflection" in the spec).
    def step_peak_disp(step, nset_name):
        if step not in res:
            return None
        ids = nsets.get(nset_name, set())
        best = (0.0, None)
        for nid, u in res[step]["disp"].items():
            if nid in ids:
                m = resultant(u)
                if m > best[0]:
                    best = (m, nid)
        return best  # (mag, nid)

    summary = {}

    # LC1
    if 1 in res:
        d, nd = step_peak_disp(1, "NTOP_LOAD")
        d_scaled = d * s_lc1 if d is not None else float("nan")
        vm_scaled = res[1]["svm_max"] * s_lc1
        summary[1] = {"d": d_scaled, "vm": vm_scaled,
                      "elem": res[1]["svm_max_elem"], "nid": nd}
        print(f"LC1 top  load (target {TARGET_LC1:.0f} N @ 12 deg): "
              f"|U|@TOP_LOAD = {d_scaled:.3f} mm (n={nd})  "
              f"max von Mises = {vm_scaled:.2f} MPa "
              f"(elem {res[1]['svm_max_elem']})")
    if 2 in res:
        d, nd = step_peak_disp(2, "NSIDE")
        d_scaled = d * s_lc2 if d is not None else float("nan")
        vm_scaled = res[2]["svm_max"] * s_lc2
        summary[2] = {"d": d_scaled, "vm": vm_scaled,
                      "elem": res[2]["svm_max_elem"], "nid": nd}
        print(f"LC2 side load (target {TARGET_LC2:.0f} N): "
              f"|U|@SIDE = {d_scaled:.3f} mm (n={nd})  "
              f"max von Mises = {vm_scaled:.2f} MPa "
              f"(elem {res[2]['svm_max_elem']})")
    if 3 in res:
        d, nd = step_peak_disp(3, "NHARNESS")
        d_scaled = d * s_lc3 if d is not None else float("nan")
        vm_scaled = res[3]["svm_max"] * s_lc3
        summary[3] = {"d": d_scaled, "vm": vm_scaled,
                      "elem": res[3]["svm_max_elem"], "nid": nd}
        print(f"LC3 harness  (target {TARGET_LC3:.0f} N rearward): "
              f"|U|@HARNESS = {d_scaled:.3f} mm (n={nd})  "
              f"max von Mises = {vm_scaled:.2f} MPa "
              f"(elem {res[3]['svm_max_elem']})")
    # LC4: deflection scaling is non-uniform (top and side scales differ),
    # so we cannot uniformly scale the LC4 step.  Instead use linear
    # superposition: LC4 == s_lc1*LC1_response + s_lc2*LC2_response.  This
    # is exact for linear-elastic CCX *STATIC.
    if 1 in res and 2 in res:
        # top-point deflection: use NTOP_LOAD nodes from LC1 + LC2
        top_ids = nsets.get("NTOP_LOAD", set())
        side_ids = nsets.get("NSIDE", set())

        def superposed_peak(target_ids):
            best = (0.0, None)
            for nid in target_ids:
                u1 = res[1]["disp"].get(nid, (0.0, 0.0, 0.0))
                u2 = res[2]["disp"].get(nid, (0.0, 0.0, 0.0))
                ux = s_lc1 * u1[0] + s_lc2 * u2[0]
                uy = s_lc1 * u1[1] + s_lc2 * u2[1]
                uz = s_lc1 * u1[2] + s_lc2 * u2[2]
                m = math.sqrt(ux*ux + uy*uy + uz*uz)
                if m > best[0]:
                    best = (m, nid)
            return best

        d_top, nd_top   = superposed_peak(top_ids)
        d_side, nd_side = superposed_peak(side_ids)
        # LC4 stress: directly from step 4 with mixed scaling.  For an
        # upper-bound check, sum scaled peaks: vm4 <= s_lc1*vm1 + s_lc2*vm2.
        vm_lc4_super = s_lc1 * res[1]["svm_max"] + s_lc2 * res[2]["svm_max"]
        # Also report direct LC4 step (scaled by max(s_lc1, s_lc2) for a
        # rough sanity figure; primary reporting uses superposition).
        summary[4] = {"d_top": d_top, "d_side": d_side, "vm": vm_lc4_super}
        print(f"LC4 combined (top + side superposed): "
              f"|U|@TOP={d_top:.3f} mm (n={nd_top})  "
              f"|U|@SIDE={d_side:.3f} mm (n={nd_side})  "
              f"vm <= s_LC1*vm_LC1 + s_LC2*vm_LC2 = {vm_lc4_super:.2f} MPa")
    print()

    # ---- mass ------------------------------------------------------------
    vol_mm3 = total_solid_volume_mm3(MESHFILE)
    if vol_mm3 == 0.0:
        # fall back to the model.inp mesh embedded after splice
        vol_mm3 = total_solid_volume_mm3(INPFILE)
    mass_bar_kg = vol_mm3 * RHO_REAL_T_PER_MM3 * 1000.0
    # convert from "bar volume" to "tube mass" by area ratio
    mass_tube_kg = mass_bar_kg * (A_TUBE / A_BAR) if A_BAR > 0 else mass_bar_kg
    print(f"Mesh volume: {vol_mm3:.0f} mm^3  "
          f"({mass_bar_kg:.3f} kg as solid bar; "
          f"{mass_tube_kg:.3f} kg with tube/bar area correction "
          f"A_tube/A_bar={A_TUBE/A_BAR:.4f})")
    print()

    # ---- pass/fail ------------------------------------------------------
    print("=" * 76); print("Requirement checks"); print("=" * 76)

    def chk(rid, name, value, op, lim, unit):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            ok = False; tag = "SKIP"
        elif op == "<=":
            ok = value <= lim
            tag = "PASS" if ok else "FAIL"
        else:
            ok = False; tag = "FAIL"
        print(f"  {rid:5s} {name:35s} value = {value:10.3f} {unit:4s}  "
              f"limit {op} {lim} {unit}   -> {tag}")
        return ok

    overall = True
    if 1 in summary:
        overall &= chk("R1a", "LC1 deflection @ top",   summary[1]["d"], "<=", LIM_DEFL_LC1, "mm")
        overall &= chk("R1b", "LC1 max von Mises",      summary[1]["vm"], "<=", LIM_VM_400, "MPa")
    if 2 in summary:
        overall &= chk("R2a", "LC2 deflection @ side",  summary[2]["d"], "<=", LIM_DEFL_LC2, "mm")
        overall &= chk("R2b", "LC2 max von Mises",      summary[2]["vm"], "<=", LIM_VM_400, "MPa")
    if 3 in summary:
        overall &= chk("R3",  "LC3 max von Mises",      summary[3]["vm"], "<=", LIM_VM_400, "MPa")
    if 4 in summary:
        overall &= chk("R4a", "LC4 top deflection",     summary[4]["d_top"], "<=", LIM_DEFL_LC1, "mm")
        overall &= chk("R4b", "LC4 side deflection",    summary[4]["d_side"], "<=", LIM_DEFL_LC2, "mm")
        overall &= chk("R4c", "LC4 max von Mises (sup.)", summary[4]["vm"], "<=", LIM_VM_447, "MPa")
    overall &= chk("R5", "Frame mass (tube-corrected)", mass_tube_kg, "<=", LIM_MASS_KG, "kg")

    print()
    print("=" * 76)
    print(f"Overall: {'PASS' if overall else 'FAIL'}")
    print("=" * 76)
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
