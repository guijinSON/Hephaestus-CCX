"""Sample agent submission for 011_pt11_misumi_aluminum_frame_bracket.

Builds a Misumi HBLFSNB8-class large reinforcing corner bracket for
8-series 40x40 aluminum extrusions, exports out.step + meta.json.

Geometry (catalog approximation): an L-shape with two perpendicular legs.
Each leg is 80 mm long (along the extrusion axis), 40 mm wide (across the
slot face), 12 mm thick. The two legs meet at a shared corner cube and are
joined into one solid.

Coordinate convention chosen so that wire_bcs face selectors map cleanly:
    Vertical leg   : x in [-12, 0],  y in [  0, 80], z in [0, 40]
        outer (bolt-down) face is the global x_min plane at x = -12.
    Horizontal leg : x in [  0, 80], y in [-12,  0], z in [0, 40]
        far-end (load) face is the global x_max plane at x = 80.

With this layout `face: x_min` uniquely picks the vertical-leg bolt-down
face (NFIXED) and `face: x_max` uniquely picks the horizontal-leg end face
(NLOAD). 6063-T5 is referenced by the AL6063T5 *MATERIAL block in
analysis_template.inp.
"""
import json
import cadquery as cq

LEG_LEN = 80.0   # mm - leg length along the extrusion axis
LEG_WID = 40.0   # mm - bracket width (face that mates to 40-mm extrusion)
LEG_THK = 12.0   # mm - leg plate thickness

# Vertical leg: rectangular plate sitting in the y-z plane, 12 mm thick in -x
vertical_leg = (
    cq.Workplane("XY")
    .box(LEG_THK, LEG_LEN, LEG_WID,
         centered=(False, False, False))
    .translate((-LEG_THK, 0, 0))
)

# Horizontal leg: rectangular plate sitting in the x-z plane, 12 mm thick in -y
horizontal_leg = (
    cq.Workplane("XY")
    .box(LEG_LEN, LEG_THK, LEG_WID,
         centered=(False, False, False))
    .translate((0, -LEG_THK, 0))
)

# Union the two legs into a single L-bracket solid.
bracket = vertical_leg.union(horizontal_leg)

cq.exporters.export(bracket, "out.step")

meta = {
    "selectors": {
        # Vertical-leg outer face that bolts to the vertical 40x40 extrusion.
        # Globally the x_min plane sits at x = -12 (only the vertical leg
        # reaches that extreme), so this selector is unambiguous.
        "NFIXED": {"face": "x_min", "tol_mm": 0.5},
        # Horizontal-leg far-end face where the per-bracket reaction force
        # from the cantilever extrusion is applied. x_max plane is at x = 80
        # (only the horizontal leg reaches that extreme).
        "NLOAD":  {"face": "x_max", "tol_mm": 0.5},
    },
    "material": "AL6063T5",
    "notes": (
        "Misumi HBLFSNB8-class L-bracket, 80x80x40 mm, 12 mm leg thickness. "
        "FEM is supporting; pass/fail driven by closed-form catalog checks "
        "(per-bracket force/moment, extrusion deflection/stress, T-nut "
        "pull-out) in check.py."
    ),
}
with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print("wrote out.step + meta.json")
