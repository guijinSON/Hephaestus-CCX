"""
NG8 — Build Change seismic retrofit: simplified wall-panel + corner columns.

This is a SUBMISSION-AGNOSTIC reference build that the eval harness can run.
It produces:
    out.step   — solid assembly (URM wall panel + 2 RC corner columns)
    meta.json  — selectors used by wire_bcs.py to populate NFIXED / NTOP
                 NSETs in the analysis_template, plus the agent's chosen
                 material handle.

Geometry (units: meters; STEP carries raw numerics so gmsh meshes in m):
    URM wall panel  : 8.5 m (X)  x  0.27 m (Y)  x  3.0 m (Z)
                       (composite thickness = 0.23 m URM + 0.04 m RC jacket)
    Corner column L : 0.27 m (X) x  0.27 m (Y)  x  3.0 m (Z) at X = -0.27..0
    Corner column R : 0.27 m (X) x  0.27 m (Y)  x  3.0 m (Z) at X = 8.5..8.77

The whole assembly is fused into a single compound; downstream gmsh tags it
as one VOLUME physical group, and analysis_template.inp uses ELSET=Eall.

Coord frame:
    +X : long-wall (8.5 m + corner-column overhangs)
    +Y : wall thickness (0.27 m)
    +Z : height (0..3.0 m); base at Z=0 is fixed.
"""

from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Dimensions (m)
# ---------------------------------------------------------------------------
WALL_LX = 8.5     # long-wall length
WALL_TY = 0.27    # composite thickness (URM 0.23 + RC jacket 0.04)
WALL_HZ = 3.0     # storey height
COL_X = 0.27      # corner-column footprint (X)
COL_Y = 0.27      # corner-column footprint (Y)
COL_H = 3.0       # corner-column height = full storey

OUT_STEP = Path(__file__).parent / "out.step"
OUT_META = Path(__file__).parent / "meta.json"


def main() -> int:
    # Wall panel centred so its base is at Z = 0, panel runs X = 0..8.5,
    # Y = 0..0.27 (cadquery .box centres at origin, so we shift after).
    wall = (
        cq.Workplane("XY")
        .box(WALL_LX, WALL_TY, WALL_HZ, centered=(False, False, False))
    )

    # Left corner column: X = -COL_X..0
    col_left = (
        cq.Workplane("XY")
        .box(COL_X, COL_Y, COL_H, centered=(False, False, False))
        .translate((-COL_X, 0.0, 0.0))
    )

    # Right corner column: X = 8.5..8.77
    col_right = (
        cq.Workplane("XY")
        .box(COL_X, COL_Y, COL_H, centered=(False, False, False))
        .translate((WALL_LX, 0.0, 0.0))
    )

    # Fuse into one solid so gmsh sees a single connected volume; this keeps
    # the analysis_template's *SOLID SECTION simple (single composite material).
    fused = wall.union(col_left).union(col_right)

    cq.exporters.export(fused, str(OUT_STEP))
    print(f"Wrote {OUT_STEP.name}")

    meta = {
        "selectors": {
            # Foundation: Z = 0 (existing strip footing assumed rigid).
            "NFIXED": {"face": "z_min", "tol_mm": 0.001},
            # Roof / top of wall: Z = 3.0 m. Used only for displacement print.
            "NTOP":   {"face": "z_max", "tol_mm": 0.001},
        },
        "jobname": "model",
        "notes": (
            "URM (E=3 GPa) + RC (E=21 GPa) homogenized into single composite "
            "material URM_RC for the linear-elastic equivalent-static check. "
            "Multi-material refinement is left to the agent's submission."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    print(f"Wrote {OUT_META.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
