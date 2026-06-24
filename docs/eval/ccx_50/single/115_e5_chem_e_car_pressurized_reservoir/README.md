# 115_e5_chem_e_car_pressurized_reservoir - AIChE Chem-E-Car Pressurized Reservoir

## What this is

A submission-agnostic CalculiX FEM eval kit for the **AIChE Chem-E-Car**
benchmark pressurized reservoir: a monolithic seamless 6061-T6 aluminum
cylinder with integral hemispherical end caps that stores compressed air or
inert CO2 for a student chemistry-car competition vehicle. The agent
submits a CAD model of the vessel as `out.step` plus a `meta.json`
describing where the wetted inner surface is and which seed nodes pin out
the rigid-body modes; the eval harness meshes the STEP with gmsh, splices
the eval-side analysis template in, runs CalculiX 2.22, and grades the
results against the benchmark allowables encoded in `spec.json`.

## Governing standards / sources

* **AIChE 2026 Chem-E-Car Official Competition Rules**
  - <https://www.aiche.org/sites/default/files/docs/pages/1.21.26_chem-e-car_rules_src_final.pdf>
  - All pressurized components must be certified to MAWP > vehicle MOP;
    pneumatic test prohibited; benchmark adopts SF >= 2.0 on yield.
* **AIChE 2020 Chem-E-Car Regional Safety Rules** (most explicit numerical
  pressure-test guidance: hydrostatic 1.3 x MOP normal, 1.5 x MAWP for
  damaged-vessel recertification):
  <https://aiche.cbe.iastate.edu/files/2020/09/Chem-E-Car-Safety-Rules.pdf>
* **AIChE 2019 Chem-E-Car Safety Rules**:
  <https://www.aiche.org/sites/default/files/media/document/chem-e-car_safety_rules_2019_final_rev1.pdf>
* **ASME BPVC Section VIII Div.1 UG-23** (long-term aluminum allowable =
  yield/3.0; used for the LC2 operating-pressure check, R2).
* **AIChE Chem-E-Car Safety Lecture (Crowl, 2010)**:
  <https://aissmscoe.com/wp-content/uploads/2018/09/Chemi-e-Car-Student_Training.pdf>

## Requirements (from `spec.json`)

| ID  | Metric                                  | Limit       | Op  | Derivation                                                              |
| --- | --------------------------------------- | ----------- | --- | ----------------------------------------------------------------------- |
| R1  | LC1 proof peak von Mises                | 138 MPa     | <=  | Yield 276 / 2.0 (benchmark SF >= 2 on pressurized reservoirs)           |
| R2  | LC2 operating peak von Mises            | 92 MPa      | <=  | Yield 276 / 3.0 (ASME VIII Div.1 UG-23 long-term aluminum allowable)    |
| R3  | First 5 natural frequencies             | 250 Hz      | >=  | Avoid resonance with 5..200 Hz drivetrain sine sweep                    |
| R4  | External-collapse buckling load factor  | 3.0         | >=  | Vacuum inside / 101 kPa outside accidental venting; SF 3.0              |
| R5  | LC3 thermal-overpressure peak vM at 50C | 183 MPa     | <=  | Yield-at-50C / 1.5 (5% knockdown at 50 C, ductile derate)               |
| R6  | Reservoir mass                          | 0.5 kg      | <=  | Vehicle mass budget partition (vehicle 2.0 kg, payload 0.5 kg)          |
| R7  | Internal volume                         | 625 mL      | >=  | 1.25 x 500 mL water payload (gas headspace allowance)                   |

Material spec (Al 6061-T6): `E = 68.9 GPa`, `nu = 0.33`, `rho = 2700 kg/m^3`,
`Fy = 276 MPa`, `Ftu = 310 MPa`. The eval template uses
`E = 68900 N/mm^2` and `rho = 2.7e-9 t/mm^3` (mm-MPa-N-tonne unit set).

## How the eval works

```
build.py  ->  out.step + meta.json
              |
              v
         gmsh -3   ->  mesh.inp (*NODE / *ELEMENT only, C3D4 tets)
              |
              v
       wire_bcs.py  (mesh.inp + meta.json + analysis_template.inp)
              |
              v
            model.inp  ->  ccx_2.22  ->  model.dat / model.frd
                                              |
                                              v
                                         check.py  ->  PASS/FAIL/SKIP per R1..R7
```

`scripts/ccx_eval/grade_ccx.py` orchestrates all stages and writes
`grade.json` summarising each stage's return code.

## Eval-side files

| File                       | Role                                                           |
| -------------------------- | -------------------------------------------------------------- |
| `spec.json`                | Authoritative requirements (R1..R7) + load cases               |
| `analysis_template.inp`    | CCX deck - Al 6061-T6 `*MATERIAL`, `*SOLID SECTION` on `Eall`, three-pin `*BOUNDARY` on `NSEED1/2/3`, three `*STATIC` + `*DSLOAD` pressure steps on `SINNER`, plus a `*FREQUENCY` step (5 modes) |
| `check.py`                 | Post-processor for `model.dat` / `.frd`; closed-form Barlow + Windenburg-Trilling cross-checks; prints PASS/FAIL/SKIP per requirement |
| `notes.md`                 | Source URLs, reference-run results, and the documented R6 mass-vs-geometry spec inconsistency |
| `build.py`                 | Sample agent submission - reference cadquery script             |

`analysis_template.inp` references `Eall` (auto-built by `wire_bcs.py`
from gmsh's C3D* ELSETs) plus the four NSETs and one SURFACE specified
by the agent's `meta.json`:

* `NSEED1`, `NSEED2`, `NSEED3` - three-pin rigid-body suppression seeds
  (`UX/UY/UZ` + `UY/UZ` + `UZ` = 6 DOF total, no net external reaction;
  the internal pressure load is self-equilibrated).
* `NINNER` - inner-surface (wetted) NSET, retained for `*NODE PRINT`
  diagnostics.
* `SINNER` - element-face surface on the inner wall, populated by
  `wire_bcs.py`'s `pressure_surfaces` selector. Required for `*DSLOAD`
  pressure on solid elements (CCX 2.22's `*SURFACE TYPE=NODE` is only
  valid for cyclic-symmetry / contact, never for pressure).

The four `*STEP`s map directly to spec.json requirements:

| Step | Type        | Load                          | Maps to    |
| ---- | ----------- | ----------------------------- | ---------- |
| 1    | `*STATIC`   | `*DSLOAD SINNER, P, -1.034`   | LC1 -> R1  |
| 2    | `*STATIC`   | `*DSLOAD SINNER, P, -0.689`   | LC2 -> R2  |
| 3    | `*STATIC`   | `*DSLOAD SINNER, P, -0.900`   | LC3 -> R5  |
| 4    | `*FREQUENCY`| 5 modes (with three-pin BCs)  | R3         |

R4 (external-collapse buckling) is intentionally evaluated **closed-form**
in `check.py` via Bresse long-tube and Windenburg-Trilling short-cylinder
formulas (see notes.md). CCX 2.22 `*BUCKLE` for closed pressure vessels
is numerically unreliable on coarse tet meshes (rigid-body-like apex modes
contaminate the geometric-stiffness eigenvalues), so the analytical
pressure is the primary R4 acceptance criterion. R6 (mass) and R7
(internal volume) are likewise closed-form on the spec geometry.

## What the agent must produce

`build.py` (this directory) is the submission entry point. When run with
`cwd=workdir` it must produce:

1. `out.step` - STEP AP242 of the monolithic vessel (cylinder + integral
   hemi caps; NPT ports are local features and may be omitted).
2. `meta.json` validating against
   [`schemas/meta.schema.json`](../../../../schemas/meta.schema.json),
   providing the `selectors` and `pressure_surfaces` referenced by
   `analysis_template.inp`.

### `meta.json` example

```json
{
  "selectors": {
    "NSEED1": {"sphere": [-142.0, 0.0, 0.0, 18.0]},
    "NSEED2": {"sphere": [+142.0, 0.0, 0.0, 18.0]},
    "NSEED3": {"sphere": [0.0,    53.0, 0.0, 18.0]},
    "NINNER": {"box":    [-139.0, -49.0, -49.0,  139.0,  49.0,  49.0]}
  },
  "pressure_surfaces": {
    "SINNER": {"box": [-139.0, -49.0, -49.0,  139.0,  49.0,  49.0]}
  },
  "material": "AL6061T6",
  "jobname": "model"
}
```

* `pressure_surfaces` is the new selector type used here: `wire_bcs.py`
  finds every C3D4/C3D10 face whose three corner nodes all match the
  selector (here: a node in the inner cavity AABB), and emits a
  `*SURFACE, NAME=SINNER, TYPE=ELEMENT` block listing those element
  faces. The cavity contains no mesh interior, so the box selector
  matches only the inner (wetted) surface of the wall.

### Conventions for this item

* Cylinder axis along `+X`, centred on origin: cylindrical straight
  section spans `x in [-90, +90]`; hemispherical caps centred at
  `(+/-90, 0, 0)` with inner radius 50 mm; outer radius 53 mm.
* Wetted inner surface is bounded by `r_in <= 50` and the same axial
  span (cylinder cavity + two hemispherical end-cavities). A single
  AABB `[-139, -49, -49, +139, +49, +49]` lies wholly inside the wall
  and so its node selector picks exactly the inner-surface nodes.

## Verifying locally

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./docs/eval/ccx_50/single/115_e5_chem_e_car_pressurized_reservoir
```

The runner exits 0 iff every stage (build / gmsh / wire / ccx / check)
returned 0. `check.log` contains the per-requirement PASS/FAIL/SKIP
output. With the as-specified geometry (3 mm wall, 100 mm ID, 180 mm
straight, 50 mm hemi caps, 6061-T6) the reference run reports:

| Req | Value             | Limit       | Status |
| --- | ----------------- | ----------- | ------ |
| R1  | ~18 MPa (LC1)     | <= 138 MPa  | PASS   |
| R2  | ~12 MPa (LC2)     | <=  92 MPa  | PASS   |
| R3  | first 5 ~3 kHz    | >= 250 Hz   | PASS   |
| R4  | SF ~34 (analytic) | >= 3.0      | PASS   |
| R5  | ~16 MPa (LC3)     | <= 183 MPa  | PASS   |
| R6  | 742 g             | <= 500 g    | FAIL   |
| R7  | 1937 mL           | >= 625 mL   | PASS   |

R6 fails because the spec's mass cap (500 g) is geometrically
inconsistent with the spec's wall thickness (3.0 mm) at the spec's
material density: a 3 mm 6061-T6 wall with this envelope masses 742 g.
See notes.md for the inconsistency analysis. The structural margins are
otherwise comfortable (LC1 stress ~13 % of yield, freqs ~12 x the
upper drivetrain band).
