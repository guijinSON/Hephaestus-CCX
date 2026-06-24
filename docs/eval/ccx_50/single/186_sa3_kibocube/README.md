# 186_sa3 - JAXA J-SSOD / KiboCUBE 1U CubeSat Primary Structure

## What this is

Verification kit for a monolithic Al 6061-T6 1U CubeSat primary
structure deployed from the ISS Kibo airlock via the JAXA Japanese
Experiment Module Small Satellite Orbital Deployer (J-SSOD /
KiboCUBE). The kit certifies the five J-SSOD / Cal Poly CDS Rev 14
requirements (R1..R5) against the agent's submitted CAD geometry.

Envelope is 100 x 100 x 113.5 mm with four 8.5 x 8.5 mm deployer rails
on the long corners (>=75 % rail-J-SSOD contact along the 113.5 mm
length), mass cap 1.33 kg.

## Governing standards

- **JX-ESPC-100134** - JAXA J-SSOD / KiboCUBE qualification spec
  (quasi-static 9 g, sine 5-100 Hz @ 2.0 g, random 20-2000 Hz GRMS 7.7,
  thermal vacuum -20/+50 C x6, first natural frequency >= 135 Hz).
- **JX-ESPC-101133-C** - JEM Payload Accommodation Handbook Vol. 8,
  Small Satellite Deployment Interface Control Document:
  <https://iss.jaxa.jp/kibouser/library/item/jx-espc_8c_en.pdf>
- **Cal Poly CDS Rev 14** - 1U CubeSat envelope (100 x 100 x 113.5 mm)
  and mass cap (<= 1.33 kg).
- J-SSOD facility / KiboCUBE programme references:
  <https://humans-in-space.jaxa.jp/en/biz-lab/experiment/facility/ef/jssod/>

## Requirements (R1..R5)

| ID | Metric                              | Limit        | Derivation                       | FEM source                             |
|----|-------------------------------------|--------------|----------------------------------|----------------------------------------|
| R1 | Max von Mises under LC1 (9 g)       | <= 138 MPa   | yield 276 / SF 2.0               | STEP 1 *STATIC + GRAV (model.dat / .frd) |
| R2 | First natural frequency             | >= 135 Hz    | JX-ESPC-100134 (J-SSOD floor)    | STEP 2 *FREQUENCY (model.dat)            |
| R3 | Total structure mass                | <= 1.33 kg   | Cal Poly CDS Rev 14 1U cap       | closed-form: rho * sum(elem volumes)     |
| R4 | Thermal-cycle combined max vM       | <= 138 MPa   | yield 276 / SF 2.0 (DT = +30 K)  | STEP 3 *STATIC + *TEMPERATURE            |
| R5 | First buckling mode load factor     | >= 2.0       | JX-ESPC-100134 secondary         | **SKIP** (see note below)                |

R5 is intentionally **SKIP**: with LC1 stress at ~1 % of yield (the
chassis is dimensioned by stiffness/CDS rather than strength), linear
buckling is not a credible failure mode for this geometry. The spec
lists buckling as a secondary requirement. A `*BUCKLE` follow-on
step is left as a clean extension for agents that wish to report it.

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
                                         check.py  ->  PASS/FAIL/SKIP per R1..R5
```

`scripts/ccx_eval/grade_ccx.py` orchestrates all stages and writes
`grade.json` summarising each step's return code.

## Eval-side files

- `analysis_template.inp` - Al 6061-T6 `*MATERIAL`, `*SOLID SECTION`
  on `Eall`, `*BOUNDARY` on `NFIXED`, three steps:
  1. `*STATIC` LC1 9 g via `*DLOAD GRAV`,
  2. `*FREQUENCY` (5 modes),
  3. `*STATIC` thermal +30 K via `*TEMPERATURE` on `NALL`.
  No `*NODE` / `*ELEMENT` (the runner splices in gmsh's mesh).
- `check.py` - parses `model.dat` for max von Mises (R1, R4) and the
  first eigenfrequency (R2); evaluates R3 from element volumes;
  reports R5 as SKIP.
- `README.md` - this file.

## What the agent must produce

`build.py` must, when run with cwd=workdir, emit:

1. `out.step` - STEP AP242 of the 1U CubeSat chassis. Geometry must
   contain four 8.5 x 8.5 mm corner rails over the full 113.5 mm
   length; simplified top/bottom plates are acceptable.
2. `meta.json` conforming to `schemas/meta.schema.json` and providing
   the NSET selectors referenced in `analysis_template.inp`.

Required `meta.json` keys:

- `selectors.NFIXED` - rail z_min face (deployer-aft interface,
  idealised as fully encastred).
- `selectors.NALL`   - every node (used by `*INITIAL CONDITIONS,
  TYPE=TEMPERATURE` and the `*TEMPERATURE` field in the thermal step).
- `material` - optional (template hard-codes `AL6061T6`; included for
  clarity).

Example:

```json
{
  "selectors": {
    "NFIXED": {"face": "z_min", "tol_mm": 0.5},
    "NALL":   {"all": true}
  },
  "material": "AL6061T6",
  "notes": "1U CubeSat, four 8.5 mm corner rails + simplified plates."
}
```
