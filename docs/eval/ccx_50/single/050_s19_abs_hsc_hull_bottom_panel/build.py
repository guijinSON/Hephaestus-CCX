"""Sample agent submission for 050_s19_abs_hsc_hull_bottom_panel.

Builds a stiffened Al 5083-H116 hull bottom plating panel as a single
fused solid (CadQuery), exports out.step + meta.json. The original
spec was modelled with S4 shells; the submission-agnostic kit instead
solves the panel as a 3D solid (C3D tet mesh from gmsh) so it goes
through the standard build.py -> gmsh -> wire_bcs -> ccx pipeline.

Topology (ABS HSC Part 3, Forward bottom panel S19):
  - Plate: 500 (x, transverse) x 1200 (y, longitudinal) x 5 (z) mm.
    z = 0 is the wetted (sea-side) face; z = +5 the dry side.
  - Longitudinal L-bar 50x50x5 stiffeners along the long edges
    (x = 0 and x = 500), web standing UP from plate top (z = 5..55),
    inboard horizontal flange 50 x 5 at the web top.
  - Transverse T 80x40x6 frames along the short edges (y = 0 and
    y = 1200), web standing UP (z = 5..85), top flange 40 x 6.
  All ribs are fused to the plate so the gmsh mesh produces a single
  3D body suitable for C3D tet elements.

Coordinate frame (mm):
    +X : transverse (between longitudinal stiffeners)
    +Y : longitudinal (between transverse frames)
    +Z : vertical, sea side at z = 0, dry/structural side at z >= +5

NSETs declared in meta.json match analysis_template.inp:
  NFIX_XMIN   - x = 0    outer face: simply supported edge
  NFIX_XMAX   - x = 500  outer face: simply supported edge
  NFIX_YMIN   - y = 0    outer face: simply supported edge
  NFIX_YMAX   - y = 1200 outer face: simply supported edge

Plus an element-face pressure surface (TYPE=ELEMENT, populated by
wire_bcs.py from the C3D tet mesh) for the slamming DLOAD:
  SURF_NLOAD  - bottom (wetted) face of the plate at z = 0

The four edge NSETs together represent the panel as supported by the
adjacent framing (ABS Part 3 Section 3 simply-supported boundary); the
plate-internal stiffener ribs supply the bending stiffness.

R1 (plate scantling), R5 (stiffener Z), R6 (weld fatigue), and R7
(mass/area) are evaluated closed-form in check.py from the spec
geometry and do not require the FEM. R2 (LC1 vM) and R4 (LC2
deflection scaled from LC1) come from model.frd.
"""
from __future__ import annotations
import json
from pathlib import Path

import cadquery as cq

# ---------------------------------------------------------------------------
# Geometry constants (mm) - ABS HSC Forward bottom panel S19
# ---------------------------------------------------------------------------
LX = 500.0          # plate transverse span (between longitudinal stiffeners)
LY = 1200.0         # plate longitudinal span (between transverse frames)
T_PLATE = 5.0       # plate thickness (trial per spec)

# Longitudinal L-bar 50x50x5 (along Y axis at x = 0 and x = LX)
H_LONG = 50.0       # L-bar web height (vertical leg)
T_LONG = 5.0        # L-bar web thickness
F_LONG = 50.0       # L-bar flange length (horizontal leg)

# Transverse T-frame 80x40x6 (along X axis at y = 0 and y = LY)
H_FRAME = 80.0      # T web height
T_FRAME = 6.0       # T web thickness
F_FRAME = 40.0      # T flange total width
TF_FRAME = 6.0      # T flange thickness

OUT_STEP = Path(__file__).resolve().parent / "out.step"
OUT_META = Path(__file__).resolve().parent / "meta.json"


def make_box(xlo, ylo, zlo, xhi, yhi, zhi):
    """Return a cq.Solid axis-aligned box from (xlo,ylo,zlo) to (xhi,yhi,zhi)."""
    dx = xhi - xlo
    dy = yhi - ylo
    dz = zhi - zlo
    return (cq.Workplane("XY")
            .workplane(offset=zlo)
            .center(xlo + dx / 2.0, ylo + dy / 2.0)
            .box(dx, dy, dz, centered=(True, True, False))
            .val())


def build_panel():
    """Plate + 2 longitudinal L-stiffeners + 2 transverse T-frames, fused."""
    parts = []

    # ---- Plate (z=0 .. z=T_PLATE) -----------------------------------------
    parts.append(make_box(0.0, 0.0, 0.0, LX, LY, T_PLATE))

    # ---- Longitudinal L-bar 50x50x5 along x = 0 (web in y-z plane) -------
    # Web: 5 mm thick along +x, 50 mm tall along +z, length LY along +y.
    parts.append(make_box(0.0, 0.0, T_PLATE,
                          T_LONG, LY, T_PLATE + H_LONG))
    # Inboard horizontal flange 50 mm wide along +x at the web top
    parts.append(make_box(0.0, 0.0, T_PLATE + H_LONG - T_LONG,
                          F_LONG, LY, T_PLATE + H_LONG))

    # ---- Longitudinal L-bar at x = LX (mirrored, flange points inboard) ---
    parts.append(make_box(LX - T_LONG, 0.0, T_PLATE,
                          LX, LY, T_PLATE + H_LONG))
    parts.append(make_box(LX - F_LONG, 0.0, T_PLATE + H_LONG - T_LONG,
                          LX, LY, T_PLATE + H_LONG))

    # ---- Transverse T-frame 80x40x6 along y = 0 ---------------------------
    # Web: centred on y=0+T_FRAME/2 thickness along +y, 80 mm tall, span x.
    # Position the web so its outer face lies on y=0.
    parts.append(make_box(0.0, 0.0, T_PLATE,
                          LX, T_FRAME, T_PLATE + H_FRAME))
    # Top flange centred on the web at the top: flange width F_FRAME along
    # +y centred on y = T_FRAME/2, thickness TF_FRAME.
    flange_y0 = T_FRAME / 2.0 - F_FRAME / 2.0
    parts.append(make_box(0.0, flange_y0, T_PLATE + H_FRAME - TF_FRAME,
                          LX, flange_y0 + F_FRAME,
                          T_PLATE + H_FRAME))

    # ---- Transverse T-frame at y = LY ------------------------------------
    parts.append(make_box(0.0, LY - T_FRAME, T_PLATE,
                          LX, LY, T_PLATE + H_FRAME))
    flange_y0_b = (LY - T_FRAME / 2.0) - F_FRAME / 2.0
    parts.append(make_box(0.0, flange_y0_b, T_PLATE + H_FRAME - TF_FRAME,
                          LX, flange_y0_b + F_FRAME,
                          T_PLATE + H_FRAME))

    # ---- Fuse all parts into one solid -----------------------------------
    solid = parts[0]
    for p in parts[1:]:
        solid = solid.fuse(p)
    return cq.Workplane(obj=solid)


def write_meta() -> dict:
    """Selectors mapping NSET names + pressure surface for analysis_template.inp."""
    meta = {
        "jobname": "model",
        "material": "AL5083",
        "selectors": {
            # Four outer side faces of the panel (where the panel is
            # connected to adjacent framing in the real hull) -> simply
            # supported (pinned in 3 translational DOF).
            "NFIX_XMIN":  {"face": "x_min", "tol_mm": 0.5},
            "NFIX_XMAX":  {"face": "x_max", "tol_mm": 0.5},
            "NFIX_YMIN":  {"face": "y_min", "tol_mm": 0.5},
            "NFIX_YMAX":  {"face": "y_max", "tol_mm": 0.5},
        },
        # Element-face surface for slamming DLOAD pressure on the wetted
        # (sea-side) face of the plate at z = 0. wire_bcs.py walks every
        # C3D4/C3D10 tet face and emits a *SURFACE, NAME=SURF_NLOAD,
        # TYPE=ELEMENT block referenced by *DLOAD in the template.
        "pressure_surfaces": {
            "SURF_NLOAD": {"face": "z_min", "tol_mm": 0.5},
        },
        "notes": (
            "ABS HSC Part 3 (2013) S19 forward bottom panel. Plate "
            f"{LX:.0f} x {LY:.0f} x {T_PLATE:.0f} mm Al 5083-H116, "
            f"L {F_LONG:.0f}x{H_LONG:.0f}x{T_LONG:.0f} longitudinal "
            "stiffeners on the long edges, T "
            f"{H_FRAME:.0f}x{F_FRAME:.0f}x{TF_FRAME:.0f} transverse "
            "frames on the short edges. Solid C3D tet mesh from gmsh; "
            "wire_bcs.py builds SURF_NLOAD (z_min element-face surface) "
            "for slamming DLOAD and four edge NSETs "
            "(x_min/x_max/y_min/y_max) all pinned to represent simple "
            "support from adjacent framing."
        ),
    }
    OUT_META.write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    panel = build_panel()
    cq.exporters.export(panel, str(OUT_STEP))
    meta = write_meta()
    print(f"wrote {OUT_STEP}")
    print(f"wrote {OUT_META}: NSETs = {list(meta['selectors'].keys())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
