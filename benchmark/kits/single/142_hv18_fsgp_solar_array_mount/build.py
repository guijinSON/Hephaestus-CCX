"""Sample agent submission for 142_hv18_fsgp_solar_array_mount.

Builds a simplified 6061-T6 solid envelope for the FSGP solar-array
canopy panel mount (160 x 80 x 40 mm block), exports out.step, and
writes meta.json with face selectors that match the NSET names
referenced in analysis_template.inp (NFIXED, NLOAD).

Geometry is intentionally simplified per notes.md: the real bracket is
a billet-machined monolith with an integral panel clamp, a 50 mm ID
rail half-saddle, and a 45-deg drag-strut stub. For an envelope-bound
CalculiX deck (lower-bound stiffness check on R1, R2, R5), a hex/tet
mesh of the bounding solid is sufficient. An agent may replace this
with a more faithful billet STEP, as long as meta.json keeps naming
the expected NSETs.

Coordinate frame (mm):
    +x  along the canopy rail, panel clamp at x=0, rail saddle at x=160
    +y  spanwise across the bracket
    +z  vertical (panel pressure acts in -z onto NLOAD)
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometric parameters (FSGP HV18 envelope; spec.json bounding_envelope_mm)
# ---------------------------------------------------------------------------
BLOCK_X_MM       = 160.0   # rail-axis length (NFIXED at x_max, NLOAD at x_min top)
BLOCK_Y_MM       =  80.0   # spanwise width
BLOCK_Z_MM       =  40.0   # height of envelope-bound block

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_bracket() -> cq.Workplane:
    """Build a 160 x 80 x 40 mm 6061-T6 envelope-bound bracket block.

    The block sits with its bottom on z=0; +z is up so panel pressure
    acts on the z_max face (NLOAD).
    """
    bracket = (
        cq.Workplane("XY")
        .box(
            BLOCK_X_MM, BLOCK_Y_MM, BLOCK_Z_MM,
            centered=(False, True, False),
        )
    )
    return bracket


def write_meta() -> dict:
    """Write meta.json with NSET selectors for NFIXED (rail saddle) and NLOAD (panel clamp)."""
    meta = {
        "jobname": "model",
        "material": "AL6061T6",
        "selectors": {
            # Rail-saddle face: x=160 (x_max). Half-saddle clamps to the
            # 50 mm OD canopy rail; here represented as the full x_max
            # face fully fixed in 3 DOF.
            "NFIXED": {"face": "x_max", "tol_mm": 0.5},
            # Panel-clamp footprint: top face (z_max) of the block.
            # Pressure load cases (LC1/LC2/LC3) are applied uniformly
            # over this face; spec maps them to a 4800 mm^2 panel-clamp
            # footprint, but template uses *DLOAD P which CalculiX
            # accumulates per element-face on the selected NSET.
            "NLOAD":  {"face": "z_max", "tol_mm": 0.5},
        },
        "notes": (
            "FSGP HV18 6061-T6 solar-array canopy panel mount, simplified "
            "as a 160 x 80 x 40 mm envelope-bound block. NFIXED = rail-"
            "saddle face (x_max), NLOAD = panel-clamp top face (z_max). "
            "Panel-clamp pressure load cases (LC1 0.0367 MPa, LC2 0.0733 "
            "MPa, LC3 0.1225 MPa) are applied as *DLOAD P on NLOAD."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    bracket = build_bracket()
    cq.exporters.export(bracket, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}: {json.dumps(meta, indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
