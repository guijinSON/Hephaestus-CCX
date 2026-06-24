"""Sample submission for 126_hv2_hyperloop_pod_shell.

Builds a thin-walled hollow cylindrical pod shell (no integral stiffeners,
no bolted end flanges) as a placeholder reference geometry for the HV2
Hyperloop benchmark.  The submission-agnostic eval kit only requires that
the agent produce a STEP whose two axial extreme faces are reachable via
the NEND1 (z_min) and NEND2 (z_max) selectors emitted in meta.json, and
whose inner cylindrical surface is reachable via the NINNER selector.

Geometry (mm, axis = +Z):
    OD          = 1800 mm        (1.8 m outer diameter)
    wall        = 5 mm           (5 mm uniform thickness)
    length      = 2000 mm        (2.0 m, z = 0 ... 2000)

Coordinate frame (mm):
    +z  pod long axis  (z_min  -> NEND1 ;  z_max  -> NEND2 -- both clamped)
    +x  lateral        (cornering body force, 0.4 g)
    +y  vertical       (cornering body force, 2 g)

NSET selectors emitted in meta.json (consumed by wire_bcs.py):
    NEND1   nodes on the z = 0   end face   (flange 1, clamped 1-3)
    NEND2   nodes on the z = L   end face   (flange 2, clamped 1-3)
    NINNER  nodes on the inner cylindrical surface (radius = r_inner).
            Realised via an AABB box that is *contained inside the cavity*
            but inflated by tol_mm so it just reaches the inner-surface
            ring; the cavity is empty so no spurious volume nodes are
            captured, and the outer cylindrical surface (at radius r_outer
            = r_inner + 5 mm) lies safely outside the inflated box.

Outputs in this directory:
    out.step    cadquery STEP AP242 export of the hollow shell
    meta.json   NSET selectors + material + jobname for wire_bcs.py
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometric parameters (mm)
# ---------------------------------------------------------------------------
OD_MM       = 1800.0   # outer diameter
WALL_MM     = 5.0      # wall thickness
LENGTH_MM   = 2000.0   # axial length

R_OUTER     = OD_MM / 2.0          # 900.0 mm
R_INNER     = R_OUTER - WALL_MM    # 895.0 mm

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_shell() -> cq.Workplane:
    """Hollow cylindrical pod shell, axis along +z, base at z=0."""
    outer = (
        cq.Workplane("XY")
        .circle(R_OUTER)
        .extrude(LENGTH_MM)
    )
    inner = (
        cq.Workplane("XY")
        .circle(R_INNER)
        .extrude(LENGTH_MM)
    )
    return outer.cut(inner)


def write_meta() -> dict:
    # Inner cylindrical surface: use wire_bcs.py's `radius_xy` selector with
    # a tol_mm tight enough to exclude the outer cylinder (5 mm wall away).
    # Inner radius ~= 895 mm; outer radius = 900 mm; tol_mm = 2 mm picks only
    # nodes on the inner cylinder with 3 mm margin to the outer.
    inner_sel = {
        "radius_xy": R_INNER,
        "axis":      "z",
        "z_range":   [0.0, LENGTH_MM],
        "tol_mm":    2.0,
    }

    meta = {
        "jobname": "model",
        "material": "AL7075T73",
        "selectors": {
            # Both end-rings clamped per spec ("flanges fixed"; NFIXED in template).
            "NEND1":  {"face": "z_min", "tol_mm": 0.5},
            "NEND2":  {"face": "z_max", "tol_mm": 0.5},
            # Inner cylindrical surface as an explicit NSET (handy for any
            # node-printout / debugging; *SURFACE for *DLOAD pressure is
            # emitted by wire_bcs.py from the pressure_surfaces block below).
            "NINNER": inner_sel,
        },
        # wire_bcs.py converts each `pressure_surfaces` entry into a
        # *SURFACE, NAME=<name>, TYPE=ELEMENT block by listing every tet
        # face whose three corner nodes are all in the named selector. The
        # analysis_template.inp can then apply *DLOAD <name>, P, magnitude.
        "pressure_surfaces": {
            "SINNER": inner_sel,
        },
        "geometry": {
            "OD_mm": OD_MM,
            "wall_mm": WALL_MM,
            "length_mm": LENGTH_MM,
            "r_inner_mm": R_INNER,
            "r_outer_mm": R_OUTER,
            "z_min_mm": 0.0,
            "z_max_mm": LENGTH_MM,
        },
        "notes": (
            "Hyperloop pod shell, OD 1800 mm x wall 5 mm x L 2000 mm, axis +z. "
            "NEND1 = z_min flange face (clamped 1-3). NEND2 = z_max flange face "
            "(clamped 1-3). NINNER = inner-cylindrical-surface NSET. SINNER = "
            "inner-cylindrical-surface *SURFACE TYPE=ELEMENT (auto-built by "
            "wire_bcs.py for *DLOAD pressure in LC1/LC2 + *BUCKLE). LC3 "
            "cornering is applied as *DLOAD GRAV body force on Eall."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    shell = build_shell()
    cq.exporters.export(shell, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}: {json.dumps(meta, indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
