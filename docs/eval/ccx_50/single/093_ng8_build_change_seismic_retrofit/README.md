# NG8 — Build Change Seismic Retrofit (URM 2-story house)

Submission-agnostic eval kit for one item in the CCX-50 CalculiX FEM suite.
The kit checks a representative seismic-retrofit package for a 2-story
unreinforced clay-brick masonry (URM) family house in a PGA 0.30-0.40 g
zone (Nepal / Philippines / Colombia analog) per **Build Change Seismic
Retrofit Guidelines (2022)**, with secondary references to **ASCE 41-17 /
FEMA 356 / FEMA P-2208** for the nonlinear-static-procedure idiom.

House envelope: 7.0 x 8.5 m plan, two stories (3.0 m each), 230 mm URM
walls, 120 mm RC roof slab. Retrofit elements: continuous RC tie beams at
floor + roof (200 x 200 mm, 4 x 12 mm bars), 8 RC corner columns
(200 x 200 mm), vertical steel strap ties at jambs, and 40 mm RC jackets on
critical ground-floor walls.

## Files

| File | Owner | Purpose |
| --- | --- | --- |
| `spec.json` | spec | Authoritative requirements R1-R5, geometry, materials, load cases. |
| `build.py` | submission (agent) | cadquery script -> `out.step` + `meta.json`. |
| `analysis_template.inp` | spec | CCX deck (materials + BCs + loads) spliced over the meshed STEP. |
| `check.py` | spec | Closed-form Build Change / ASCE 41 verification + FE sanity. |
| `notes.md` | spec | Cited guidance + result table + caveats. |
| `README.md` | spec | This file. |

## Pipeline

The shared runner `scripts/ccx_eval/grade_ccx.py` wires this together:

```
build.py            ->  out.step + meta.json
gmsh -3 out.step    ->  mesh.inp                (C3D4 tets, ELSET=Volume1)
wire_bcs.py         ->  model.inp               (mesh + NSETs + analysis_template)
ccx_2.22 model      ->  model.dat / model.frd
check.py            ->  check.log + PASS/FAIL summary
```

Run end-to-end:

```bash
/opt/anaconda3/envs/cadquery/bin/python \
    scripts/ccx_eval/grade_ccx.py \
    docs/eval/ccx_50/single/093_ng8_build_change_seismic_retrofit
```

## Geometry (build.py)

Simplified rectangular wall panel + 2 corner columns, in **meters**:

```
URM panel        : 8.5 m (X)  x  0.27 m (Y)  x  3.0 m (Z)
                   (composite t = 0.23 m URM + 0.04 m RC jacket)
Corner column L  : 0.27 m (X) x  0.27 m (Y)  x  3.0 m at X = -0.27..0
Corner column R  : 0.27 m (X) x  0.27 m (Y)  x  3.0 m at X = 8.5..8.77
```

Frame: +X along-wall, +Y wall-thickness, +Z up. Base at Z = 0 is fixed.
The three solids are fused into a single compound; gmsh tags the result
as one `VOLUME` physical group, and `analysis_template.inp` uses
`ELSET=Eall` (auto-built by `wire_bcs.py` from gmsh's volume sets).

`meta.json` declares two NSET selectors:
- `NFIXED` — `face: z_min` — foundation, fully fixed.
- `NTOP`   — `face: z_max` — roof level, used for top-displacement read-out.

## FE analysis (analysis_template.inp)

Linear-elastic equivalent-static check — the FE deck supports the
closed-form ASCE 41 capacity/demand check; it is not a full pushover.

- **Material** — single composite `URM_RC` (volume-weighted average of
  URM and RC jacket/columns): E = 4.5 GPa, nu = 0.20, rho = 1950 kg/m^3.
  Multi-material refinement is left to the agent.
- **Boundary** — `NFIXED` clamped in 1-3.
- **Loads** — combined LC1 + LC2 in one `*STATIC` step:
  - Gravity 9.81 m/s^2 in -Z (LC2 dead).
  - Equivalent lateral seismic 0.35 * 9.81 = 3.4335 m/s^2 in +X (LC1),
    applied as a mass-proportional inertial body force via `*DLOAD GRAV`.
- **Output** — `*EL PRINT S` (URM stresses), `*NODE PRINT U` (NTOP top
  displacement), plus `.frd` for visualisation.

## Verification (check.py)

Five Build Change / ASCE 41 pass/fail criteria:

| ID | Metric | Source | Limit |
| -- | ------ | ------ | ----- |
| **R1** | Design PGA (g) | spec / Build Change 0.3-0.4 g band | >= 0.35 g |
| **R2** | New mortar compressive strength (MPa) | spec / Build Change | >= 5.0 MPa |
| **R3** | Wall density per floor per direction (%) | geometric plan check | >= 3.0 % |
| **R4** | Corner ties (corners * levels) | spec retrofit schedule | == 16 |
| **R5** | Pushover base-shear capacity / demand | closed-form ASCE 41 NSP | >= 1.0 |

R3 derivation:
- Plan area A = 7.0 x 8.5 = 59.5 m^2.
- Effective wall thickness t_eff = 0.230 + 0.040 = 0.270 m.
- 3 walls in each direction (2 exterior + 1 interior).
- rho_X = 100 * 3 * 8.5 * 0.27 / 59.5 = **11.57 %**.
- rho_Y = 100 * 3 * 7.0 * 0.27 / 59.5 = **9.53 %**.

R5 derivation:
- Seismic weight W ~ 1409 kN (URM walls + jackets + tie beams + columns +
  slabs + 25 % live).
- Demand V = 0.35 W = 493 kN.
- Capacity V_cap = v_allow * A_w with v_allow = 0.20 MPa post-retrofit
  allowable; critical (Y) direction A_w = 5.67 m^2 -> V_cap = 1134 kN.
- Capacity / demand = **2.30**, well above 1.0.

The FE deck produces top displacement ~0.025 mm and peak URM shear
~0.02 MPa under 0.35 g static — both far inside elastic, consistent with
the closed-form result.

## Caveats

- Wall-density "3.0 %" is a representative lower bound; per-country Build
  Change manuals may set 4-5 % for higher PGA bands.
- v_allow = 0.20 MPa for URM + 40 mm jacket is conservative.
- Foundation retrofit, non-structural hazards (chimneys, parapets), and
  diaphragm strengthening are explicitly out of scope per `spec.json`.
- The reference `build.py` is a single composite material; an agent
  submission MAY emit a multi-material STEP (e.g. one body per ELSET URM
  / RC_COL / RC_TIE) and override `__MATERIAL__` via `meta.json`.
