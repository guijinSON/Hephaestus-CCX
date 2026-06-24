# 061_jp6 — Coma Wars Standard-Class Spinning Top (JP6)

## What this is

Verification kit for a *Zenkoku Seizogyo Koma Taisen* ("All-Japan
Manufacturers' Spinning-Top Tournament") **standard-class** koma. The
catalog gates a 20 mm OD x 60 mm height envelope, a 0.02 mm spin-axis
concentricity tolerance, and structural survival of a 50 N lateral
collision impact at the widest diameter (LC1). Mass-properties (mass,
CG height, polar moment of inertia Izz, concentricity) are computed
**analytically** from the agent's CAD — the spec explicitly excludes
FEA for these. The CalculiX run corroborates that the brass body
remains elastic under LC1.

## Governing reference

- **Coma Wars official rules (Standard class)** —
  <https://www.komataisen.com/english/>
- **Standard-class detail page** —
  <https://www.komataisen.com/spinforalongtime/standard/rule/>
- **Rules page** —
  <https://www.komataisen.com/%E8%A6%8F%E7%B4%84/rule-english-ver/>

Catalog gates used here:

- OD <= 20.000 mm at the spin axis (stationary)
- Total length <= 60.000 mm (stationary)
- Material unrestricted (we pick brass C36000 for the demo build)
- Combat: collide on the *dohyo*; top that stops first OR exits ring loses

## Requirements (R1..R4)

| ID | Metric | Limit | Derivation | Source |
|----|--------|-------|------------|--------|
| R1 | `OD_mm` | `<= 20` | Standard-class envelope (Coma Wars rules) | CAD via `mass_properties.json` |
| R2 | `height_mm` | `<= 60` | Standard-class envelope (Coma Wars rules) | CAD via `mass_properties.json` |
| R3 | `concentricity_mm` | `<= 0.02` | Balanced-spin tolerance (spec) | Manufacturing-declared (analytic) |
| R4 | `max_vm_stress_MPa` | `<= 310` (Sy of Brass C36000) | No plastic yield under LC1 50 N lateral | CalculiX `model.dat` |

The mass-properties side outputs (mass_g, CG_height_mm,
polar_moment_Izz_kg_mm2) are reported by `check.py` and the >50 g
review flag is informational, not pass/fail.

## How the eval works

```
build.py  ->  out.step + meta.json + mass_properties.json
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
                                         check.py  ->  PASS/FAIL per R1..R4
```

`scripts/ccx_eval/grade_ccx.py` orchestrates every stage and writes
`grade.json` summarising each step's return code.

## Eval-side files

- `analysis_template.inp` — Brass C36000 `*MATERIAL`, `*SOLID SECTION` on `Eall`, `*BOUNDARY` on `NFIXED`, `*CLOAD` on `NLOAD`, single `*STATIC` step that outputs `S` and `U`. No `*NODE` / `*ELEMENT` (the runner splices in gmsh's mesh).
- `check.py` — reads `spec.json` + `mass_properties.json` + `model.dat`; evaluates R1..R4 and writes `check_summary.json`.
- `spec.json` — catalog-derived requirements / load cases / output schema.
- `notes.md` — sources, design rationale, and reference results.
- `README.md` — this file.

## What the agent must produce

`build.py` must, when run with cwd=workdir, emit:

1. `out.step` — STEP AP242 of the spinning-top body (OD <= 20 mm, total height <= 60 mm).
2. `meta.json` conforming to `schemas/meta.schema.json` and providing the NSET selectors referenced in `analysis_template.inp`.
3. `mass_properties.json` with the analytic mass-property report (the spec mandates these are NOT FEA-derived).

Required `meta.json` keys:

- `selectors.NFIXED` — picks nodes on the bottom contact patch (tip / dohyo contact).
- `selectors.NLOAD` — picks nodes on the top-rim ring at the widest diameter (LC1 50 N lateral load introduction).
- `material` — optional (template hard-codes `BRASS_C36000`; included for clarity).

Required `mass_properties.json` keys consumed by `check.py`:

- `OD_mm`, `height_mm`, `concentricity_mm_assumed`, `mass_g`,
  `CG_height_mm`, `polar_moment_Izz_kg_mm2`, `n_nodes`, `n_elements`.

Example:

```json
{
  "selectors": {
    "NFIXED": {"face": "z_min", "tol_mm": 0.05},
    "NLOAD":  {"face": "z_max", "tol_mm": 0.05}
  },
  "material": "BRASS_C36000",
  "notes": "Brass C36000 cylindrical koma body, OD=20 mm, H=18 mm; LC1 50 N lateral impact on z_max top face."
}
```
