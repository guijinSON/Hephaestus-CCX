"""
AISC 360-22 W10x49 steel column - reference cadquery build.

Emits:
    out.step   - extruded W-shape (I-section) solid, 6000 mm tall along +Z
    meta.json  - selectors mapping NFIXED -> z_min face, NTOP -> z_max face,
                 plus material name "A992".

W10x49 nominal section dimensions (AISC Manual 15th ed, in -> mm):
    d  = 10.00 in = 254.0 mm   (overall depth)
    bf = 10.00 in = 254.0 mm   (flange width)
    tf =  0.560 in =  14.224 mm (flange thickness)
    tw =  0.340 in =   8.636 mm (web thickness)

Coordinate system (mm):
    +X : flange-width direction (weak-axis bending plane normal)
    +Y : section-depth direction (strong-axis bending plane normal)
    +Z : column axis, base at z=0, top at z=6000

The I-section is built as the union of three rectangles (top flange,
web, bottom flange) extruded over the column height. STEP-meshed by
gmsh into 3D solid elements; capacity checks (R1..R4) are closed-form
in check.py and do not require the FEM to match any AISC chapter
result. The FEM step is a sanity-check linear static under a small
per-node axial demand on NTOP.
"""

import json

import cadquery as cq

# ---------------------------------------------------------------------------
# Section dimensions (mm) - W10x49
# ---------------------------------------------------------------------------
D  = 254.0      # overall depth
BF = 254.0      # flange width
TF = 14.224     # flange thickness
TW = 8.636      # web thickness

H_COLUMN = 6000.0   # clear column height (mm)

# ---------------------------------------------------------------------------
# Build the W-shape cross-section as a closed wire on XY, then extrude +Z.
# Polyline goes around the I-shape outline (12 vertices).
# ---------------------------------------------------------------------------
half_bf = BF / 2.0
half_d  = D  / 2.0
half_tw = TW / 2.0
y_inner = half_d - TF   # top of bottom flange / bottom of top flange

#   y
#   ^                  +-----------------+      <- y = +half_d
#   |                  |   top flange    |
#   |              +-- +---+         +---+ --+   <- y = +y_inner
#   |              |       |         |       |
#   |              |       |   web   |       |
#   |              +-- +---+         +---+ --+   <- y = -y_inner
#   |                  |  bottom flange  |
#   |                  +-----------------+      <- y = -half_d
#   +----------> x
section_pts = [
    (-half_bf, -half_d),   # bottom-left of bottom flange
    ( half_bf, -half_d),   # bottom-right of bottom flange
    ( half_bf, -y_inner),  # top-right of bottom flange
    ( half_tw, -y_inner),  # right edge of web at bottom
    ( half_tw,  y_inner),  # right edge of web at top
    ( half_bf,  y_inner),  # bottom-right of top flange
    ( half_bf,  half_d),   # top-right of top flange
    (-half_bf,  half_d),   # top-left of top flange
    (-half_bf,  y_inner),  # bottom-left of top flange
    (-half_tw,  y_inner),  # left edge of web at top
    (-half_tw, -y_inner),  # left edge of web at bottom
    (-half_bf, -y_inner),  # top-left of bottom flange
]

column = (
    cq.Workplane("XY")
      .polyline(section_pts)
      .close()
      .extrude(H_COLUMN)
)

cq.exporters.export(column, "out.step")
print(f"Wrote out.step (W10x49 I-section, height {H_COLUMN:.0f} mm)")

# ---------------------------------------------------------------------------
# Sidecar selectors for wire_bcs.py
#   NFIXED : nodes on the base face (z = 0)
#   NTOP   : nodes on the top face (z = 6000)
# ---------------------------------------------------------------------------
meta = {
    "selectors": {
        "NFIXED": {"face": "z_min", "tol_mm": 0.05},
        "NTOP":   {"face": "z_max", "tol_mm": 0.05},
    },
    "material": "A992",
    "jobname":  "model",
    "notes":    "W10x49 I-section, 6.0 m tall, A992 steel; AISC 360-22 capacity checks are closed-form in check.py."
}

with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)
print("Wrote meta.json")
