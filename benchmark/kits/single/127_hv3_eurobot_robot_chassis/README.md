# 127_hv3_eurobot_robot_chassis - Eurobot Collegiate Robot Chassis Deck

## What this is

A submission-agnostic CalculiX FEM eval kit for a **Eurobot 2026** (HV3)
collegiate autonomous-robot **5083-H111 aluminum chassis deck plate**
(300 x 300 x 8 mm, monolithic, billet-machined). The agent submits a
CAD model of the deck as `out.step` plus a `meta.json` describing which
features are fixed / loaded; the eval harness meshes it with gmsh,
splices the eval-side analysis template in, runs CalculiX 2.22, and
grades the results against the limits encoded in `spec.json`.

## Governing references

- **Eurobot 2026 General Rules** (1200 / 1400 mm perimeter silhouette
  rule, robot mass budget): <https://www.eurobot.org/en/>
- **Material**: Aluminum 5083-H111 wrought plate
  (`E = 71 GPa`, `nu = 0.33`, `rho = 2660 kg/m^3`, `Sy = 145 MPa`,
  `Stu = 310 MPa`).
- **Standard robotics engineering practice** for safety factors:
  yield/2.0 operating, yield/1.5 transient.

## Requirements (R1..R6 from `spec.json`)

| ID  | Metric                                  | Limit        | Op  | Derivation / Source                                     | FEM source                              |
| --- | --------------------------------------- | ------------ | --- | ------------------------------------------------------- | --------------------------------------- |
| R1  | Max von Mises (LC1 match-play)          | 72 MPa       | <=  | Sy/2.0 = 145/2.0 = 72.5 -> 72 MPa (operating SF)         | `model.frd` step 1 STRESS               |
| R2  | Max von Mises (LC2 collision)           | 97 MPa       | <=  | Sy/1.5 = 145/1.5 = 96.7 -> 97 MPa (transient SF)         | `model.frd` step 2 STRESS               |
| R3  | Max VM AND max deflection (LC3 drop)    | 97 MPa, 3 mm | <=  | Sy/1.5 + motor-gearhead alignment cap                   | `model.frd` step 3 STRESS + DISP        |
| R4  | First 6 natural frequencies (modal)     | 60 Hz        | >=  | Drive-motor harmonic avoidance (20-50 Hz at 3-6 krpm)   | `model.dat` MODE NO / EIGENVALUE table  |
| R5  | Deck mass (spec geometry)               | 1.6 kg       | <=  | Total mass budget for Eurobot competition vehicle        | analytical (spec geom)                  |
| R6  | Silhouette perimeter (start footprint)  | 1200 mm      | <=  | Eurobot benchmark perimeter rule (start-size silhouette)| analytical (4 x 300 mm = 1200 mm)       |

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

## Load cases (`*STEP` in `analysis_template.inp`)

Three quasi-static `*STATIC` steps + one `*FREQUENCY` step. Distributed
on-deck payload mass (3.5 kg of electronics/batteries/actuators
distributed across the 3x4 motor grid) is folded into the GRAV
magnitude via an effective-density factor:

```
rho_eff / rho_plate = (m_plate + m_payload) / m_plate
                    = (1.9152 kg + 3.5 kg) / 1.9152 kg
                    = 2.828
```

so `*DLOAD GRAV` magnitudes are pre-multiplied by 2.828 to recover the
combined plate+payload weight per unit volume.

| Step | Case                | Body force                                                | Edge load                        |
| ---- | ------------------- | --------------------------------------------------------- | -------------------------------- |
| 1    | LC1 match-play      | 1g (-z) + 0.15g (+x), GRAV mag = 27737 / 4161 mm/s^2     | 2 kg manipulator on NLOAD (-z)   |
| 2    | LC2 collision       | none                                                      | 50 N quasi-static on NLOAD (+y)  |
| 3    | LC3 5g drop         | 5g (-z), GRAV mag = 138684 mm/s^2                        | 5x manipulator on NLOAD (-z)    |
| 4    | modal (R4)          | none (NFIXED clamped)                                     | 6 modes                          |

The 2 kg manipulator force (`F = m * g = 19.62 N` at 1g) is applied
via `*CLOAD` on `NLOAD` with `1.0` per node in z; CalculiX's per-node
convention means the absolute total reaction is `n_NLOAD * 1.0`, which
is reported but not the dominant contribution to the von Mises field
(plate+payload body force dominates by ~5x).

## Eval-side files (this directory)

| File                       | Role                                                                                                |
| -------------------------- | --------------------------------------------------------------------------------------------------- |
| `spec.json`                | Authoritative requirements (R1..R6) + load cases + material                                         |
| `analysis_template.inp`    | CCX deck — 5083-H111 `*MATERIAL`, `*SOLID SECTION` on `Eall`, `*BOUNDARY` on NFIXED_*, 3 LCs + modal |
| `check.py`                 | Post-processor for `model.frd` / `model.dat`, prints PASS/FAIL/SKIP per R1..R6                      |
| `notes.md`                 | Source URLs + FEM idealization notes (informational)                                                |
| `build.py`                 | Sample agent submission - reference cadquery script (300x300x8 plate + 4 corner bores)              |
| `README.md`                | This file                                                                                            |

`analysis_template.inp` references `Eall`, `NFIXED_NW/NE/SW/SE`, and
`NLOAD` symbolically. `Eall` is auto-constructed by `wire_bcs.py` from
every `C3D*` ELSET in the gmsh-emitted `mesh.inp`. The four
`NFIXED_*` and the single `NLOAD` are populated from the agent's
`meta.json` selectors.

## What the agent must produce

`build.py` (this directory) is the submission entry point. It must:

1. Build the deck CAD with cadquery 2.8 (or any STEP-emitting tool).
2. Export `out.step` (STEP AP242 preferred).
3. Write `meta.json` validating against
   [`schemas/meta.schema.json`](../../../../schemas/meta.schema.json).

### `meta.json` example (this kit's reference build.py)

```json
{
  "jobname": "model",
  "material": "AL5083",
  "selectors": {
    "NFIXED_NW": {"sphere": [12.0,  12.0, 4.0, 7.21]},
    "NFIXED_NE": {"sphere": [288.0, 12.0, 4.0, 7.21]},
    "NFIXED_SW": {"sphere": [12.0, 288.0, 4.0, 7.21]},
    "NFIXED_SE": {"sphere": [288.0, 288.0, 4.0, 7.21]},
    "NLOAD":     {"box": [50.0, -0.5, 7.5, 250.0, 0.5, 8.5], "tol_mm": 0.5}
  }
}
```

Selectors supported by `wire_bcs.py`: `face`, `face_eq`, `box`,
`sphere`, `all`. See the schema for the full grammar.

### Conventions for this item

- `+x` is width (left-right); `+y` is depth (front-back, **front edge
  at `y = 0`**); `+z` is up (deck top at `z = DECK_T = 8 mm`).
- The four corner M12 standoff bores must be addressable via spheres
  centered on each bore axis at mid-thickness.
- The front-edge load-introduction strip must be addressable via an
  AABB box capturing top-face nodes at `y close to 0` within the
  200 mm wide front recess band (50..250 mm in x).
- The named material in `meta.json["material"]` is spliced into
  `__MATERIAL__` in `analysis_template.inp` (the same name appears on
  `*MATERIAL, NAME=` and `*SOLID SECTION, MATERIAL=`).

## Verifying locally

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./benchmark/kits/single/127_hv3_eurobot_robot_chassis
```

The runner exits 0 iff every stage (build / gmsh / wire / ccx /
check) returned 0. `check.log` contains the per-requirement
PASS/FAIL/SKIP output; `grade.json` records per-stage rc + elapsed
seconds.
