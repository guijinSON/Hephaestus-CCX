# 252_a12_asme_hpvc_rps -- ASME HPVC Tandem Roll Protection System

Submission-agnostic CalculiX eval kit for the ASME Human-Powered Vehicle
Challenge tandem-rider Roll Protection System (RPS).  The agent ships
`build.py` (CadQuery) and the eval kit ships `analysis_template.inp` +
`check.py`; the shared runner at
`scripts/ccx_eval/grade_ccx.py` glues them together via gmsh + ccx.

## Pipeline

```
build.py  ->  out.step + meta.json
gmsh      ->  mesh.inp           (tet C3D mesh from STEP)
wire_bcs  ->  model.inp          (mesh + Eall + NSET blocks + template)
ccx       ->  model.dat / .frd
check.py  ->  PASS/FAIL per requirement
```

Run end-to-end:

```
/opt/anaconda3/envs/cadquery/bin/python \
    scripts/ccx_eval/grade_ccx.py \
    docs/eval/ccx_50/single/252_a12_asme_hpvc_rps
```

## Material

4130 normalized chromoly steel, `STEEL4130` in the deck:

| property | value |
|---|---|
| E | 205 000 MPa |
| nu | 0.29 |
| rho | 7.85e-9 t/mm^3 |
| yield | 460 MPa |
| ultimate | 670 MPa |

## Boundary conditions and load cases

`analysis_template.inp` references four NSETs that `build.py` exposes
through `meta.json` selectors.  `wire_bcs.py` resolves the selectors
against the meshed geometry:

| NSET | Selector role | Used in |
|---|---|---|
| `NFIXED` | 4 base mounts (z_min face) clamped 6-DOF | All steps |
| `NTOP_LOAD` | top of the front roll hoop | LC1, LC4 |
| `NSIDE` | shoulder height patch on the front-left leg | LC2, LC4 |
| `NHARNESS` | harness-attachment tab tip | LC3 |

Forces are applied per-node at unit magnitude; `check.py` rescales the
linear-elastic CCX response by `target_total_force / N_nodes_in_NSET`
so the assessed peaks correspond to the spec's actual loads.

| Step | Load case | Spec total | Direction |
|---|---|---|---|
| 1 | LC1 top | 2670 N | 12 deg from vertical, aimed at driver |
| 2 | LC2 side | 1330 N | horizontal at front-left shoulder |
| 3 | LC3 harness | 1334 N | rearward (+x) at harness tab |
| 4 | LC4 combined | LC1 + LC2 | superposed in `check.py` |

## Requirements (R1-R5)

| ID | Metric | Limit | Source |
|---|---|---|---|
| R1a | LC1 deflection @ top-load point | <= 51 mm | spec.json |
| R1b | LC1 max von Mises | <= 400 MPa | yield / 1.15 |
| R2a | LC2 deflection @ side-load point | <= 38 mm | spec.json |
| R2b | LC2 max von Mises | <= 400 MPa | yield / 1.15 |
| R3  | LC3 max von Mises | <= 400 MPa | yield / 1.15 |
| R4a | LC4 top deflection (superposed) | <= 51 mm | spec.json |
| R4b | LC4 side deflection (superposed) | <= 38 mm | spec.json |
| R4c | LC4 max von Mises (upper-bound) | <= 447 MPa | UTS / 1.5 |
| R5  | Frame mass (tube-corrected) | <= 7.5 kg | spec.json |

LC4 metrics use linear-elastic superposition of the LC1 and LC2 scaled
responses (exact for the *STATIC analysis); the directly-assessed step 4
stress is bounded by `s_LC1 * vm_LC1 + s_LC2 * vm_LC2`.

## Sample build.py geometry

The sample submission models the RPS as solid square bars whose side
length matches the bending I of the real 25.4 mm OD x 2.41 mm wall
chromoly tube (`a = (12 * I_tube)^(1/4) ~ 19.33 mm`).  This keeps the
solid clean for tetrahedral meshing while preserving bending stiffness.
`check.py` rescales the bar volume to the equivalent tube mass via
`A_tube / A_bar = 0.466`.

The frame is two unbraced main hoops (front + rear, 1050 mm tall, 600
mm wide, 2200 mm apart) plus a 100 mm harness tab cantilevered from the
rear hoop top; longitudinal bracing is omitted to stay inside the 7.5 kg
envelope.  All seven primitive bars are fused into a single STEP solid.

## Files

| File | Provider | Role |
|---|---|---|
| `analysis_template.inp` | eval kit | material, BCs, 4x *STATIC steps |
| `check.py` | eval kit | parses model.dat, rescales, prints R1-R5 |
| `spec.json` | eval kit | machine-readable spec |
| `build.py` | sample submission | CadQuery -> out.step + meta.json |
| `meta.json` | sample submission (output) | NSET selectors + material |
| `out.step`, `mesh.inp`, `model.inp`, `model.dat`, `model.frd` | runner outputs | |
| `grade.json`, `grade.log`, `check.log` | runner outputs | |
