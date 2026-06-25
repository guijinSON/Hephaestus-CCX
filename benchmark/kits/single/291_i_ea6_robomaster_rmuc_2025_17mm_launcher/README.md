# RMUC 2025 17 mm Launcher (single-part eval)

Item id: `291_i_ea6_robomaster_rmuc_2025_17mm_launcher`

A submission-agnostic CalculiX eval kit. The agent supplies a
cadquery `build.py` that emits `out.step` + `meta.json`; the shared
runner (`scripts/ccx_eval/grade_ccx.py`) meshes, splices BCs from
`analysis_template.inp`, runs `ccx_2.22`, then invokes `check.py`
to score against the seven pass/fail requirements in `spec.json`.

## Files in this directory

| File                    | Role                                                                 |
|-------------------------|----------------------------------------------------------------------|
| `spec.json`             | Authoritative spec (envelope, materials, load cases, limits R1..R7)  |
| `notes.md`              | Source provenance + RMUC 2025 rule references                        |
| `build.py`              | Reference cadquery submission (housing box + flywheel disc + meta)   |
| `analysis_template.inp` | Material + BC + load template; mesh+NSETs spliced by `wire_bcs.py`   |
| `check.py`              | Reads `model.dat` + `spec.json` -> prints PASS/FAIL per R1..R7       |
| `model.inp`             | Legacy hand-meshed deck retained for reference (not used by runner)  |

## Pass/fail map (R1..R7)

| Req | Source         | What it checks                                   | Limit                |
|-----|----------------|--------------------------------------------------|----------------------|
| R1  | FEM step 1     | Peak vM in housing under LC1 (1 kN recoil)       | <= 184 MPa           |
| R2  | closed-form    | Peak vM in 2024-T3 flywheel under LC2 (10 krpm)  | <= 230 MPa           |
| R3  | closed-form    | Flywheel radial growth under LC2                  | <= 0.15 mm           |
| R4  | FEM step 1+2   | Linear superposition vM in housing for LC1+LC3   | <= 200 MPa           |
| R5  | FEM step 3     | Peak vM in housing under LC4 (20 g lateral)      | <= 184 MPa           |
| R6  | closed-form    | Total launcher mass (housing + 2 fly + barrel)   | <= 600 g             |
| R7  | closed-form    | Barrel ID under LC3 (alpha * dT * D)             | 17.0..18.0 mm        |

The FEM corroborates LC1, LC3 and LC4 directly. LC2 (centrifugal
on the disc) and the bore growth in LC3 are evaluated analytically
because the housing FEM does not need to carry them and the
relevant closed-form expressions are tighter than what a coarse
disc mesh would give.

## Pipeline

```
build.py                    # emits out.step + meta.json
  |
  v
gmsh -3 out.step -> mesh.inp
  |
  v
wire_bcs.py mesh.inp meta.json analysis_template.inp model.inp
  |
  v
ccx_2.22 model              # produces model.dat / model.frd
  |
  v
check.py                    # parses model.dat + spec.json -> R1..R7
```

Run via the shared driver:

```
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./benchmark/kits/single/291_i_ea6_robomaster_rmuc_2025_17mm_launcher
```

## Required NSETs (from `meta.json`)

| NSET     | Selector type | Description                                                   |
|----------|---------------|---------------------------------------------------------------|
| `Nall`   | `{"all": true}` | Every node (used for `*INITIAL CONDITIONS` and `*TEMPERATURE`) |
| `NFIXED` | AABB box      | 4 turret-yoke bolt corners on the housing bottom (z=0)        |
| `NLOAD`  | AABB box      | Barrel-end face on the housing (x=150)                        |

## Load cases

| Step | LC  | Load                                                 |
|------|-----|------------------------------------------------------|
| 1    | LC1 | `*CLOAD NLOAD,1,-1000.0` (1 kN -x recoil)            |
| 2    | LC3 | `*TEMPERATURE Nall,333.15` (uniform +40 K)           |
| 3    | LC4 | `*DLOAD Eall,GRAV,196200,0,1,0` (20 g +y lateral)    |

Material is 6061-T6 (housing); `*ELSET HOUSING := Eall` so the
spec-named ELSET carries the property assignment while letting
`wire_bcs.py` keep its `Eall` auto-build of all gmsh `C3D*`
volume sets.

## Iterating

* `model.inp` (legacy hand-meshed) is preserved so the deck still
  runs without `build.py` -- useful for sanity-checking solver
  behaviour. The new pipeline overwrites `model.inp` when run via
  the shared driver, but the legacy version is in git history.
* Closed-form portions of `check.py` (R2, R3, R6, R7) are pure
  reads of `spec.json` and are independent of the agent's STEP, so
  changes to the build geometry only affect R1, R4 and R5.
