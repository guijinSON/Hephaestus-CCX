# 050 - ABS HSC Part 3 (2013) Forward Bottom Hull Panel S19

Submission-agnostic CalculiX eval kit for an Al 5083-H116 stiffened hull
bottom plating panel in the forward region (Station 4, x/L = 0.3) of a
15 m, 15-tonne planing craft at 35 knots, governed by ABS Rules for
Building and Classing High-Speed Craft Part 3 (2013 July).

## What the agent submits

The agent provides exactly one file: `build.py` (a CadQuery script) that
emits

- `out.step` - solid 3D geometry of a 500 x 1200 mm plate stiffened by
  two longitudinal L-bars (long edges) and two transverse T-frames
  (short edges). The reference build uses the trial scantling from the
  spec: 5 mm plate, L 50x50x5 longitudinals, T 80x40x6 frames; the ribs
  are extruded as solid bodies and fused to the plate so the gmsh mesh
  produces a single 3D body of C3D tet elements.
- `meta.json` - sidecar matching `schemas/meta.schema.json`, with
    - four NSET selectors (`NFIX_XMIN`, `NFIX_XMAX`, `NFIX_YMIN`,
      `NFIX_YMAX`) for the four outer side faces of the panel that
      represent the simply-supported edge connection to adjacent
      framing
    - one `pressure_surfaces` entry (`SURF_NLOAD`) for the wetted
      (sea-side) face at z = 0, populated by `wire_bcs.py` as a
      `*SURFACE, TYPE=ELEMENT` block from the C3D4 tet faces
    - `material` of `"AL5083"` to splice into the analysis template

## How the runner grades

`scripts/ccx_eval/grade_ccx.py` runs the pipeline:

1. `python build.py` -> `out.step`, `meta.json`
2. gmsh `-3 out.step` -> `mesh.inp` (3D solid C3D* elements)
3. `wire_bcs.py` splices `mesh.inp` + `meta.json` + `analysis_template.inp`
   -> `model.inp` (auto-builds `Eall`, populates the four NFIX NSETs and
   the SURF_NLOAD pressure surface)
4. `ccx_2.22 model` -> `model.dat`, `model.frd`
5. `python check.py` -> PASS/FAIL per requirement

The original eval used S4 shells and produced a spurious 277 MPa peak
at the rigid stiffener-frame corner, exactly the local geometric
singularity that ABS warns against (the welded permissible 120 MPa is
nominal membrane+bending, not corner peaks). The C3D solid mesh
distributes that intersection stress over volume and gives a clean
nominal vM that R2 actually checks against.

## Requirements (R1..R7)

| ID | Type | Metric | Limit | Source |
|----|------|--------|-------|--------|
| R1 | scantling   | plate thickness vs ABS Section 3.3 minimum    | t >= 5 mm        | ABS HSC Part 3 Section 3.3 |
| R2 | strength    | LC1 slamming max vM in plate                  | <= 120 MPa       | ABS welded-Al permissible (Table 3.2) |
| R3 | strength    | LC3 combined slamming + hull-girder bending  | <= 215 MPa       | Fty welded 5083-H116 |
| R4 | service     | LC2 mid-panel deflection (40 kPa hydrostatic) | <= s/300 = 1.67 mm | ABS serviceability |
| R5 | stiffener   | longitudinal stiffener Z (with effective flange) | >= 25 cm^3   | ABS HSC Part 3 Section 4 |
| R6 | fatigue     | weld-toe stress range at 1e6 cycles           | <= 71 MPa        | ABS Class D S-N (fatigue appendix) |
| R7 | mass        | mass per unit hull area                        | <= 25 kg/m^2     | spec design objective |

R1, R5, R7 are evaluated closed-form in `check.py` from spec geometry
and ABS Part 3 formulas (no FEM needed). R2, R4, R6 are evaluated from
`model.frd` -- specifically:

- R2 reads max nodal von Mises among plate-band nodes
  (z in [0, T_PLATE]) under LC1 (50 kPa) directly.
- R4 takes the LC1 max |UZ| in the plate band and **scales linearly**
  to LC2 (40 kPa) by 0.8.
- R6 takes the LC1 max plate vM and **scales linearly** to the LC4
  fatigue range (20 kPa) as a hot-spot proxy.
- R3 superposes LC1 plate vM with the spec hull-girder bending
  contribution of 60 MPa at the panel location (conservative, since
  the hull-girder stress is mostly axial and plate vM is mostly
  transverse bending; a square-root-sum-of-squares would be less
  conservative but the simple sum bounds the combined demand).

## Load cases (per ABS HSC Part 3 Section 2.3 at Station 4)

| LC  | Description                                  | Pressure / Stress |
|-----|----------------------------------------------|-------------------|
| LC1 | slamming                                     | 50 kPa uniform    |
| LC2 | hydrostatic + hydrodynamic service           | 40 kPa uniform    |
| LC3 | LC1 superposed with hull-girder bending      | 50 kPa + 60 MPa   |
| LC4 | fatigue equivalent range (1e6 cycles)        | 20 kPa range      |

The FEM template runs only LC1 (50 kPa) directly; LC2, LC3, LC4 are
linearly scaled or superposed in `check.py` (valid for linear elastic
analysis).

## Material

Al 5083-H116 marine-grade plate, **welded** condition (ABS Part 3 Tab 3.2):

| Property      | Value                       |
|---------------|------------------------------|
| E             | 70 GPa  = 70 000 MPa         |
| nu            | 0.33                         |
| rho           | 2700 kg/m^3 = 2.7e-9 t/mm^3  |
| Fty (welded)  | 215 MPa                      |
| Ftu           | 305 MPa                      |
| sigma_perm    | 120 MPa  (ABS welded allow.) |

## Expected verdict (reference build, 5 mm plate + L 50x50x5)

The trial scantling from the spec is **deliberately undersized** at the
stiffener: ABS Part 3 Section 4 requires Z >= ~25 cm^3 but the L
50x50x5 with effective plate flange yields Z ~= 4.5 cm^3, so the
reference build is expected to PASS R1/R2/R3/R4/R6/R7 and **FAIL R5**.
This is documented in `notes.md` and matches the spec narrative
("the L 50x50x5 trial is undersized; it must be upsized to ~75x75x6
or equivalent tee to meet ABS"). Agents iterating on this kit will
typically upsize the longitudinal stiffener to clear R5.

## Citations

- ABS Rules for Building and Classing High-Speed Craft Part 3 (2013 July)
  https://ww2.eagle.org/content/dam/eagle/rules-and-guides/archives/special_service/61_hsc_2013/hsc_part_3_e-july13.pdf
- ABS HSNC 2003 Part 3 (sister rules, cross-check on slamming/scantling)
- Aluminum Association / ABS Table 3.2 welded-aluminum permissible stress
- L-bar 50x50x5 section properties: Z ~ 3.05 cm^3 bare, ~4.5 cm^3 with
  500 mm effective plate flange (parallel-axis theorem from check.py)

## Files

- `spec.json`              - eval item metadata (kept verbatim)
- `notes.md`               - design notes + closed-form derivation log
- `build.py`               - **agent-side** CadQuery panel + meta builder
- `analysis_template.inp`  - eval-side CCX deck (material, BCs, *STATIC step)
- `check.py`               - eval-side R1..R7 verifier (FEM + closed-form)
- `model.inp` / `model.dat` / `model.frd` - generated by the runner
- `README.md`              - this file
