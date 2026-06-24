"""
IRC 2026 rover manipulator upper-arm link - reference submission.

Welded 6061-T6 closed-box section, axis along +X:
    L = 600 mm pivot-to-pivot
    H = 60 mm (Z, strong / bending axis)
    W = 40 mm (Y, weak axis)
    t = 3 mm uniform wall

Coordinates (mm):
    X: axial,  0 .. L                 (shoulder x=0, elbow x=L)
    Y: width, -W/2 .. +W/2
    Z: height, -H/2 .. +H/2

Outputs:
    out.step  - single solid (closed-box shell as a hollow brick)
    meta.json - selectors for wire_bcs.py
"""

import json

import cadquery as cq

# ---------------------------------------------------------------------------
# Dimensions (mm)
# ---------------------------------------------------------------------------
L = 600.0
H = 60.0
W = 40.0
T = 3.0

# ---------------------------------------------------------------------------
# Closed-box solid: outer brick minus inner brick.
# Walls = 3 mm uniform, capped at both ends so x_min and x_max are full
# rectangular faces (the 30 mm bearing-boss reinforcement is idealised
# as part of the closed shell at each end).
# ---------------------------------------------------------------------------
outer = (
    cq.Workplane("YZ")
      .rect(W, H)
      .extrude(L)
)

# Inner cavity is shorter than the outer extrude so the end-caps remain solid
# (length L - 2*T leaves a 3 mm-thick endcap at each pin end).
inner = (
    cq.Workplane("YZ")
      .workplane(offset=T)
      .rect(W - 2 * T, H - 2 * T)
      .extrude(L - 2 * T)
)

link = outer.cut(inner)

cq.exporters.export(link, "out.step")
print(f"Wrote out.step  (L={L}, H={H}, W={W}, t={T} mm)")

# ---------------------------------------------------------------------------
# meta.json - tells wire_bcs.py which faces are which.
#
# NFIXED  : distal (shoulder) end-cap, x = 0
# NLOAD   : proximal (elbow)   end-cap, x = L
# NALL    : every node (used by *INITIAL CONDITIONS / *TEMPERATURE)
# ---------------------------------------------------------------------------
meta = {
    "selectors": {
        "NFIXED": {"face": "x_min", "tol_mm": 0.05},
        "NLOAD":  {"face": "x_max", "tol_mm": 0.05},
        "NALL":   {"all": True},
    },
    "material": "AL6061T6",
    "jobname": "model",
    "notes": (
        "IRC 2026 rover manipulator upper-arm link, welded 6061-T6 closed-box, "
        "60 x 40 mm OD x 3 mm wall x 600 mm length. Distal (x=0) clamped, "
        "proximal (x=L) loaded. HAZ band split is applied downstream in "
        "check.py via x-coordinate (25 mm at each end)."
    ),
}

with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)
print("Wrote meta.json")
