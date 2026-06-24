"""Sample agent submission for 061_jp6_coma_wars.

Builds a brass C36000 spinning-top body (a solid cylinder, OD <= 20 mm,
H ~ 18 mm) and writes meta.json with face selectors that match the NSET
names referenced in analysis_template.inp (NFIXED, NLOAD).

The Coma Wars catalog requirements are envelope (R1: OD<=20, R2: H<=60),
manufacturing tolerance (R3: concentricity<=0.02 mm, declared analytically
since the spec explicitly excludes FEA for it), and structural (R4: no
plastic yield under LC1 50 N lateral collision at the widest diameter).

Geometry is intentionally a plain cylinder: the spec is envelope/MoI-driven
and check.py computes mass properties (mass, CG, Izz, concentricity)
analytically from this CAD, while the FEM corroborates that the brass
body remains elastic under LC1.
"""
import json
import math
import os

import cadquery as cq

OD_MM = 20.0          # Coma Wars standard-class envelope max
H_BODY_MM = 18.0      # body height; well below 60 mm envelope, keeps mass <= 50 g
RHO_KG_M3 = 8500.0    # Brass C36000

R = OD_MM / 2.0

# Solid brass cylinder, base at z=0 (rests on dohyo / tip contact at z_min).
top = (
    cq.Workplane("XY")
    .circle(R)
    .extrude(H_BODY_MM)
)

cq.exporters.export(top, "out.step")

# ----------------------------------------------------------------------
# Mass properties (analytic — spec mandates these are NOT from FEA).
# Volume V = pi R^2 H, mass m = rho * V, polar Izz = (1/2) m R^2.
# ----------------------------------------------------------------------
V_mm3 = math.pi * R * R * H_BODY_MM
V_m3 = V_mm3 * 1e-9
m_kg = RHO_KG_M3 * V_m3
m_g = m_kg * 1000.0
R_m = R * 1e-3
Izz_kg_m2 = 0.5 * m_kg * R_m * R_m
Izz_kg_mm2 = Izz_kg_m2 * 1e6
CG_height_mm = H_BODY_MM / 2.0

mass_properties = {
    "OD_mm": OD_MM,
    "height_mm": H_BODY_MM,
    "volume_mm3": V_mm3,
    "mass_g": m_g,
    "CG_height_mm": CG_height_mm,
    "polar_moment_Izz_kg_mm2": Izz_kg_mm2,
    # Concentricity is a manufacturing-declared tolerance for a turned
    # part on a precision lathe. Standard koma builders quote 0.005-0.01
    # mm; we declare 0.01 mm here (well within the 0.02 mm spec gate).
    "concentricity_mm_assumed": 0.01,
    "rho_kg_m3": RHO_KG_M3,
    # Mesh size fields are filled by the eval runner (gmsh) for reporting,
    # but check.py only needs the analytic block above. Provide harmless
    # placeholders so the spec-side check.py keys never KeyError.
    "n_nodes": 0,
    "n_elements": 0,
}
with open("mass_properties.json", "w") as f:
    json.dump(mass_properties, f, indent=2)

# ----------------------------------------------------------------------
# meta.json — face selectors for wire_bcs.py.
# NFIXED = bottom contact patch (z_min, dohyo/tip contact).
# NLOAD  = top-rim widest-diameter ring (z_max face, +X 50 N collision).
# ----------------------------------------------------------------------
meta = {
    "selectors": {
        "NFIXED": {"face": "z_min", "tol_mm": 0.05},
        "NLOAD":  {"face": "z_max", "tol_mm": 0.05},
    },
    "material": "BRASS_C36000",
    "notes": (
        "Coma Wars standard-class spinning-top body, solid Brass C36000 "
        "cylinder OD=20.000 mm, H=18.000 mm. NFIXED is the dohyo contact "
        "patch (z_min); NLOAD receives a 50 N lateral force in +X at the "
        "widest-diameter top rim (z_max). Concentricity, mass, CG, and "
        "polar Izz are analytic outputs of build.py (not FEA-derived) per "
        "the spec's verification rules."
    ),
}
with open("meta.json", "w") as f:
    json.dump(meta, f, indent=2)

print(f"wrote out.step + meta.json + mass_properties.json")
print(f"  OD={OD_MM} mm, H={H_BODY_MM} mm, mass={m_g:.2f} g, "
      f"Izz={Izz_kg_mm2:.3f} kg*mm^2")
