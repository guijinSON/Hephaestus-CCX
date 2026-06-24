"""ISO 10328 P5 transtibial prosthetic pylon - submission-agnostic CAD.

Geometry: hollow circular tube (the structurally critical pylon between the
proximal pyramid adapter and the distal pyramid clamp). The two pyramid
adapters are NOT modeled as solids; their mass is accounted analytically in
check.py against the R5 assembly-mass criterion.

Tube parameters (within the spec.json envelope):
    outer diameter   30.0 mm  (envelope 28-34 mm)
    wall thickness    2.5 mm  (envelope 1.5-3.0 mm for aluminum)
    length           250.0 mm (envelope 200-400 mm)
    axis             +Z, distal end at z=0, proximal end at z=L.

The distal end (z_min) is the NFIXED clamp face (fully restrained in the
ISO 10328 alignment jig idealisation). The proximal end (z_max) is the
NLOAD face where the principal static test force is applied.

Outputs:
    out.step    - STEP AP203/AP242 of the pylon tube.
    meta.json   - selectors NFIXED=z_min, NLOAD=z_max plus material name.
"""

from __future__ import annotations

import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometry parameters (mm)
# ---------------------------------------------------------------------------
OD_MM = 30.0
WALL_MM = 2.5
LENGTH_MM = 250.0

OUTER_R = OD_MM / 2.0
INNER_R = OUTER_R - WALL_MM


def build_tube() -> cq.Workplane:
    """Hollow circular tube, axis along +Z, base at z=0."""
    outer = cq.Workplane("XY").circle(OUTER_R).extrude(LENGTH_MM)
    inner = cq.Workplane("XY").circle(INNER_R).extrude(LENGTH_MM)
    return outer.cut(inner)


def main() -> None:
    here = Path(__file__).resolve().parent
    tube = build_tube()

    step_path = here / "out.step"
    cq.exporters.export(tube, str(step_path))

    meta = {
        "material": "AL6061T6",
        "jobname": "model",
        "selectors": {
            # Distal (foot side) clamp face - all DOFs fixed in the test rig.
            "NFIXED": {"face": "z_min", "tol_mm": 0.05},
            # Proximal (knee side) load face - principal test force applied.
            "NLOAD":  {"face": "z_max", "tol_mm": 0.05},
        },
        "notes": (
            "ISO 10328 P5 pylon tube. NFIXED = distal clamp (z_min); "
            "NLOAD = proximal pyramid interface (z_max). Principal static "
            "forces applied as -Z compression in analysis_template.inp."
        ),
    }
    (here / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"Wrote {step_path}")
    print(f"Wrote {here / 'meta.json'}")


if __name__ == "__main__":
    main()
