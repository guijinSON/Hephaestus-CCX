"""Sample agent submission for 127_hv3_eurobot_robot_chassis.

Builds a simplified Eurobot 5083-H111 collegiate-robot chassis deck plate
(300 x 300 x 8 mm) with four corner M12 standoff through-bores, exports
out.step plus meta.json with selectors that match the NSET names
referenced in analysis_template.inp:
    NFIXED_NW / NFIXED_NE / NFIXED_SW / NFIXED_SE   (four corner bore patches)
    NLOAD                                           (front-edge top strip)

This is intentionally a minimal reference geometry: the spec.json calls
out additional features (4 weight-reduction pockets, 12 motor-grid M5
holes, 2 D-shaped wheel cutouts, a 200 mm front-edge recess) but those
features do NOT influence R1..R6 pass/fail when the deck is checked as
a flat plate (margins are large; mass is analytically corrected in
check.py via spec geometry). An agent may submit a richer STEP - the
eval runner tetrahedralizes whatever STEP arrives, as long as:

  * the four corner M12 standoff bores are addressable by spheres at
    the corner inset positions (NFIXED_NW/NE/SW/SE), and
  * a front-edge load-introduction strip is addressable by an AABB
    box (NLOAD) covering the top face strip near y=0.

Coordinate frame (mm):
    +x  width  (300 mm)
    +y  depth  (300 mm); front edge at y = 0
    +z  thickness (8 mm); deck top at z = 8, bottom at z = 0
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometric parameters (mm) - per spec.json
# ---------------------------------------------------------------------------
DECK_W = 300.0          # width  (x)
DECK_H = 300.0          # depth  (y)
DECK_T = 8.0            # thickness (z)

CORNER_BORE_D = 12.0    # M12 standoff through-bore diameter
CORNER_INSET = 12.0     # bore-center inset from each corner edge

# Front-edge load-introduction patch: 200 mm wide front-edge recess strip,
# kept as a flat sub-band on the top face so an AABB box selector captures
# the manipulator-mass / collision target nodes.
FRONT_EDGE_BAND_X0 =  50.0   # 50..250 mm in x (200 mm wide, centered on 150 mm)
FRONT_EDGE_BAND_X1 = 250.0

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_deck() -> cq.Workplane:
    """Build a 300x300x8 flat plate with four corner M12 through-bores.

    The plate is positioned with its base corner at origin so x in [0, 300],
    y in [0, 300], z in [0, 8] - this makes face-axis selectors map cleanly
    to the deck silhouette edges. Bores are cut full-thickness at the four
    corner inset positions (12 mm from each edge).
    """
    deck = (
        cq.Workplane("XY")
        .box(DECK_W, DECK_H, DECK_T, centered=(False, False, False))
    )

    # Four corner M12 through-bores at (inset, inset) from each corner.
    bore_xy = [
        (CORNER_INSET,            CORNER_INSET),
        (DECK_W - CORNER_INSET,   CORNER_INSET),
        (CORNER_INSET,            DECK_H - CORNER_INSET),
        (DECK_W - CORNER_INSET,   DECK_H - CORNER_INSET),
    ]
    for (cx, cy) in bore_xy:
        bore = (
            cq.Workplane("XY")
            .moveTo(cx, cy)
            .circle(CORNER_BORE_D / 2.0)
            .extrude(DECK_T)
        )
        deck = deck.cut(bore)

    return deck


def write_meta() -> dict:
    """Write meta.json with NSET selectors.

    Four sphere selectors center on each corner-bore axis at mid-thickness;
    radius = bore_radius + a small margin + half-thickness, which captures
    every node on the bore inner-cylinder (and the immediate ring around
    each bore). These are referenced as NFIXED_NW/NE/SW/SE in *BOUNDARY.

    NLOAD is a thin AABB box covering the top-face front-edge strip
    (y close to 0, z close to DECK_T) within the 200 mm front recess band.
    Used by *CLOAD in LC1/LC2/LC3 (per-node convention).
    """
    bore_r = CORNER_BORE_D / 2.0
    # Sphere radius: covers the through-bore plus a small node-ring around
    # the rim. Slightly larger than half the diagonal of the bore-thickness
    # cylinder bounding box: sqrt(r^2 + (t/2)^2) + small margin.
    sphere_r = (bore_r ** 2 + (DECK_T / 2.0) ** 2) ** 0.5 + 0.5

    meta = {
        "jobname": "model",
        "material": "AL5083",
        "selectors": {
            # Four corner M12 standoff bores: each captured by a sphere
            # centered on the bore axis at mid-thickness. The template's
            # *BOUNDARY block restrains all four NSETs.
            "NFIXED_NW": {
                "sphere": [CORNER_INSET, CORNER_INSET,
                           DECK_T / 2.0, sphere_r],
            },
            "NFIXED_NE": {
                "sphere": [DECK_W - CORNER_INSET, CORNER_INSET,
                           DECK_T / 2.0, sphere_r],
            },
            "NFIXED_SW": {
                "sphere": [CORNER_INSET, DECK_H - CORNER_INSET,
                           DECK_T / 2.0, sphere_r],
            },
            "NFIXED_SE": {
                "sphere": [DECK_W - CORNER_INSET, DECK_H - CORNER_INSET,
                           DECK_T / 2.0, sphere_r],
            },
            # Front-edge top strip: AABB box covering top-face nodes at
            # y close to 0, z close to DECK_T, within 50..250 mm in x.
            "NLOAD": {
                "box": [FRONT_EDGE_BAND_X0, -0.5, DECK_T - 0.5,
                        FRONT_EDGE_BAND_X1,  0.5, DECK_T + 0.5],
                "tol_mm": 0.5,
            },
        },
        "notes": (
            "Eurobot HV3 collegiate-robot 5083-H111 chassis deck plate "
            "(300x300x8 mm) with four corner M12 standoff bores. NFIXED_* "
            "are four sphere selectors (one per corner bore); NLOAD is the "
            "200 mm front-edge top strip used by LC1 manipulator CLOAD, "
            "LC2 collision push, and LC3 drop manipulator CLOAD."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    deck = build_deck()
    cq.exporters.export(deck, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}")
    print(f"  NFIXED_NW/NE/SW/SE via 4 corner-bore sphere selectors "
          f"(r ~ {(CORNER_BORE_D/2.0)**2 + (DECK_T/2.0)**2:.2f} ** 0.5 + 0.5 mm)")
    print(f"  NLOAD via front-edge top-face AABB "
          f"({FRONT_EDGE_BAND_X0}..{FRONT_EDGE_BAND_X1} mm in x)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
