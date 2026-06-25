# 034_s3 — NASA-STD-5020B 4-Bolt Titanium Bolted-Lap-Joint Bracket Pair

## What this is

Verification kit for a 4-bolt MJ8 A286 / Ti-6Al-4V bolted-lap-joint
bracket pair carrying a 40 kg payload simulator offset 150 mm above the
joint plane under a combined 10 g lateral / 20 g axial quasi-static
launch event. The kit certifies the six NASA-STD-5020B margin
requirements (separation, slip, bolt ultimate, bolt yield, plate
bearing, thread engagement) against the agent's submitted CAD geometry.

## Governing standard

- **NASA-STD-5020B (2021-08-06)** — *Requirements for Threaded Fastening Systems in Spaceflight Hardware*, Section 6 ("Design") + Appendix A ("Strength Analysis"). Reference: <https://standards.nasa.gov/sites/default/files/standards/NASA/B/0/2021-08-06-nasa-std-5020b_final.pdf>
- Cross-checked against NASA TM-20210024657 background on Appendix A margin equations: <https://ntrs.nasa.gov/api/citations/20210024657/downloads/TM-20210024657.pdf>

## Requirements (R1..R6)

| ID | Metric | Limit | Derivation | FEM source |
|----|--------|-------|------------|------------|
| R1 | Separation margin `MS_sep` | `>= 0` | `P_pld_min / (n * SF_sep * P_tL) - 1` with `n = 0.5`, `SF_sep = 1.2` (Appendix A) | closed-form (spec.json inputs) |
| R2 | Slip margin `MS_slip` | `>= 0` | `(mu_j * n_f * P_pld_min) / (SF_slip * P_sL) - 1` with `mu_j = 0.2`, `SF_slip = 1.2` | closed-form |
| R3 | Bolt ultimate-tensile margin `MS_U` | `>= 0` | `P_allow_U / (P_pld_max + Phi * P_tU) - 1` with `Phi = 0.15` | closed-form |
| R4 | Bolt yield margin `MS_Y` | `>= 0` | `P_allow_Y / (P_pld_max + Phi * 1.25 * P_tL) - 1` | closed-form |
| R5 | Plate bearing stress `sigma_brg` | `<= 1.5 * Ftu_Ti = 1425 MPa` | `P_sU / (d_bolt * t_plate)` | closed-form (FEM peak Szz reported as corroboration) |
| R6 | Thread engagement past nut | `>= 2 threads` | NASA-STD-5020B Section 6.1.2 geometric check (MJ8x1.25 pitch, ~6.5 mm nut) | geometric |

The FEM (CalculiX run on the agent's STEP) is a supporting check that
the plates stay elastic under the lumped per-bolt loads. The Appendix A
closed-form margins are the authoritative pass/fail drivers.

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
`grade.json` summarising each step's return code.

## Eval-side files

- `analysis_template.inp` — Ti-6Al-4V `*MATERIAL`, `*SOLID SECTION` on `Eall`, `*BOUNDARY` on `NFIXED`, `*CLOAD` on `NLOAD`, single `*STATIC` step. No `*NODE` / `*ELEMENT` (the runner splices in gmsh's mesh).
- `check.py` — closed-form NASA-STD-5020B Appendix A margin evaluator (R1..R6); also reads `model.dat` for FEM peak |Szz| / von Mises corroboration.
- `README.md` — this file.

## What the agent must produce

`build.py` must, when run with cwd=workdir, emit:

1. `out.step` — STEP AP242 of the bolted-joint geometry.
2. `meta.json` conforming to `schemas/meta.schema.json` and providing the NSET selectors referenced in `analysis_template.inp`.

Required `meta.json` keys:

- `selectors.NFIXED` — picks nodes on the lower plate underside (deck attach face).
- `selectors.NLOAD` — picks nodes on the upper plate top face (load introduction).
- `material` — optional (template hard-codes `TI64`; included for clarity).

Example:

```json
{
  "selectors": {
    "NFIXED": {"face": "z_min", "tol_mm": 0.5},
    "NLOAD":  {"face": "z_max", "tol_mm": 0.5}
  },
  "material": "TI64",
  "notes": "Two 100x60x8 mm Ti-6Al-4V plates, MJ8 A286 bolt pattern omitted for FEM corroboration."
}
```
