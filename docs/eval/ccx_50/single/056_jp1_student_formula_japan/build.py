"""Sample submission for 056_jp1_student_formula_japan.

Builds an approximate Student Formula Japan 2025 space frame chassis as a
simplified solid envelope, exports out.step, and writes meta.json with the
NSET selectors expected by analysis_template.inp.

This is a deliberately rough placeholder geometry — the FSJ 2025 chassis is
a tubular space frame, but for a CCX linear-static check on the limits given
in spec.json (R1..R6 are dominated by closed-form geometric / material
checks), a solid bounding envelope is adequate. An agent may replace this
with a more faithful tubular STEP, as long as meta.json still names the
expected NSETs and the chassis fits within an X-aligned envelope where:
    x_min  = rear axle plane (NFIXED)
    x_max  = front bulkhead plane (NLOAD)

Coordinate frame (mm):
    +x  forward (rear of chassis -> front)
    +y  outward (port-starboard)
    +z  upward
"""
from __future__ import annotations
import json
import math
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometric parameters (FSAE 2025 rulebook F.1.2 / F.3 / IA test)
# ---------------------------------------------------------------------------
WHEELBASE_MM       = 1600.0   # >= 1525 (R3)
TRACK_MM           = 1200.0   # nominal FSAE
CHASSIS_HEIGHT_MM  = 1100.0   # main hoop top
CHASSIS_WIDTH_MM   = 600.0    # interior width (driver compartment)
WALL_THK_MM        = 25.4     # nominal main hoop OD (R1) — used as wall thickness of envelope shell
FRONT_BULKHEAD_X   = WHEELBASE_MM   # NLOAD face (front bulkhead surrogate)
REAR_AXLE_X        = 0.0            # NFIXED face

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_chassis() -> cq.Workplane:
    """Build a solid envelope approximating the FSJ 2025 space frame.

    Geometry: a hollow rectangular envelope from rear axle (x=0) to front
    bulkhead (x=WHEELBASE_MM). Outer shell wall thickness ~ tube OD.
    """
    outer_y = CHASSIS_WIDTH_MM
    outer_z = CHASSIS_HEIGHT_MM

    # Solid outer block centered on y=0, z=outer_z/2.
    chassis = (
        cq.Workplane("YZ")
        .workplane(offset=REAR_AXLE_X)
        .center(0.0, outer_z / 2.0)
        .rect(outer_y, outer_z)
        .extrude(WHEELBASE_MM)
    )

    # Hollow out the cabin so the envelope is a thin-wall box (bigger surface,
    # less stiff -> non-trivial linear-static result).
    inner_y = outer_y - 2 * WALL_THK_MM
    inner_z = outer_z - 2 * WALL_THK_MM
    cabin_x_start = REAR_AXLE_X + WALL_THK_MM
    cabin_x_len = WHEELBASE_MM - 2 * WALL_THK_MM
    cabin = (
        cq.Workplane("YZ")
        .workplane(offset=cabin_x_start)
        .center(0.0, outer_z / 2.0)
        .rect(inner_y, inner_z)
        .extrude(cabin_x_len)
    )
    chassis = chassis.cut(cabin)
    return chassis


def write_meta() -> dict:
    """Write meta.json with NSET selectors for NFIXED (rear) and NLOAD (front)."""
    meta = {
        "jobname": "model",
        "material": "STEEL1010",
        "selectors": {
            "NFIXED": {"face": "x_min", "tol_mm": 0.5},
            "NLOAD":  {"face": "x_max", "tol_mm": 0.5},
        },
        "notes": (
            "FSJ 2025 chassis envelope. NFIXED is the rear axle plane "
            "(x_min); NLOAD is the front bulkhead plane (x_max), where "
            "the IA quasi-static push is applied in -x."
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
