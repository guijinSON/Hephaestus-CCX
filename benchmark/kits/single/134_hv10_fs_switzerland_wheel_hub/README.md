# 134_hv10_fs_switzerland_wheel_hub - FS Switzerland 7075-T6 Rear Driven Wheel Hub

## What this is

A submission-agnostic CalculiX FEM eval kit for the **FS Switzerland (FSCH
2026) Class 1 CV** rear driven wheel hub — a monolithic forged-and-machined
billet aluminum 7075-T6 part for a collegiate Formula-class racecar. The
hub mounts a 13-inch wheel via a 5-stud / 100 mm PCD pattern, an integral
56 mm pilot, a brake-rotor flange (8 x M8 on 135 mm PCD), a twin-tapered
bearing bore (55 mm ID), and an internal half-shaft spline.

The agent submits a CAD model of the hub as `out.step` plus a `meta.json`
describing which faces are fixed / loaded; the eval harness meshes it
with gmsh, splices the eval-side analysis template in, runs CalculiX
2.22, and grades the results against the limits encoded in `spec.json`.

## Governing references

- **Formula Student Rules 2026 v1.0** (Formula Student Germany / DesignJudges):
  <https://www.formulastudent.de/fileadmin/user_upload/all/2026/rules/FS-Rules_2026_v1.0.pdf>
- **Formula SAE 2026 Rules** (FSAE Online):
  <https://www.fsaeonline.com/cdsweb/gen/DownloadDocument.aspx?DocumentID=278fd4d7-aa27-4e33-bc4a-090148e662a0>
- **Formula Student UK 2026 Rules v1.0**:
  <https://www.imeche.org/docs/default-source/1-oscar/formula-student/2026/rules/fsuk-2026-rules---v1-0.pdf>
- **FSAE wheel-hub design literature** (corner-load conventions):
  <https://pubs.aip.org/aip/acp/article/3325/1/030009/3365966/Design-and-analysis-of-FSAE-wheel-hub>,
  <https://www.ijert.org/design-analysis-and-fabrication-of-a-wheel-hub-for-a-formula-sae-vehicle-ijertv15is041776>
- **Material**: Aluminum 7075-T6 (E 71.7 GPa, nu 0.33, rho 2810 kg/m^3,
  Sy 503 MPa, Stu 572 MPa, S_e 159 MPa @ 5e8 cycles, MIL-HDBK-5J).

## Requirements (R1..R6)

| ID  | Metric                                | Limit        | Op  | Derivation / Source                                      | FEM source                              |
| --- | ------------------------------------- | ------------ | --- | -------------------------------------------------------- | --------------------------------------- |
| R1  | Max von Mises (LC1 cornering)         | 252 MPa      | <=  | yield/2.0 collegiate FSAE suspension SF (Sy=503 MPa)     | `model.dat` step 1 *EL PRINT S          |
| R2  | Max von Mises (LC2 launch)            | 252 MPa      | <=  | yield/2.0                                                | `model.dat` step 2 *EL PRINT S          |
| R3  | Max von Mises (LC3 brake peak)        | 335 MPa      | <=  | yield/1.5 brake-peak transient SF                         | `model.dat` step 3 *EL PRINT S          |
| R4  | First 6 natural frequencies           | informational| n/a | spec floor 500 Hz applies to full-feature CAD only        | `model.dat` step 4 *FREQUENCY (SKIP)    |
| R5  | Hub mass                              | 0.85 kg      | <=  | spec billet-machined monolith mass cap                    | **SKIP** (closed-form on agent CAD vol) |
| R6  | Wheel-plane tilt under LC1            | 0.15 mm      | <=  | spec camber-control flatness gate                         | **SKIP** (needs full-feature CAD)       |

The CCX deck runs LC1 / LC2 / LC3 as `*STATIC` per-node `*CLOAD` steps
plus a `*FREQUENCY` modal step. Mass (R5), flatness (R6), and the modal
floor (R4) are explicitly `SKIP`ped: they require the agent's actual
feature-resolved CAD (stud bores, brake-bolt holes, splined ID) which
the simplified hollow-annulus reference geometry does not provide.

## Load cases

| Case | Description           | Resultant load                                        | NSET applied   |
| ---- | --------------------- | ----------------------------------------------------- | -------------- |
| LC1  | cornering (1.6 g lat + 2 g vert)| 1286 N lateral (Fx) + 1608 N vertical (Fy)  | NWHEEL         |
| LC2  | launch (1.2 g long + drive torque)| 966 N longitudinal (Fz) + 200 N-m drive    | NWHEEL + NBRAKE|
| LC3  | brake peak (1.5 g decel + brake)  | 1206 N longitudinal (Fz) + 1500 N-m brake  | NWHEEL + NBRAKE|

Per-node `*CLOAD` magnitudes in `analysis_template.inp` are calibrated
to the reference annulus (~80 nodes per face). With a richer agent CAD,
mesh node counts will differ; absolute totals scale linearly. The
simplified annulus produces peak von Mises ~ 41 MPa under the LC3
envelope (12x margin to yield), so per-node scaling does not affect
the PASS verdict.

## How the eval works

```
build.py  ->  out.step + meta.json
              |
              v
         gmsh -3   ->  mesh.inp (*NODE / *ELEMENT only)
              |
              v
       wire_bcs.py  (mesh.inp + meta.json + analysis_template.inp)
              |
              v
            model.inp  ->  ccx_2.22  ->  model.dat / model.frd
                                              |
                                              v
                                         check.py  ->  PASS/FAIL/SKIP per R1..R6
```

`scripts/ccx_eval/grade_ccx.py` orchestrates all stages and writes
`grade.json` summarising each stage's return code.

## Eval-side files (this directory)

| File                       | Role                                                                                                                         |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `spec.json`                | Authoritative requirements (R1..R6) + load cases                                                                              |
| `analysis_template.inp`    | CCX deck — 7075-T6 `*MATERIAL`, `*SOLID SECTION` on `Eall`, `*BOUNDARY` on `NFIXED`, three `*STATIC` `*CLOAD` steps + `*FREQUENCY` |
| `check.py`                 | Post-processor for `model.dat`; prints PASS/FAIL/SKIP per requirement                                                          |
| `notes.md`                 | Source URLs and FEM idealizations                                                                                              |
| `build.py`                 | Sample agent submission — reference cadquery script (OD 150 / ID 55 / L 90 hollow annulus)                                    |
| `build_model.py`           | Legacy in-place model generator (predates the submission-agnostic kit; retained for historical reference)                     |
| `README.md`                | This file                                                                                                                     |

`analysis_template.inp` references `Eall`, `NFIXED`, `NWHEEL`, and
`NBRAKE` symbolically. `Eall` is auto-constructed by `wire_bcs.py`
from every `C3D*` ELSET in the gmsh-emitted `mesh.inp`. `NFIXED`,
`NWHEEL`, and `NBRAKE` are populated from the agent's `meta.json`
selectors.

## What the agent must produce

`build.py` (this directory) is the submission entry point. It must:

1. Build the wheel-hub CAD with cadquery 2.8 (or any STEP-emitting tool).
2. Export `out.step` (STEP AP242 preferred).
3. Write `meta.json` validating against
   [`schemas/meta.schema.json`](../../../../schemas/meta.schema.json).

### `meta.json` example

```json
{
  "jobname": "model",
  "material": "AL7075T6",
  "selectors": {
    "NFIXED": {"box": [-28.0, -28.0, -1.0, 28.0, 28.0, 91.0]},
    "NWHEEL": {"face": "z_min", "tol_mm": 0.5},
    "NBRAKE": {"face": "z_max", "tol_mm": 0.5}
  },
  "notes": "NFIXED = inner-bore band (axle/spline reaction); NWHEEL = wheel-stud face; NBRAKE = brake-flange face."
}
```

Selectors supported by `wire_bcs.py`: `face`, `face_eq`, `box`,
`sphere`, `all`. See the schema for the full grammar.

### Conventions for this item

- `+z` is the hub axial / wheel spin axis (wheel-stud face at `z_min`,
  brake-flange face at `z_max`).
- `+x` is lateral (cornering side-load direction); `+y` is vertical
  (cornering bump direction).
- The inner bore is the cylindrical surface at `r = ID/2`. NFIXED is a
  bounding box of half-width `R_IN + 0.5` on x and y over the full
  axial span — it captures inner-bore-surface (and a thin near-bore
  volume) nodes while excluding outer-cylinder nodes by geometry (a
  node at `(R_OUT, 0)` has `|x| = 75 >> 28`; only inner-cylinder
  nodes have BOTH `|x|` and `|y|` <= 28).
- The named material in `meta.json["material"]` is spliced into the
  `__MATERIAL__` placeholder in `analysis_template.inp`.

## Verifying locally

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./benchmark/kits/single/134_hv10_fs_switzerland_wheel_hub
```

The runner exits 0 iff every stage (build / gmsh / wire / ccx /
check) returned 0. `check.log` contains the per-requirement
PASS/FAIL/SKIP output; `grade.json` summarises stage rcs.
