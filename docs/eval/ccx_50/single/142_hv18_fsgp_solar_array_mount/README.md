# 142_hv18_fsgp_solar_array_mount — FSGP Solar-Array Canopy Panel Mount

## What this is

A submission-agnostic CalculiX FEM eval kit for the **Formula Sun Grand
Prix 2026** monolithic 6061-T6 solar-array canopy panel mount. The
bracket clamps a 2 m x 1 m (2.0 m^2, 12 kg) composite solar panel to
the canopy rails of a single-occupant solar vehicle. The agent submits
a CAD model of the bracket as `out.step` plus a `meta.json` describing
which faces are fixed / loaded; the eval harness meshes it with gmsh,
splices the eval-side analysis template in, runs CalculiX 2.22, and
grades the results against the limits encoded in `spec.json`.

## Governing references

- **ASC 2026 Regulations Rev B (2025-11-19)** — silicon SOV class:
  <https://www.americansolarchallenge.org/american-solar-challenge-2026-regulations/>
- **Formula Sun Grand Prix general info & 2025 regs (mirror)**:
  <https://www.americansolarchallenge.org/formula-sun-grand-prix/>,
  <https://www.americansolarchallenge.org/formula-sun-grand-prix-2025-regulations/>
- **Material**: 6061-T6 (E 68.9 GPa, nu 0.33, rho 2700 kg/m^3,
  Sy 276 MPa, Stu 310 MPa, S_e 97 MPa @ 5e8 cycles).

## Requirements (R1..R6)

| ID  | Metric                                | Limit       | Op  | Derivation / Source                                                       | FEM source                            |
| --- | ------------------------------------- | ----------- | --- | ------------------------------------------------------------------------- | ------------------------------------- |
| R1  | Max von Mises (LC1 aero)              | 138 MPa     | <=  | yield/2.0 continuous (Sy = 276 MPa, SF=2.0)                               | `model.dat` step 1 *EL PRINT S        |
| R2  | Max von Mises (LC2 gust)              | 184 MPa     | <=  | yield/1.5 transient                                                        | `model.dat` step 2 *EL PRINT S        |
| R3  | Max von Mises (LC3 transport)         | 184 MPa     | <=  | yield/1.5 transient                                                        | `model.dat` step 3 *EL PRINT S        |
| R4  | Panel-clamp deflection (LC1)          | 1.0 mm      | <=  | spec R5 — flatness preservation to avoid PV cell micro-cracking            | `model.dat` step 1 *NODE PRINT NLOAD U |
| R5  | First 3 natural frequencies (modal)   | informational| n/a | spec excludes vibration analysis; modal reported as sanity check           | `model.dat` step 4 *FREQUENCY         |
| R6a | Clamp body temperature (45 C amb.)    | 85 C        | <=  | spec R4 — T6 over-aging gate (820 W/m^2 panel rejection)                   | **SKIP** (steady-state thermal not in CCX deck) |
| R6b | Bracket mass                          | 0.25 kg     | <=  | spec R6 — billet-machined monolith mass cap                                 | **SKIP** (envelope-bound block over-shoots; agent CAD must drive mass) |

The CCX deck runs LC1/LC2/LC3 as `*STATIC` pressure steps plus a
`*FREQUENCY` modal step. The thermal (R6a) and mass (R6b)
requirements are explicitly `SKIP`ped: the linear-static deck does
not solve a steady-state heat-conduction problem, and the simplified
envelope-bound block overshoots the 0.25 kg billet-machined cap by
construction. The agent's true CAD STEP determines the real mass —
`check.py` may report the as-built figure but treats it as
informational against the FEM-side template.

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

## Load cases (panel-clamp pressure)

| Case | Description       | Total force          | Footprint | Pressure on NLOAD |
| ---- | ----------------- | -------------------- | --------- | ----------------- |
| LC1  | aero @ 70 mph     | 176 N lift (+88 drag)| 4800 mm^2 | 0.0367 MPa        |
| LC2  | gust 2x lift      | 352 N lift           | 4800 mm^2 | 0.0733 MPa        |
| LC3  | transport / 5 g   | 588 N vertical       | 4800 mm^2 | 0.1225 MPa        |

LC1 drag (88 N in +x) is conservatively bounded by the larger LC3
magnitude and is omitted from the deck.

## Eval-side files (this directory)

| File                       | Role                                                                                                                              |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `spec.json`                | Authoritative requirements + load cases                                                                                           |
| `analysis_template.inp`    | CCX deck — 6061-T6 `*MATERIAL`, `*SOLID SECTION` on `Eall`, `*BOUNDARY` on `NFIXED`, three `*STATIC` `*DLOAD P` steps, `*FREQUENCY`|
| `check.py`                 | Post-processor for `model.dat`, prints PASS/FAIL/SKIP per requirement (R6a/R6b are SKIP by design)                                |
| `notes.md`                 | Source URLs, idealization, and FEM caveats                                                                                        |
| `build.py`                 | Sample agent submission — reference cadquery script (160 x 80 x 40 mm envelope block)                                             |
| `README.md`                | This file                                                                                                                          |

`analysis_template.inp` references `Eall`, `NFIXED`, and `NLOAD`
symbolically. `Eall` is auto-constructed by `wire_bcs.py` from every
`C3D*` ELSET in the gmsh-emitted `mesh.inp`. `NFIXED` and `NLOAD` are
populated from the agent's `meta.json` selectors.

## What the agent must produce

`build.py` (this directory) is the submission entry point. It must:

1. Build the bracket CAD with cadquery 2.8 (or any STEP-emitting tool).
2. Export `out.step` (STEP AP242 preferred).
3. Write `meta.json` validating against
   [`schemas/meta.schema.json`](../../../../schemas/meta.schema.json).

### `meta.json` example

```json
{
  "jobname": "model",
  "material": "AL6061T6",
  "selectors": {
    "NFIXED": {"face": "x_max", "tol_mm": 0.5},
    "NLOAD":  {"face": "z_max", "tol_mm": 0.5}
  },
  "notes": "NFIXED = rail-saddle face; NLOAD = panel-clamp footprint."
}
```

Selectors supported by `wire_bcs.py`: `face`, `face_eq`, `box`,
`sphere`, `all`. See the schema for the full grammar.

### Conventions for this item

- `+x` is along the canopy rail (rail saddle at `x_max` -> NFIXED).
- `+z` is vertical (panel-clamp footprint at `z_max` -> NLOAD).
- Pressure (`*DLOAD P`) acts inward to the element face; CalculiX
  applies it consistently regardless of mesh density.
- The named material in `meta.json["material"]` is informational; the
  template hard-codes `AL6061T6` to keep the deck self-contained.

## Verifying locally

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    ./scripts/ccx_eval/grade_ccx.py \
    ./docs/eval/ccx_50/single/142_hv18_fsgp_solar_array_mount
```

The runner exits 0 iff every stage (build / gmsh / wire / ccx /
check) returned 0. `check.log` contains the per-requirement
PASS/FAIL/SKIP output.
