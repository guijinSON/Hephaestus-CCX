# 039 - FIA Appendix J Article 253 Rally Rollcage

Submission-agnostic CalculiX eval kit for a closed-cockpit rally car rollcage
(GVW 1200 kg), governed by FIA Appendix J Article 253 (2013 edition),
Section 8 (Rollover protective structures).

## What the agent submits

The agent provides a single file: `build.py` (a CadQuery script) that emits

- `out.step` - 3D solid geometry of the rollcage. The reference build models
  the minimum FIA Art 253-8.3 lattice as solid cylinders / boxes:
  - Main hoop (MH) at x = 0     : 2 vertical legs + top crossbar (45 mm OD)
  - Front hoop (FH) at x = +850 : 2 vertical legs + top crossbar (45 mm OD)
  - 2 upper longitudinal connectors (38 mm OD) at z = 1350
  - 1 diagonal across the main-hoop plane (38 mm OD)
  - 2 lower side bars at z = 0  (38 mm OD)
  - 6 floor mounting feet, each a 120 mm x 100 mm x 3 mm steel plate
- `meta.json` - sidecar matching `schemas/meta.schema.json`, declaring four
  NSET selectors keyed by name:
    - `NFIXED` - all six floor mount footprints (z_min plane)
    - `NLC1`   - top of main hoop, vertical-load patch
    - `NLC2`   - top of front hoop, rearward-load patch (near MH-FH cnx)
    - `NLC3`   - main hoop driver-side leg at shoulder height (~915 mm)
  and `material = "STEEL_S235"` to splice into the analysis template.

Tubes are modeled as **solid cylinders** (not thin-walled annuli) for mesh
robustness with gmsh's HXT tetrahedral algorithm. The closed-form structural
verification in `check.py` uses the **spec hollow OD x wall** sections, so
the FEM idealisation does not affect the pass/fail outcome.

## How the runner grades

`scripts/ccx_eval/grade_ccx.py` runs the pipeline:

1. `python build.py` -> `out.step`, `meta.json`
2. gmsh `-3 out.step` -> `mesh.inp` (3D solid C3D* tetrahedral elements)
3. `wire_bcs.py` splices `mesh.inp` + `meta.json` + `analysis_template.inp`
   -> `model.inp` (auto-builds `Eall`, populates NFIXED / NLC1..3)
4. `ccx_2.22 model` -> `model.dat`, `model.frd`
5. `python check.py` -> PASS/FAIL per requirement, exit 0 only if all PASS

Note: `*CLOAD` on a solid mesh applies the listed value PER node in the NSET
(CalculiX convention), so the mesh-count-dependent total cannot be controlled
exactly without preprocessing. The analysis template therefore applies a
**small representative per-node load** (Fz = -50 N on NLC1, Fx = +25 N on
NLC2, Fy = +25 N on NLC3) so the FEM deck always assembles and solves
regardless of the mesh node count, while R1..R4 are verified **closed-form**
in `check.py` from the spec OD x wall sections under the full FIA loads
(7.5 W, 3.5 W, 3.5 W). This matches FIA Art 253 homologation analytical
practice.

## Requirements (all must PASS)

| ID | Type       | Metric                                   | Limit       | Source                                     |
|----|------------|------------------------------------------|-------------|--------------------------------------------|
| R1 | structural | max sigma LC1 (vertical 7.5 W on MH)    | <= 235 MPa  | tube yield Fty (S235); FIA Art 253-8.3.2.1 |
| R2 | structural | max sigma LC2 (rearward 3.5 W on FH)    | <= 235 MPa  | tube yield Fty; FIA Art 253-8.3.2.1.2      |
| R3 | structural | max sigma LC3 (lateral 3.5 W on MH)     | <= 235 MPa  | tube yield Fty; FIA Art 253-8.3.2.1.3      |
| R4 | structural | deformation at load patch (each LC)      | <= 50 mm    | FIA homologation criterion (Art 253-8.3.2) |
| R5 | geometric  | tube OD x wall in FIA permitted table    | binary      | FIA Art 253 Drawing 253-37 / Table 253     |
| R6 | geometric  | mount foot count/area/thickness/bolts    | binary      | FIA Art 253-8.3.2.4                        |
| R7 | mass       | total rollcage tube mass                 | <= 45 kg    | practical weight target (not mandated)     |

### Closed-form structural models (R1..R4)

- **LC1 vertical 88,290 N on MH top crossbar**: simply-supported beam of
  length L = shoulder width (1200 mm) with midspan point load.
  - M_max = F * L / 4
  - sigma_b = M / S, where S = I / c for the spec hollow tube
  - delta = F * L^3 / (48 * E * I)

- **LC2 rearward 41,202 N on FH top**: each FH leg cantilevers from the
  fixed mount foot up to z = H_hoop (1350 mm); load shared by the two
  legs.
  - M_max per leg = (F/2) * H_hoop
  - sigma_b = M / S
  - delta = (F/2) * H_hoop^3 / (3 * E * I)

- **LC3 lateral 41,202 N on MH at z_shoulder = 915 mm**: cantilever from
  fixed base to load point.
  - M_max = F * z_shoulder
  - sigma_b = M / S
  - delta = F * z_shoulder^3 / (3 * E * I)

For the spec sections (45 x 2.5 mm hollow tube): I = 75,625 mm^4,
S = 3,361 mm^3.

### Honest interpretation of FEM FAIL

The minimum FIA Art 253 lattice (MH + FH + upper connectors + one MH
diagonal + lower side bars) is the **catalog-permitted topology** but
lacks the in-cockpit cross-bracing (door bars, X-bracing, harness bar,
windscreen pillar tubes) that every homologated FIA Art 253 cage carries
in practice. Closed-form bending stresses for the permitted 45 x 2.5
and 38 x 2.5 sections under the FIA homologation loads exceed the 235
MPa yield - this matches the well-known result that a cage of "permitted
tubes" still fails homologation unless the full Art 253-8.3 bracing
pattern is included. The kit reports R1..R3 as FAIL transparently.

R5 (tube table compliance), R6 (mount foot geometry), and R7 (mass
budget) are binary closed-form/catalog checks and PASS for the spec.

## Load cases (FIA homologation, quasi-static)

| LC  | Direction                       | Force            | Point of application                |
|-----|---------------------------------|------------------|-------------------------------------|
| LC1 | -Z (vertical down)              | 7.5 W = 88,290 N | MH top crossbar, mid-span           |
| LC2 | +X (rearward)                   | 3.5 W = 41,202 N | FH top, near MH-FH connection       |
| LC3 | +Y (lateral, driver-to-passenger) | 3.5 W = 41,202 N | MH driver-side leg at z = 915 mm    |

W = 1200 kg * 9.81 m/s^2 = 11,772 N.

## FIA permitted tubes (cold-drawn seamless non-alloy carbon steel)

Per FIA Art 253 Drawing 253-37 / Table 253 (UTS >= 350 N/mm^2):

| OD (mm) | wall (mm) | Use                            |
|---------|-----------|--------------------------------|
| 45      | 2.5       | main hoop, front hoop, lateral |
| 50      | 2.0       | main hoop, front hoop, lateral |
| 38      | 2.5       | other components               |

## Citations

- FIA Appendix J Article 253 (2013):
  https://www.fia.com/sites/default/files/regulation/file/253%20(2013).pdf
- Cold-drawn seamless tubing (CDS) reference:
  https://www.industrialtube.com.au/post/rethinking-roll-cage-tubing-a-comparative-analysis-of-cds-dom-and-hfiw-erw

## Files

- `spec.json`              - eval item metadata (kept verbatim)
- `notes.md`               - design notes + closed-form derivation log
- `build.py`               - **agent-side** CadQuery rollcage builder + meta
- `analysis_template.inp`  - eval-side CCX deck (S235 material, BCs, 3 *STEPs)
- `check.py`               - eval-side closed-form R1..R7 verifier
- `model.inp` / `model.dat` / `model.frd` - generated by the runner
- `README.md`              - this file
