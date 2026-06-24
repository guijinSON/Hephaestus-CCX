"""Sample agent submission for 034_s3_nasa_5020b_bolted_joint.

Builds a simplified two-plate stack (Ti-6Al-4V) and writes meta.json
with face selectors that match the NSET names referenced in
analysis_template.inp (NFIXED, NLOAD).

Geometry is intentionally simplified per spec.json's note that
NASA-STD-5020B verification is closed-form (margin equations live in
check.py); the FEM is a supporting check that plates remain elastic.
Bolt holes are omitted — the two plates are merged into one solid block
of overall envelope 100 x 60 x 16 mm so wire_bcs's z_min / z_max face
selectors map cleanly to the lower-plate underside and upper-plate top.
"""
import json
import cadquery as cq

PLATE_X = 100.0  # mm
PLATE_Y = 60.0   # mm
PLATE_T = 8.0    # mm (single plate thickness)
STACK_Z = 2 * PLATE_T  # 16 mm — two plates stacked, treated as one tied volume

# Single solid representing the bolted plate stack (holes omitted; the FEM
# is a corroborating coarse check, not a stress-concentration study).
stack = cq.Workplane("XY").box(
    PLATE_X, PLATE_Y, STACK_Z, centered=(True, True, False)
)

cq.exporters.export(stack, "out.step")

meta = {
    "selectors": {
        # Lower-plate bottom face — bolted to spacecraft deck (pinned).
        "NFIXED": {"face": "z_min", "tol_mm": 0.5},
        # Upper-plate top face — lumped per-bolt loads applied here as
        # a uniformly distributed *CLOAD (representative of the 40 kg
        # offset-mass reaction transmitted through the bolt group).
        "NLOAD":  {"face": "z_max", "tol_mm": 0.5},
    },
    "material": "TI64",
    "notes": (
        "NASA-STD-5020B 4-bolt Ti-6Al-4V bolted joint. Two 100x60x8 mm "
        "plates stacked into a single 16 mm-thick block (holes/bolt "
        "shanks omitted). FEM is supporting; pass/fail driven by closed-"
        "form Appendix A margins in check.py."
    ),
}
with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print("wrote out.step + meta.json")
