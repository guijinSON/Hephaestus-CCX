# 011_pt11 — Misumi HBLFSNB8-class 8-Series Reinforcing Corner Bracket

## What this is

Submission-agnostic eval kit for a Misumi HBLFSNB8-class large reinforcing
corner bracket (80 x 80 x 40 mm, 12 mm leg thickness) joining two HFS8-4040
aluminum extrusions (vertical 600 mm + horizontal 400 mm cantilever) under
a tip payload of 300 N vertical + 150 N horizontal.

The kit certifies six requirements (R1..R6) drawn from the Misumi
catalog: per-bracket static / dynamic force, per-bracket bending moment,
cantilever tip deflection, extrusion bending stress, and M8 T-nut
pull-out — against the agent's submitted CAD bracket geometry. The
authoritative pass/fail comes from closed-form catalog formulas in
`check.py`; the CalculiX FEM is a corroborating elasticity check that
the bracket itself stays elastic at the per-bracket reaction load.

## Governing references

- **Misumi HBLFSNB8** product page (8-Series Reversal Brackets with Tab):
  <https://us.misumi-ec.com/vona2/detail/110300449520/?HissuCode=HBLFSNB8>
- **Misumi HBLFSNB8 part-community model**:
  <https://misumi.partcommunity.com/3d-cad-models/hblfsn8-hblfsnb8-hblfsnm8-brackets-hfs8-reversal-brackets-with-tab-misumi>
- **Misumi HFS8-4040 1-slot extrusion**:
  <https://misumi.partcommunity.com/3d-cad-models/nefs8-4040-nefsb8-4040-nefsy8-4040-efs8-4040-efsb8-4040-hfs8-4040-hfsb8-4040-caf8-4040-hfsy8-4040-hfsl8-4040-hfslb8-4040-gfs8-4040-nfsl8-4040-hfs8-series-aluminum-extrusions-40-square-1-slot-misumi>
- **Misumi technical PDF** (HFS8 40/80 sq, 1-slot, sectional moment of inertia):
  <https://in.misumi-ec.com/pdf/fa/2015/p2_663.pdf>

Catalog values used (per `spec.json` and `notes.md`):

- HBLFSNB8 static allowable load: **1,470 N** (150 kgf) per bracket
- HBLFSNB8 dynamic allowable load: **490 N** (50 kgf) per bracket
- HBLFSNB8 allowable bending moment: **29.4 N·m** per bracket
- HFS8-4040 `Ix = Iy ≈ 9.0e4 mm^4` (Misumi catalog value, rounded)
- M8 short-thread T-nut pull-out: **2,400 N**

## Requirements (R1..R6)

| ID | Metric | Limit | Derivation | Source |
|----|--------|-------|------------|--------|
| R1 | `per_bracket_force_static` | `<= 1470 N` | `M_v / (N_brackets * arm)` with `M_v = 300 * 400 = 120e3 N·mm`, `N_brackets = 2`, `arm = 40 mm` -> 1500 N | closed-form (catalog) |
| R2 | `per_bracket_force_dynamic` | `<= 490 N` | Same as R1 (full reversal at fatigue) | closed-form (catalog) |
| R3 | `per_bracket_bending_moment` | `<= 29.4 N·m` | `M_v / N_brackets` (vertical moment shared by 2 brackets) -> 60 N·m | closed-form (catalog) |
| R4 | `horizontal_extrusion_tip_deflection` | `<= 1.33 mm` | `delta = sqrt(delta_v^2 + delta_h^2)` with `delta = F L^3 / (3 E Ix)`, `L = 400 mm`, `E = 68900 MPa`, `Ix = 9.0e4 mm^4` | closed-form (Euler-Bernoulli) |
| R5 | `max_extrusion_bending_stress` | `<= 85 MPa` | `sigma = sqrt(sigma_v^2 + sigma_h^2)` with `sigma = M / Z`, `Z = Ix / c`, `c = 20 mm` (yield/SF=170/2.0) | closed-form |
| R6 | `per_tnut_pullout_load` | `<= 1200 N` | `per_bracket_force / 2` (2 nuts on engaged leg, SF 2.0 on 2400 N rating) | closed-form (catalog) |

The FEM (CalculiX run on the agent's STEP) is a supporting check that
the bracket stays elastic at the per-bracket reaction force; the catalog
limits drive the authoritative R1..R6 verdict.

Per the spec, the agent is expected to **flag the undersized bracket
selection**: R1, R2, R3 fail at the stated payload (1500 N > 1470 N
static; 1500 N > 490 N dynamic; 60 N·m > 29.4 N·m), while R4, R5, R6
pass. Mitigations: add brackets, switch to a diagonal brace, or shorten
the cantilever.

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
                                         check.py  ->  PASS/FAIL per R1..R6
```

`scripts/ccx_eval/grade_ccx.py` orchestrates all stages and writes
`grade.json` summarising each stage's return code.

## Eval-side files

- `analysis_template.inp` — 6063-T5 `*MATERIAL` (E = 68.9 GPa, ν = 0.33),
  `*SOLID SECTION` on `Eall`, `*BOUNDARY` fully fixing `NFIXED`
  (vertical-leg bolt-down face), `*CLOAD` on `NLOAD` (horizontal-leg
  far-end face) representing the per-bracket reaction, single `*STATIC`
  step. No `*NODE` / `*ELEMENT` (the runner splices in gmsh's mesh).
- `check.py` — closed-form catalog evaluator (R1..R6) plus a bracket-
  level FEM cross-check that reads `model.frd` for peak von Mises.
- `notes.md` — catalog citations and data-point provenance.
- `spec.json` — full prompt, requirements, and pass/fail criteria.
- `README.md` — this file.

## What the agent must produce

`build.py`, when run with `cwd = workdir`, must emit:

1. `out.step` — STEP AP242 of an HBLFSNB8-class L-bracket (an L-shape
   with two perpendicular legs, each 80 mm long along the extrusion
   axis, 40 mm wide across the slot face, 12 mm thick).
2. `meta.json` conforming to `schemas/meta.schema.json` and providing
   the NSET selectors referenced in `analysis_template.inp`.

Required `meta.json` keys:

- `selectors.NFIXED` — picks nodes on the vertical-leg outer face
  (mates to the vertical 40 x 40 extrusion's slot face).
- `selectors.NLOAD` — picks nodes on the horizontal-leg far-end face
  (where the per-bracket reaction couple lands).
- `material` — optional (template hard-codes `AL6063T5`).

Example:

```json
{
  "selectors": {
    "NFIXED": {"face": "x_min", "tol_mm": 0.5},
    "NLOAD":  {"face": "x_max", "tol_mm": 0.5}
  },
  "material": "AL6063T5",
  "notes": "HBLFSNB8-class L-bracket, 80 x 80 x 40 mm, 12 mm leg thickness."
}
```

The reference `build.py` in this directory uses cadquery to model an
L-bracket whose vertical leg sits at `x in [-12, 0]` and horizontal leg
at `x in [0, 80]`, so `face: x_min` / `face: x_max` cleanly resolve to
the bolt-down and load faces respectively. Any equivalent geometry that
honours the same `meta.json` selector contract is acceptable.

## Running

```
/opt/anaconda3/envs/cadquery/bin/python \
  scripts/ccx_eval/grade_ccx.py \
  docs/eval/ccx_50/single/011_pt11_misumi_aluminum_frame_bracket
```

Final `grade.json` should show `rc=0` for every stage; `check.log`
prints the R1..R6 verdicts. With the catalog payload the suite emits
3 PASS / 3 FAIL by design (R1/R2/R3 fail per spec) and `check.py` exits
0 — the failures are part of the expected agent-must-flag behaviour.
