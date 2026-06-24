# 326 - TEKNOFEST Tarımsal İKA Cantilevered Tool Arm (single-part eval)

Item id: `326_i_me6_teknofest_tarim_ugv_tool_arm`

A submission-agnostic CalculiX eval kit. The agent supplies a cadquery
`build.py` that emits `out.step` + `meta.json`; the shared runner
(`scripts/ccx_eval/grade_ccx.py`) meshes with gmsh, splices BCs from
`analysis_template.inp` via `wire_bcs.py`, runs `ccx_2.22`, then invokes
`check.py` to score against the five pass/fail requirements R1..R5 in
`spec.json`.

## Item

National aerospace student benchmark Tarımsal İnsansız Kara Aracı
(Agricultural UGV) primary tool arm. 6061-T6 welded box-beam cantilever,
60 x 40 mm cross-section, 3 mm wall, 0.8 m reach, 4-bolt M10 8.8 flange
mount to the chassis, holds a 5 kg interchangeable agricultural
implement (spray nozzle / sensor boom / mechanical weeder).

## Files in this directory

| File                    | Origin     | Role                                                                |
|-------------------------|------------|---------------------------------------------------------------------|
| `spec.json`             | spec-side  | Authoritative spec (envelope, materials, load cases, R1..R5)        |
| `analysis_template.inp` | spec-side  | Material + BCs + 6 *STEPs (LC1..LC5 + *FREQUENCY); mesh+NSETs spliced by `wire_bcs.py` |
| `check.py`              | spec-side  | Reads `model.dat` -> R1..R5 PASS/FAIL                               |
| `notes.md`              | spec-side  | Source provenance + TEKNOFEST rule references                       |
| `build.py`              | submission | Reference cadquery script: 60x40x3 mm hollow box, 800 mm long       |
| `meta.json`             | submission | Selectors NFIXED (shoulder), NLOAD (tip) for `wire_bcs.py`          |
| `out.step`              | submission | Single-body STEP geometry (closed-box hollow shell)                 |

## Pipeline

```
build.py                    # emits out.step + meta.json
   |
   v
gmsh -3 out.step  ->  mesh.inp
   |
   v
wire_bcs.py  mesh.inp meta.json analysis_template.inp model.inp
   |
   v
ccx_2.22 model              # produces model.dat / model.frd
   |
   v
check.py                    # parses model.dat + spec.json -> R1..R5
```

Run end-to-end:

```bash
/opt/anaconda3/envs/cadquery/bin/python \
   ./scripts/ccx_eval/grade_ccx.py \
   ./docs/eval/ccx_50/single/326_i_me6_teknofest_tarim_ugv_tool_arm
```

## Required NSETs (from `meta.json`)

| NSET     | Selector              | Description                                                      |
|----------|-----------------------|------------------------------------------------------------------|
| `NFIXED` | `face: x_min`         | Shoulder end-cap face at x = 0 (4-bolt M10 chassis mount)        |
| `NLOAD`  | `face: x_max`         | Tip end-cap face at x = 800 mm (5 kg implement attachment)       |

`Eall` is auto-built by `wire_bcs.py` by unioning every `C3D*` ELSET that
gmsh emits — the *SOLID SECTION assignment in `analysis_template.inp`
binds 6061-T6 to that element set. Units throughout the deck are
mm / N / MPa / t (tonne), consistent with a gmsh-meshed STEP in mm.

## Load cases (analysis_template.inp)

| Step | LC   | Load                                                                       |
|------|------|----------------------------------------------------------------------------|
| 1    | LC1  | `*CLOAD NLOAD,3,-49.05`  (5 kg static gravity at tip in -Z)                |
| 2    | LC2  | `*CLOAD NLOAD,3,-98.10`  (2 g curb-drop shock, effective 10 kg at tip)     |
| 3    | LC3  | `*CLOAD NLOAD,2,+12.69; NLOAD,3,-47.38` (15 deg slope: bend + slight tors) |
| 4    | LC4  | `*CLOAD NLOAD,2,+150.0`  (implement-ground impact, 150 N horizontal)       |
| 5    | LC5  | `*CLOAD NLOAD,3,-50.0`   (fatigue alternating amplitude reference)         |
| 6    | mode | `*FREQUENCY 4`           (first cantilever eigenfrequency, R5 gate)        |

The 4-bolt shoulder flange is idealised as a fully clamped end-cap
face (`*BOUNDARY NFIXED, 1, 3`) — pin/clamp distinctions at the bolt
holes are below the resolution of this single-body simplified geometry.

## Pass / fail map (R1..R5)

| Req  | Source         | Metric                                              | Limit             |
|------|----------------|-----------------------------------------------------|-------------------|
| R1   | FEM steps 1-4  | Peak von Mises in arm under LC1..LC4                | <= 184 MPa        |
| R2   | FEM step 1     | Tip deflection magnitude under LC1                  | <= 5 mm           |
| R3   | closed-form    | Goodman-equivalent fully-reversed alt stress at root | <= 74 MPa         |
| R4   | closed-form    | Arm dry mass from box-section geometry + density    | <= 4.5 kg         |
| R5   | FEM step 6     | First cantilever eigenfrequency                     | >= 25 Hz          |

R1 enforces the 1.5 yield FoS on 6061-T6 (276 MPa / 1.5 = 184 MPa). R2
preserves implement positioning accuracy. R3 is the Goodman-corrected
fatigue gate (96 MPa endurance / 1.3 margin = 74 MPa allowable); R3
is closed-form because spec.json's `verification.requires_non_fea_solver`
flags fatigue as a separate post-processing step (e.g. ANSYS nCode,
FE-SAFE). R4 is closed-form because the C3D solid mesh integration
points report stress, not section mass. R5 decouples the arm's first
mode from typical agricultural-UGV drive-train vibration.

## Engineering interpretation

- The 60 x 40 OD x 3 mm wall x 800 mm closed-box section is heavily
  overbuilt for a 5 kg static cantilever load (R1 passes with ~12x
  margin in the reference geometry). Margin is set by the 25 Hz
  modal floor (R5) and by weldability and bolt-pad reinforcement at
  the shoulder, not by the static stress allowable.
- Closed-form arm mass: A_section = 60*40 - 54*34 = 540 mm^2;
  m = 540 mm^2 * 800 mm * 2.7e-9 t/mm^3 = 1.166e-3 t = 1.17 kg
  (well below the 4.5 kg R4 budget).
- Closed-form fatigue: M_alt = 50 N * 800 mm = 40000 N*mm,
  I_strong = (40*60^3 - 34*54^3)/12 = 273564 mm^4, c = 30 mm,
  sigma_alt = 4.39 MPa; Goodman-equivalent at S_ut = 310 MPa with
  R=0 mean = sigma_alt is sigma_alt / (1 - 4.39/310) = 4.45 MPa,
  comfortably below 74 MPa.
- The B32R hand-meshed `model.inp` (legacy) gives a first cantilever
  mode of 70.8 Hz; the solid C3D mesh from `build.py` produces a
  comparable first mode (closed-box sections are well-approximated
  by Euler-Bernoulli for L/h = 13).
