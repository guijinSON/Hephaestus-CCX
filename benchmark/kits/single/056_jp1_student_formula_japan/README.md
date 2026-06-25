# 056_jp1_student_formula_japan — FSJ 2025 Chassis + Impact Attenuator

## What this is

A submission-agnostic CalculiX FEM eval kit for the **Student Formula Japan
2025** primary structure (tubular space frame chassis + front Impact
Attenuator). The agent submits a CAD model of the chassis as `out.step`
plus a `meta.json` describing which faces are fixed / loaded; the
eval harness meshes it with gmsh, splices the eval-side analysis template
in, runs CalculiX 2.22, and grades the results against the rulebook
limits encoded in `spec.json`.

## Governing standard

* **Formula SAE Rules 2025 V1** (rule sections F.1, F.3, IA test, tilt test)
  - Mirror: <https://sites.usnh.edu/unh-precision-racing/wp-content/uploads/sites/136/2025/03/FSAE_Rules_2025_V1.pdf>
  - DRAFT (public comment): <https://www.fsaeonline.com/cdsweb/gen/DownloadDocument.aspx?DocumentID=349fa543-c65d-467f-ac12-730949d0dc85>
* **Formula Student UK 2025 Rules v1.0** (uses identical hoop/tube tables):
  <https://www.imeche.org/docs/default-source/1-oscar/formula-student/2025/rules/fsuk-2025-rules---v1-0.pdf>
* **2025 FSAEJ Participation Rules (en, 2025-02-12)**:
  <https://www.scribd.com/document/875576904/2025-FSAEJ-ParticpationRules2-en-20250212>
* **DesignJudges — 2025 FSAE Frame Rule Changes**:
  <https://www.designjudges.com/articles/guide-to-2025-fsae-frame-rule-changes>
* **UNCA chassis rules summary, F.3.4 steel tubing**:
  <https://sites.google.com/unca.edu/chassis/chassis-rules/f-3-tubing-and-material/f-3-4-steel-tubing-and-material>

## Requirements (from `spec.json`)

| ID  | Metric                              | Limit       | Op  | Derivation                                                              |
| --- | ----------------------------------- | ----------- | --- | ----------------------------------------------------------------------- |
| R1  | `main_hoop_tube_OD_mm`              | 25.4 mm     | ==  | F.3.4 (rulebook T.3.4) Size A primary-structure steel tube              |
| R2  | `main_hoop_tube_wall_mm`            | 2.4 mm      | >=  | F.3.4 nominal wall thickness                                            |
| R3  | `wheelbase_mm`                      | 1525 mm     | >=  | F.1.2 (rulebook T.1.2) wheelbase minimum                                |
| R4  | `IA_avg_decel_g`                    | 20 g        | <=  | IA test 7 m/s into rigid barrier, 300 kg eq. vehicle, 7.5 g target avg  |
| R5  | `IA_peak_decel_g`                   | 40 g        | <=  | IA test peak deceleration cap                                           |
| R6  | `tilt_first_failure_angle_deg`      | 60 deg      | >=  | Tilt test 60 deg (no fluid leakage / no tip-over)                       |

Material spec (mild steel SAE/AISI 1010 or equivalent): `Fy >= 305 MPa`,
`Ftu >= 365 MPa`. The eval template uses `E = 200 GPa`, `nu = 0.3`,
`rho = 7.85e-9 t/mm^3` (mm-MPa-N-tonne unit set).

## How the eval works

1. The agent's `build.py` runs in this directory and produces:
   * `out.step` — the chassis CAD as STEP
   * `meta.json` — selector map for `NFIXED` (rear) and `NLOAD` (front
     bulkhead) plus the named material
2. `gmsh` 4.15 meshes `out.step` into tetrahedral C3D solids (`mesh.inp`).
3. `wire_bcs.py` builds an `Eall` ELSET from the C3D ELSETs, applies the
   meta.json selectors to the meshed nodes to populate `*NSET=NFIXED` and
   `*NSET=NLOAD`, and concatenates `mesh.inp + nsets + analysis_template.inp`
   into `model.inp`.
4. `ccx_2.22 model` runs the deck. Two `*STEP`s:
   * **LC1** — IA quasi-static front-bulkhead push: 22 070 N total in -x
     applied as `*CLOAD` to NLOAD.
   * **LC2** — 60-deg static tilt: `*DLOAD ... GRAV` body load with vector
     `(0, -sin60, -cos60)`.
5. `check.py` parses `model.dat`, evaluates R1..R6 against the rulebook
   limits and prints `PASS`, `FAIL`, or `SKIP` for each.

### Why FEA only "informs" R4..R6 here

* **R4, R5** (IA decelerations) are fundamentally a nonlinear
  explicit-dynamics problem (LS-DYNA / Abaqus Explicit / Radioss). CCX 2.22
  linear-static cannot resolve the deceleration history, so `check.py`
  reports `SKIP` with the rule's own design target as the closed-form
  fallback (per `spec.json` `requires_non_fea_solver`).
* **R6** is geometric (CoG vs half-track) plus a fluid-orientation gate, not
  a chassis-strength gate. `check.py` evaluates it closed-form via
  `tan(theta_tip) = (track/2) / h_cog` (FSAE-typical 1200 mm track,
  300 mm CoG height -> ~63.4 deg) and reports the FEA LC2 max von Mises
  for information only.
* **R1, R2, R3** are closed-form material/geometric: `check.py` reads them
  from constants matching the as-built model.

## Eval-side files (this directory)

| File                       | Role                                                           |
| -------------------------- | -------------------------------------------------------------- |
| `spec.json`                | Authoritative requirements (R1..R6) + load cases               |
| `analysis_template.inp`    | CCX deck — material, `*SOLID SECTION` on `Eall`, `*BOUNDARY`, two `*STEP`s with `*CLOAD` on NLOAD and `*DLOAD GRAV` |
| `check.py`                 | Post-processor for `model.dat`, prints PASS/FAIL/SKIP per requirement |
| `notes.md`                 | Source URLs and FEA simplifications (informational)            |
| `build.py`                 | Sample agent submission — reference cadquery script            |

`analysis_template.inp` references `Eall`, `NFIXED`, and `NLOAD` symbolically.
`Eall` is auto-constructed by `wire_bcs.py` from every `C3D*` ELSET in the
gmsh-emitted `mesh.inp`. `NFIXED` and `NLOAD` are populated from the
agent's `meta.json` selectors.

## What the agent must produce

`build.py` (this directory) is the submission entry point. It must:

1. Build the chassis CAD with cadquery 2.8 (or any STEP-emitting tool).
2. Export `out.step` (STEP AP242 preferred).
3. Write `meta.json` validating against
   [`schemas/meta.schema.json`](../../../../schemas/meta.schema.json).

### `meta.json` example

```json
{
  "jobname": "model",
  "material": "STEEL1010",
  "selectors": {
    "NFIXED": {"face": "x_min", "tol_mm": 0.5},
    "NLOAD":  {"face": "x_max", "tol_mm": 0.5}
  },
  "notes": "NFIXED = rear axle plane; NLOAD = front bulkhead (IA push face)."
}
```

Selectors supported by `wire_bcs.py`: `face`, `face_eq`, `box`, `sphere`,
`all`. See the schema for the full grammar.

### Conventions for this item

* `+x` is forward (rear axle at `x_min`, front bulkhead at `x_max`).
* The chassis envelope is anything STEP whose extreme `x_min` and `x_max`
  faces correspond to "rear axle attach plane" (clamped) and "front
  bulkhead" (IA push) respectively.
* The named material in `meta.json["material"]` is spliced into the
  `__MATERIAL__` placeholder in `analysis_template.inp`. Use whatever
  internal material name your build.py prefers — the eval template only
  cares that `*MATERIAL, NAME=<x>` matches the `MATERIAL=<x>` reference
  on `*SOLID SECTION` (both are expanded from `__MATERIAL__`).

## Verifying locally

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./benchmark/kits/single/056_jp1_student_formula_japan
```

The runner exits 0 iff every stage (build / gmsh / wire / ccx / check)
returned 0. `check.log` contains the per-requirement PASS/FAIL/SKIP output.
