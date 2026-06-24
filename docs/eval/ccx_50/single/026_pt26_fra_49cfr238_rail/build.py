"""Sample agent submission for 026_pt26_fra_49cfr238_rail.

Builds a single representative collision post (200 deep x 150 wide x 15 mm
wall hollow box, 2.6 m tall, A572 Gr 50) and writes meta.json with face
selectors that match the NSET names referenced in analysis_template.inp
(NFIXED, NLOAD).

Geometry is intentionally simplified per spec.json's note that the FRA
49 CFR 238 verification is closed-form (sigma = M/Z bending margin lives
in check.py); the FEM is a supporting check that the post stays elastic
under the LC3 load distribution.  The full Tier I car body has two
collision posts plus two corner posts plus an end beam, but only one
collision post is modeled here — it is the governing member for LC3/LC4.

Origin: post base centroid at (0,0,0); +x = longitudinal car direction
(direction of LC3 horizontal force); +z = upward along the post.
"""
import json
import cadquery as cq

# Section: outer 200 (x, longitudinal) x 150 (y, transverse), wall t=15 mm
OUT_X = 200.0
OUT_Y = 150.0
T = 15.0
HEIGHT = 2600.0  # 2.6 m post length

# LC3 load plane: 18 in = 457 mm above underframe (post base).
LC3_Z = 457.0

# Build the hollow box: outer prism minus inner prism.
outer = cq.Workplane("XY").box(OUT_X, OUT_Y, HEIGHT, centered=(True, True, False))
inner = cq.Workplane("XY").box(
    OUT_X - 2 * T, OUT_Y - 2 * T, HEIGHT, centered=(True, True, False)
)
post = outer.cut(inner)

cq.exporters.export(post, "out.step")

meta = {
    "selectors": {
        # Underframe attachment plane: post base, fully fixed (welded).
        "NFIXED": {"face": "z_min", "tol_mm": 0.5},
        # LC3 load-introduction band at z = 457 mm (18 in) above the
        # underframe.  face_eq picks all nodes within tol of the plane.
        "NLOAD":  {"face_eq": "z", "value": LC3_Z, "tol_mm": 5.0},
    },
    "material": "A572GR50",
    "notes": (
        "FRA 49 CFR 238 Tier I commuter rail collision post: 200x150x15 mm "
        "hollow box, 2.6 m cantilever, A572 Gr 50.  Single representative "
        "post (the governing member for LC3/LC4); end beam and corner posts "
        "omitted — pass/fail driven by closed-form sigma=M/Z in check.py."
    ),
}
with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print("wrote out.step + meta.json")
