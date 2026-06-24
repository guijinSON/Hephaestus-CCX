"""Sample agent submission for 186_sa3_kibocube.

Builds a simplified Al 6061-T6 1U CubeSat primary structure for JAXA
J-SSOD / KiboCUBE deployment per Cal Poly CDS Rev 14:
    Envelope: 100 x 100 x 113.5 mm
    Rails:    four 8.5 x 8.5 mm rails on the long corners
    Plates:   simplified top and bottom end-caps (2 mm thick)
    Material: Aluminum 6061-T6 (monolithic chassis)

Coordinate frame (mm):
    +X / +Y : in-plane (deployer cross-section)
    +Z      : along the rail / deployer push-out axis (z=0 = aft)

Outputs:
    out.step  - STEP AP242 of the chassis (rails + simplified plates)
    meta.json - selectors referenced by analysis_template.inp:
                  NFIXED -> rail z_min face (deployer interface)
                  NALL   -> every node (thermal initial condition)
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometry parameters (Cal Poly CDS Rev 14 1U + JX-ESPC-100134)
# ---------------------------------------------------------------------------
LX = 100.0     # mm - envelope X
LY = 100.0     # mm - envelope Y
LZ = 113.5     # mm - envelope Z (rail length)
RAIL_W = 8.5   # mm - corner rail cross-section
PLATE_T = 2.0  # mm - simplified top/bottom plate thickness

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_chassis() -> cq.Workplane:
    """Build the 1U chassis: 4 corner rails plus 2 end plates.

    The four 8.5 x 8.5 x 113.5 mm rails sit at the corners of the
    100 x 100 mm envelope. Top and bottom plates close the chassis at
    z = 0..PLATE_T and z = LZ-PLATE_T..LZ. End-cap plates and rails
    are unioned into a single monolithic solid (representative of the
    machined-from-billet skeletonized chassis described in the spec).
    """
    rail_xy = [
        (0.0,            0.0),
        (LX - RAIL_W,    0.0),
        (LX - RAIL_W,    LY - RAIL_W),
        (0.0,            LY - RAIL_W),
    ]

    rails = None
    for (rx, ry) in rail_xy:
        rail = (
            cq.Workplane("XY")
              .workplane(offset=0.0)
              .moveTo(rx + RAIL_W / 2.0, ry + RAIL_W / 2.0)
              .rect(RAIL_W, RAIL_W)
              .extrude(LZ)
        )
        rails = rail if rails is None else rails.union(rail)

    bot_plate = (
        cq.Workplane("XY")
          .workplane(offset=0.0)
          .moveTo(LX / 2.0, LY / 2.0)
          .rect(LX, LY)
          .extrude(PLATE_T)
    )
    top_plate = (
        cq.Workplane("XY")
          .workplane(offset=LZ - PLATE_T)
          .moveTo(LX / 2.0, LY / 2.0)
          .rect(LX, LY)
          .extrude(PLATE_T)
    )

    chassis = rails.union(bot_plate).union(top_plate)
    return chassis


def write_meta() -> dict:
    meta = {
        "selectors": {
            # Deployer-aft rail end (z = 0): J-SSOD rail interface,
            # idealised as fully encastred per analysis_template.inp.
            "NFIXED": {"face": "z_min", "tol_mm": 0.5},
            # All nodes - used by *INITIAL CONDITIONS, TYPE=TEMPERATURE
            # and by *TEMPERATURE in the thermal step.
            "NALL":   {"all": True},
        },
        "material": "AL6061T6",
        "jobname": "model",
        "notes": (
            "JAXA J-SSOD / KiboCUBE 1U CubeSat (Al 6061-T6). "
            "100 x 100 x 113.5 mm envelope, four 8.5 x 8.5 mm corner "
            "rails plus simplified 2 mm top/bottom plates. NFIXED is "
            "the rail z_min face (deployer-aft interface)."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    chassis = build_chassis()
    cq.exporters.export(chassis, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}: {json.dumps(meta, indent=2)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
