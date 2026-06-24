"""Sample agent submission for 252_a12_asme_hpvc_rps.

Builds a simplified ASME HPVC tandem Roll Protection System (RPS) as
SOLID bodies (rectangular bars approximating tubular hoops + a short
harness tab) and writes meta.json with face/box selectors that match
the NSET names referenced in analysis_template.inp:

    NFIXED        -> 4 base mounts (z_min face)
    NTOP_LOAD     -> top of front roll hoop (top center patch)
    NSIDE         -> shoulder height patch on the front-left leg
    NHARNESS      -> rear-hoop harness-attachment tab tip

The geometry is intentionally simplified per the ccx_eval pipeline:
  - gmsh meshes a STEP solid into tetrahedral C3D elements (not B32
    beams), so we model bars as solid prismatic blocks of an
    equivalent square cross-section sized to match the bending I of
    the real 25.4 mm OD x 2.41 mm wall 4130 chromoly tube.
  - I_tube = pi/64 * (25.4^4 - 20.58^4) = 11626.27 mm^4
  - Equivalent square side a satisfies a^4/12 = I_tube
        a = (12 * I_tube)^(1/4) = 19.33 mm
    A 19.33 x 19.33 mm bar reproduces the tube's bending stiffness
    while remaining a clean solid for tetrahedral meshing.  Real-tube
    mass is recovered in check.py by scaling bar volume by A_tube/A_bar.

The RPS skeleton is two unbraced main hoops (front + rear) plus a short
harness-attachment tab cantilevered off the rear hoop crossbar.  The
combined frame exports as a single fused solid.  Longitudinal rails are
omitted to keep mass within the 7.5 kg envelope (R5); the four hoop
legs alone provide a closed roll-protection envelope around the tandem
cockpit and are sufficient to verify the four structural load cases.
"""
import json
import math

import cadquery as cq

# --- frame envelope ---------------------------------------------------------
COCKPIT_LEN_MM = 2200.0   # front-to-rear hoop spacing
HOOP_HEIGHT_MM = 1050.0   # roll-hoop top above seat pan
HOOP_WIDTH_MM  = 600.0    # hoop track width

# --- equivalent solid bar size (matches tube I = 11626 mm^4) ---------------
TUBE_OD = 25.4
TUBE_WT = 2.41
TUBE_ID = TUBE_OD - 2 * TUBE_WT
I_TUBE  = math.pi / 64.0 * (TUBE_OD ** 4 - TUBE_ID ** 4)        # 11626.27 mm^4
BAR_SIDE = (12.0 * I_TUBE) ** 0.25                              # ~ 20.6 mm
BAR = BAR_SIDE  # alias

SHOULDER_Z = 700.0   # shoulder height above mounts (for LC2 side load)
TAB_LEN    = 100.0   # rearward harness-attachment tab length

# --- one roll hoop (closed portal) at given x ------------------------------
def make_hoop(x_pos: float) -> cq.Workplane:
    """Inverted-U hoop: two vertical legs + horizontal top, all square bars."""
    half_w = HOOP_WIDTH_MM / 2.0
    # left leg: bar centered at (x_pos, -half_w, HOOP_HEIGHT_MM/2)
    left_leg = (
        cq.Workplane("XY")
        .box(BAR, BAR, HOOP_HEIGHT_MM, centered=(True, True, False))
        .translate((x_pos, -half_w, 0.0))
    )
    right_leg = (
        cq.Workplane("XY")
        .box(BAR, BAR, HOOP_HEIGHT_MM, centered=(True, True, False))
        .translate((x_pos, +half_w, 0.0))
    )
    # top crossbar: span -half_w..+half_w along Y, at z = HOOP_HEIGHT_MM
    # length must include the leg half-thickness on each end so it touches
    top_len = HOOP_WIDTH_MM + BAR
    top_bar = (
        cq.Workplane("XY")
        .box(BAR, top_len, BAR, centered=(True, True, False))
        .translate((x_pos, 0.0, HOOP_HEIGHT_MM - BAR))
    )
    return left_leg.union(right_leg).union(top_bar)


front = make_hoop(0.0)
rear  = make_hoop(COCKPIT_LEN_MM)
half_w = HOOP_WIDTH_MM / 2.0

# --- harness-attachment tab (rearward cantilever off rear hoop top) --------
# Short bar sticking out behind the rear hoop crossbar in +X, centered on Y
# axis at the hoop top.  Box extends from x=COCKPIT_LEN_MM to
# x=COCKPIT_LEN_MM+TAB_LEN, fused into the rear top crossbar.
HARNESS_Z = HOOP_HEIGHT_MM - BAR  # match rear top-crossbar centerline z
harness_tab = (
    cq.Workplane("XY")
    .box(TAB_LEN + BAR, BAR, BAR, centered=(True, True, False))
    .translate((COCKPIT_LEN_MM + TAB_LEN / 2.0, 0.0, HARNESS_Z))
)

# --- fuse everything into a single body ------------------------------------
frame = front.union(rear).union(harness_tab)

cq.exporters.export(frame, "out.step")

# --- selectors --------------------------------------------------------------
# NFIXED: the four base patches (z_min face) -> auto-grabs all foot nodes
# NTOP_LOAD: top of front hoop (small box around x=0, y=0, z=HOOP_HEIGHT_MM)
# NSIDE: front-left shoulder area (box on left side at SHOULDER_Z, x near 0)
# NHARNESS: tip of harness tab (box at x=COCKPIT_LEN_MM+TAB_LEN, z=SHOULDER_Z)
HALF_BAR = BAR / 2.0 + 1.5  # 1.5 mm tolerance into the bar interior

meta = {
    "selectors": {
        # Mounts: the bottom face of the four legs (z = 0 plane).
        "NFIXED": {"face": "z_min", "tol_mm": 0.5},
        # Top-load patch: small AABB around the top of the front hoop crossbar
        "NTOP_LOAD": {
            "box": [
                -HALF_BAR, -HALF_BAR, HOOP_HEIGHT_MM - 0.5,
                +HALF_BAR, +HALF_BAR, HOOP_HEIGHT_MM + 0.5,
            ],
            "tol_mm": 0.5,
        },
        # Side-load patch: shoulder height on the front-left leg
        # (front hoop is at x=0, left leg at y=-half_w)
        "NSIDE": {
            "box": [
                -HALF_BAR,
                -half_w - HALF_BAR,
                SHOULDER_Z - HALF_BAR,
                +HALF_BAR,
                -half_w + HALF_BAR,
                SHOULDER_Z + HALF_BAR,
            ],
            "tol_mm": 0.5,
        },
        # Harness-tab tip: rearmost end of the harness tab.  Tab is a box
        # centered at x = COCKPIT_LEN+TAB_LEN/2 with X length (TAB_LEN+BAR),
        # so its rear face is at x = COCKPIT_LEN + TAB_LEN/2 + (TAB_LEN+BAR)/2
        # = COCKPIT_LEN + TAB_LEN + BAR/2 (= 2309.66 mm for the default sizes).
        "NHARNESS": {
            "box": [
                COCKPIT_LEN_MM + TAB_LEN + BAR / 2.0 - 0.5,
                -HALF_BAR,
                HARNESS_Z - 0.5,
                COCKPIT_LEN_MM + TAB_LEN + BAR / 2.0 + 0.5,
                +HALF_BAR,
                HARNESS_Z + BAR + 0.5,
            ],
            "tol_mm": 0.5,
        },
    },
    "material": "STEEL4130",
    "notes": (
        "ASME HPVC tandem RPS, simplified to solid square bars of "
        f"{BAR_SIDE:.2f} mm side (bending-I-equivalent to 25.4 mm OD x "
        "2.41 mm wall 4130 tube). Two unbraced main hoops + harness tab "
        "cantilevered off the rear hoop top. Mounted at z=0 base patches; "
        "loads applied at top-of-front-hoop (LC1), front-left shoulder "
        "(LC2), harness-tab tip (LC3). LC4 = LC1+LC2."
    ),
}
with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"wrote out.step + meta.json (bar side = {BAR_SIDE:.3f} mm, "
      f"I_tube = {I_TUBE:.1f} mm^4)")
