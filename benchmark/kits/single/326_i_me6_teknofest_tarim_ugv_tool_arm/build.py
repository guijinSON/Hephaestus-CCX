"""
TEKNOFEST Tarımsal İKA - Cantilevered Tool Arm - reference submission.

Welded 6061-T6 closed-box section, axis along +X:
    L = 800 mm shoulder-flange-to-tip cantilever
    H = 60 mm (Z, strong / vertical bending axis)
    W = 40 mm (Y, weak / lateral bending axis)
    t = 3 mm uniform wall (closed at both ends)

Coordinates (mm):
    X: axial,   0 .. L                 (shoulder x=0, tip x=L)
    Y: width,  -W/2 .. +W/2
    Z: height, -H/2 .. +H/2

Outputs:
    out.step  - single solid (closed-box hollow shell)
    meta.json - selectors NFIXED (shoulder face) and NLOAD (tip face)
                consumed by wire_bcs.py.
"""

import json
import os

import cadquery as cq

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Dimensions (mm) - per spec.json geometric_constraints
# ---------------------------------------------------------------------------
L = 800.0   # cantilever length, shoulder->tip
H = 60.0    # vertical (strong axis)
W = 40.0    # lateral (weak axis)
T = 3.0     # uniform wall thickness

# ---------------------------------------------------------------------------
# Closed-box solid: outer brick minus inner brick.
# Walls = 3 mm uniform, capped at both ends so x_min and x_max are full
# rectangular faces. This idealises the welded 4-bolt M10 shoulder flange
# at x=0 (clamp boundary) and the implement-attachment cap at x=L (load).
# Inner cavity is 2*T shorter than the outer extrude so a 3 mm endcap
# survives at each end.
# ---------------------------------------------------------------------------
outer = (
    cq.Workplane("YZ")
      .rect(W, H)
      .extrude(L)
)

inner = (
    cq.Workplane("YZ")
      .workplane(offset=T)
      .rect(W - 2 * T, H - 2 * T)
      .extrude(L - 2 * T)
)

arm = outer.cut(inner)

step_path = os.path.join(HERE, "out.step")
cq.exporters.export(arm, step_path)
print(f"wrote {step_path}  (L={L}, H={H}, W={W}, t={T} mm)")

# ---------------------------------------------------------------------------
# meta.json - tells wire_bcs.py which faces are which.
#
# NFIXED  : shoulder end-cap face, x = 0  (4-bolt M10 mount to UGV chassis)
# NLOAD   : tip end-cap face,    x = L  (interchangeable implement attachment)
# ---------------------------------------------------------------------------
meta = {
    "selectors": {
        "NFIXED": {"face": "x_min", "tol_mm": 0.05},
        "NLOAD":  {"face": "x_max", "tol_mm": 0.05},
    },
    "material": "AL6061T6",
    "jobname": "model",
    "notes": (
        "TEKNOFEST Tarımsal İKA cantilevered tool arm, welded 6061-T6 "
        "closed-box, 60 (Z, strong) x 40 (Y, weak) mm OD x 3 mm wall x "
        "800 mm length. Shoulder (x=0) clamped at the 4-bolt M10 flange; "
        "tip (x=L) holds the 5 kg interchangeable agricultural implement. "
        "R3 (Goodman fatigue) and R4 (mass) are evaluated closed-form in "
        "check.py; R1/R2/R5 come from the FEM solid C3D mesh."
    ),
}

meta_path = os.path.join(HERE, "meta.json")
with open(meta_path, "w") as f:
    json.dump(meta, f, indent=2)
print(f"wrote {meta_path}")
