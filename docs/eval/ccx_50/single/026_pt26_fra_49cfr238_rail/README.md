# 026_pt26 — FRA 49 CFR 238 Tier I Commuter Rail End-Frame / Collision Post

## What this is

Verification kit for the end-beam and collision/corner-post assembly of a
Tier I commuter passenger rail car body per 49 CFR 238 Subpart C.  The
kit certifies the eight pass/fail requirements (R1..R8) — six static
load cases (LC1 buff, LC2 anti-telescoping, LC3/LC4 collision-post
ultimate/intermediate, LC5/LC6 corner-post ultimate/intermediate) plus
weld fatigue (R7) and Charpy toughness (R8) — against the agent's
submitted CAD geometry.  The FEM exercises the LC3 collision-post
ultimate case (1.334 MN at 457 mm above the underframe); the remaining
load cases are evaluated closed-form via sigma = M/Z bending margins
against the spec-prescribed sections.

## Governing standard

- **49 CFR 238.203** — Static end strength: 800,000 lbf (3.56 MN) buff
  load on the line of draft, no permanent deformation.
  <https://www.ecfr.gov/current/title-49/subtitle-B/chapter-II/part-238/subpart-C/section-238.203>
- **49 CFR 238.205** — Anti-climbing mechanism: vertical resistance
  without failure.  (Spec applies a stricter 500,000 lbf vertical.)
  <https://www.ecfr.gov/current/title-49/subtitle-B/chapter-II/part-238/subpart-C/section-238.205>
- **49 CFR 238.211** — Collision posts: ultimate longitudinal shear
  strength of not less than 300,000 lbf at the top of the underframe.
  <https://www.ecfr.gov/current/title-49/subtitle-B/chapter-II/part-238/subpart-C/section-238.211>
- **49 CFR 238.213** — Corner posts: 150,000 lbf horizontal at top of
  underframe without exceeding ultimate; 30,000 lbf at 18 in without
  permanent deformation.
  <https://www.ecfr.gov/current/title-49/subtitle-B/chapter-II/part-238/subpart-C/section-238.213>
- **APTA PR-CS-S-034-99** + **AAR Manual Standard S-034** — revenue-
  service fatigue spectrum and weld fatigue category B for 30-year life.
- **AWS D1.1** — weld procedure qualification (FRA Charpy 20 J at -40 C).

## Requirements (R1..R8)

| ID | Load case | Metric                                  | Limit       | Derivation                          | Eval source      |
|----|-----------|-----------------------------------------|-------------|-------------------------------------|------------------|
| R1 | LC1       | `max_von_mises_buff`                    | `<= 345 MPa` (Fy)  | yield per 49 CFR 238.203     | closed-form (axial, 5-way share) |
| R2 | LC2       | `max_von_mises_anti_telescoping`        | `<= 450 MPa` (Fu)  | UTS per 49 CFR 238.205       | closed-form (end-beam bending)   |
| R3 | LC3       | `max_von_mises_collision_post_ultimate` | `<= 450 MPa` (Fu)  | UTS per 49 CFR 238.211       | FEM + closed-form (M = F·457 mm) |
| R4 | LC4       | `max_von_mises_collision_post_intermed` | `<= 345 MPa` (Fy)  | yield per 49 CFR 238.211     | closed-form (M = F·762 mm)       |
| R5 | LC5       | `max_von_mises_corner_post_ultimate`    | `<= 450 MPa` (Fu)  | UTS per 49 CFR 238.213       | closed-form (M = F·457 mm)       |
| R6 | LC6       | `max_von_mises_corner_post_intermed`    | `<= 345 MPa` (Fy)  | yield per 49 CFR 238.213     | closed-form (M = F·762 mm)       |
| R7 | LC7       | weld fatigue category                   | `== B`             | AAR S-034 / AWS D1.1         | **SKIP** — design intent (S-N closed-form, not FEM) |
| R8 | design    | weld Charpy at -40 C                    | `>= 20 J`          | FRA cold-climate guidance    | **SKIP** — material/WPS, not FEM |

R7 (weld fatigue Cat. B per AAR S-034) and R8 (Charpy 20 J at -40 C) are
**SKIP** because they are weld-procedure / material-toughness items not
exercised by static FEM — they require an explicit S-N cycle count input
(R7) or a notched-bar impact test (R8).

The FEM (CalculiX run on the agent's STEP) is a supporting check that
the collision post stays elastic under LC3 load introduction.  The
closed-form sigma = M/Z bending margins are the authoritative pass/fail
drivers for the static R1..R6 set.

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
                                         check.py  ->  PASS/FAIL/SKIP per R1..R8
```

`scripts/ccx_eval/grade_ccx.py` orchestrates all stages and writes
`grade.json` summarising each step's return code.

## Eval-side files

- `analysis_template.inp` — A572 Gr 50 `*MATERIAL` (E=200 GPa, nu=0.3,
  rho=7.85e-9 t/mm^3), `*SOLID SECTION` on `Eall`, `*BOUNDARY` clamping
  `NFIXED` (1..3), single `*STATIC` step with `*CLOAD` on `NLOAD` in +x
  representing the LC3 1.334 MN horizontal force.  No `*NODE` /
  `*ELEMENT` (the runner splices in gmsh's mesh).
- `check.py` — closed-form sigma = M/Z bending evaluator for R1..R6
  against the spec's prescribed sections (end beam 250x200x15, collision
  post 200x150x15, corner post 150x150x15); also reads `model.dat` for
  FEM peak von Mises corroboration on LC3.  R7/R8 reported as SKIP.
- `README.md` — this file.

## What the agent must produce

`build.py` must, when run with cwd=workdir, emit:

1. `out.step` — STEP AP242 of the collision-post geometry
   (200 mm long axis x 150 mm short axis x 15 mm wall hollow box,
    2.6 m tall, A572 Gr 50).
2. `meta.json` conforming to `schemas/meta.schema.json` and providing
   the NSET selectors referenced in `analysis_template.inp`.

Required `meta.json` keys:

- `selectors.NFIXED` — picks nodes on the post base (underframe-attach
  plane); the assembly is welded to the underframe and modeled as fully
  clamped.
- `selectors.NLOAD` — picks nodes on the load-introduction band at
  z = 457 mm (18 in) above the underframe, where the LC3 1.334 MN
  longitudinal force is applied.
- `material` — optional (template hard-codes `A572GR50`; included for
  clarity).

Example:

```json
{
  "selectors": {
    "NFIXED": {"face": "z_min", "tol_mm": 0.5},
    "NLOAD":  {"face_eq": "z", "value": 457.0, "tol_mm": 5.0}
  },
  "material": "A572GR50",
  "notes": "200x150x15 mm A572 Gr 50 collision post, 2.6 m cantilever; FRA 49 CFR 238.211 LC3."
}
```
