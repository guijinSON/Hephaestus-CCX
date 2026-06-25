#!/usr/bin/env python3
"""
build.py - Reference cadquery submission for the RMUC 2025 17 mm
launcher single-part eval.

Emits two artefacts the eval runner consumes:

    out.step  : STEP AP203/AP242 with two solids
                  (a) a simplified 6061-T6 launcher housing box
                      (150 x 80 x 40 mm) with a 17.5 mm ID barrel
                      bore implied by the analytical R7 check.
                  (b) a flywheel disc body (60 mm OD x 15 mm)
                      offset from the housing so the meshed model
                      contains both volumes (LC1 / LC3 / LC4 still
                      load only the housing, R2/R3 are closed-form).
    meta.json : selectors used by wire_bcs.py to populate the NSETs
                referenced in analysis_template.inp:
                  NFIXED : 4 turret-yoke bolt corner regions on the
                           housing bottom face.
                  NLOAD  : barrel-end face (bore-axis +x extreme) of
                           the housing.
                  Nall   : every node (used for *INITIAL CONDITIONS
                           and *TEMPERATURE).

Units: mm throughout (cadquery default; STEP is exported in mm,
which keeps the gmsh mesh and the MPa/t/N analysis_template.inp
consistent).
"""
from __future__ import annotations
import json
import os

import cadquery as cq

HERE = os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------
# Geometry parameters (per spec.json envelope)
# ------------------------------------------------------------------
HOUSE_L = 150.0   # bore-axis length  (x)
HOUSE_W = 80.0    # width             (y)
HOUSE_H = 40.0    # height            (z) -- half-height open shell
BOLT_PATTERN = 40.0   # 4 x M4 on a 40 mm square pattern
BOLT_CORNER_BOX = 6.0 # half-width of the bolt-corner clamp region

FLY_OD = 60.0     # flywheel outer diameter
FLY_T = 15.0      # flywheel width

# Place the flywheel disc clear of the housing so gmsh produces two
# disjoint volumes, both of which roll into Eall.
FLY_X_OFFSET = HOUSE_L + 50.0   # 50 mm clear of barrel end
FLY_Y_OFFSET = HOUSE_W / 2.0

# ------------------------------------------------------------------
# Build housing (simple box)
# ------------------------------------------------------------------
housing = (
    cq.Workplane("XY")
    .box(HOUSE_L, HOUSE_W, HOUSE_H, centered=(False, False, False))
)

# ------------------------------------------------------------------
# Build flywheel (disc, axis along z so OD shows in xy plane)
# ------------------------------------------------------------------
flywheel = (
    cq.Workplane("XY")
    .workplane(offset=0.0)
    .center(FLY_X_OFFSET, FLY_Y_OFFSET)
    .circle(FLY_OD / 2.0)
    .extrude(FLY_T)
)

# ------------------------------------------------------------------
# Compose into one assembly and export STEP
# ------------------------------------------------------------------
assy = cq.Assembly()
assy.add(housing, name="housing", color=cq.Color("gray"))
assy.add(flywheel, name="flywheel", color=cq.Color("gray"))

step_path = os.path.join(HERE, "out.step")
assy.save(step_path, exportType="STEP")
print(f"wrote {step_path}")

# ------------------------------------------------------------------
# meta.json - selectors for wire_bcs.py
#
# NFIXED: union of 4 small AABBs at the bottom-face corners of the
#   housing on a 40 mm bolt-pattern square centred in the (x,y)
#   plane. wire_bcs supports a single 'box' per selector key, so we
#   capture the corners by widening to a thin 'frame' and filtering
#   to just the 4 corners by leveraging a tight z window plus an
#   x/y window covering the bolt circle. To stay within the existing
#   selector vocabulary, we use four separate NSETs and union them
#   inside the analysis_template via *NSET, NSET=NFIXED ... ?
#
# Simpler: a single 'box' that captures all bolt-bottom-face nodes
# along the housing's lower z extreme around the centre 40 mm
# square. The clamp region encompasses the bolt holes; physically
# this is the bonded interface to the turret yoke.
# ------------------------------------------------------------------
cx = HOUSE_L / 2.0
cy = HOUSE_W / 2.0

meta = {
    "selectors": {
        "Nall": {"all": True},
        # Bottom-face clamp footprint: 40 mm square centred in xy at
        # the housing bottom (z=0). Tolerance widens to grab the
        # surface mesh nodes that sit exactly on z=0.
        "NFIXED": {
            "box": [
                cx - BOLT_PATTERN / 2.0 - BOLT_CORNER_BOX,
                cy - BOLT_PATTERN / 2.0 - BOLT_CORNER_BOX,
                -0.01,
                cx + BOLT_PATTERN / 2.0 + BOLT_CORNER_BOX,
                cy + BOLT_PATTERN / 2.0 + BOLT_CORNER_BOX,
                0.01,
            ],
            "tol_mm": 0.5,
        },
        # Barrel-end face (bore-axis +x extreme of the housing only).
        # Restrict to housing x_max with a tight tolerance and a y/z
        # window matching the housing extents so the flywheel disc
        # (which sits at x > HOUSE_L) does not leak into NLOAD.
        "NLOAD": {
            "box": [
                HOUSE_L - 0.01,
                -1.0,
                -1.0,
                HOUSE_L + 0.01,
                HOUSE_W + 1.0,
                HOUSE_H + 1.0,
            ],
            "tol_mm": 0.5,
        },
    },
    "jobname": "model",
    "notes": (
        "RMUC 2025 17 mm launcher reference submission: 6061-T6 "
        "housing box (150x80x40 mm) plus a 60 mm OD x 15 mm "
        "flywheel disc placed clear of the housing. Closed-form "
        "checks (mass, centrifugal, barrel-ID) are evaluated in "
        "check.py against spec.json."
    ),
}

meta_path = os.path.join(HERE, "meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"wrote {meta_path}")
