"""Sample agent submission for 134_hv10_fs_switzerland_wheel_hub.

Builds a simplified Aluminum 7075-T6 monolithic wheel hub idealized as a
hollow cylinder (annulus) with axis along +z:

    OD          = 150 mm  (outer wheel-mount flange diameter)
    ID          =  55 mm  (central twin-tapered-bearing bore)
    Length      =  90 mm  (total hub axial envelope)

Coordinate frame (mm):
    +z  hub axial / wheel spin axis
    +x  lateral (cornering body force direction)
    +y  vertical (cornering vertical bump direction)
    base: z = 0 (wheel-mount face); top: z = 90 (brake-flange face)

Exports out.step plus meta.json with NSET selectors that match the
NSET names referenced in analysis_template.inp:

    NFIXED  - inner-bore cylindrical band (axle/spline reaction; clamped)
    NWHEEL  - z = 0 face nodes (wheel-stud mount face -- vertical/lateral
              cornering load + longitudinal traction)
    NBRAKE  - z = L face nodes (brake-flange face -- tangential brake torque
              proxy + drive-torque proxy)

The agent may submit a richer STEP (with the 5-stud bolt circle, 8-bolt
brake-bolt circle, splined ID, pilot diameter, etc.) so long as meta.json
still names NFIXED, NWHEEL, and NBRAKE on the same load-introduction
regions and the OD/ID/length envelope is preserved.

Inner-bore selector: a tight axis-aligned bounding box of half-width
(ID/2 + 0.5 mm) around the central axis on both x and y. Because the
hub body is an annulus with r in [27.5, 75], a body node at radius r
satisfies r >= 27.5; only inner-bore-surface (or near-bore-volume)
nodes have BOTH |x| <= 28 and |y| <= 28 — outer-cylinder nodes always
have at least one coordinate well above 28 (e.g. (75,0) or (53,53)).
This bbox approach is robust to gmsh tet placement on the inner
cylindrical surface.
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometric parameters (mm) -- per spec.json envelope (OD 160 / 90 length)
# Reference geometry uses OD 150 (wheel-flange OD), ID 55 (bearing bore),
# length 90 (axial envelope); the 5-stud / 8-bolt / spline features are
# omitted for the eval skeleton (the agent CAD may include them).
# ---------------------------------------------------------------------------
OD_MM = 150.0       # outer diameter
ID_MM = 55.0        # inner-bore diameter
LZ_MM = 90.0        # axial length

R_OUT = OD_MM / 2.0  # 75.0
R_IN = ID_MM / 2.0   # 27.5

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def build_hub() -> cq.Workplane:
    """Hollow cylindrical hub, axis along +z, base at z=0."""
    outer = (
        cq.Workplane("XY")
        .circle(R_OUT)
        .extrude(LZ_MM)
    )
    inner = (
        cq.Workplane("XY")
        .circle(R_IN)
        .extrude(LZ_MM)
    )
    return outer.cut(inner)


def write_meta() -> dict:
    """Write meta.json with NSET selectors for NFIXED, NWHEEL, NBRAKE.

    NFIXED selector: AABB of half-width (R_IN + 0.5) on x and y, full axial
    span. Only inner-bore surface (and a thin near-bore volume layer) lie
    inside this bbox — every outer-cylinder body node has either |x| or |y|
    well above R_IN + 0.5.
    NWHEEL selector: thin z = 0 slab face.
    NBRAKE selector: thin z = LZ slab face.
    """
    bore_half = R_IN + 0.5  # 28.0 mm (slight tol over R_IN = 27.5)

    meta = {
        "jobname": "model",
        "material": "AL7075T6",
        "selectors": {
            # Inner-bore cylindrical band: bbox tighter than outer radius but
            # wider than inner radius so all bore-surface nodes are captured
            # while every outer-cylinder node is excluded. The narrow-square
            # AABB excludes outer-cylinder nodes by virtue of geometry: a
            # node at (R_OUT, 0) has |x| = R_OUT >> bore_half, so it is
            # outside the bbox; the only nodes whose BOTH |x| and |y| are
            # <= bore_half belong to the inner cylinder (and a thin near-
            # bore volume layer of similar radial extent).
            "NFIXED": {
                "box": [
                    -bore_half, -bore_half, -1.0,
                    bore_half, bore_half, LZ_MM + 1.0,
                ],
                "tol_mm": 0.0,
            },
            # Wheel-stud-mount face (z = 0): thin slab capturing the bottom
            # face of the hub (where the wheel attaches in the reference
            # frame).
            "NWHEEL": {"face": "z_min", "tol_mm": 0.5},
            # Brake-flange face (z = LZ): thin slab capturing the top face
            # of the hub (where the brake rotor attaches).
            "NBRAKE": {"face": "z_max", "tol_mm": 0.5},
        },
        "geometry": {
            "OD_mm": OD_MM,
            "ID_mm": ID_MM,
            "length_mm": LZ_MM,
            "z_min_mm": 0.0,
            "z_max_mm": LZ_MM,
        },
        "notes": (
            "FS Switzerland HV10 7075-T6 rear driven wheel hub, OD 150 / "
            "ID 55 / length 90 mm hollow annulus, axis +z. NFIXED selects "
            "inner-bore nodes via a (R_IN + 0.5)-wide bbox (excludes outer-"
            "cylinder nodes by geometry). NWHEEL = z_min face (wheel-stud "
            "mount). NBRAKE = z_max face (brake-flange mount). Loads are "
            "applied per-node via *CLOAD in analysis_template.inp; the "
            "tangential brake-torque is approximated as a per-node Fy "
            "value because CCX *CLOAD on a NSET cannot encode true "
            "rotational tangential direction."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    hub = build_hub()
    cq.exporters.export(hub, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}")
    print(f"  NFIXED via inner-bore bbox half-width = {R_IN + 0.5:.2f} mm "
          f"(R_IN = {R_IN:.1f} mm, R_OUT = {R_OUT:.1f} mm)")
    print(f"  NWHEEL via z_min face (wheel-stud mount)")
    print(f"  NBRAKE via z_max face (brake-flange mount)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
