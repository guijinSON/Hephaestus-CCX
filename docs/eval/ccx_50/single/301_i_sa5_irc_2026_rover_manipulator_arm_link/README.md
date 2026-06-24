# 301 - IRC 2026 Rover Manipulator Upper-Arm Link

Welded 6061-T6 closed-box link of a 6-DOF planetary-rover manipulator,
designed against the IRC 2026 Rulebook. This kit is **submission-agnostic**:
the agent supplies a CadQuery `build.py` that emits an `out.step` plus a
`meta.json` of named-face selectors; the eval harness meshes with gmsh,
splices the spec-side `analysis_template.inp` via `wire_bcs.py`, runs
CalculiX, and grades with `check.py`.

## Geometry (reference)

- External 60 mm (Z, strong axis) x 40 mm (Y, weak axis) closed box
- Uniform 3 mm wall, capped at both pivot ends
- 600 mm pin-to-pin (axis along +X, distal x=0 / proximal x=L)
- Material: 6061-T6 (E = 69 GPa, nu = 0.33, rho = 2700 kg/m^3,
  alpha = 23.6e-6 /K), with a 25 mm-wide HAZ band at each weld
  (penalty allowable evaluated in `check.py`)

## Files

| File                     | Origin    | Purpose                                                                   |
|--------------------------|-----------|---------------------------------------------------------------------------|
| `spec.json`              | spec-side | Authoritative requirements + load cases                                   |
| `analysis_template.inp`  | spec-side | Materials + BCs + 6 *STEPs (LC1..LC5 + *BUCKLE), wired by NSET name       |
| `check.py`               | spec-side | Parses `model.frd` / `model.dat`, returns R1..R6 PASS/FAIL                |
| `notes.md`               | spec-side | Design narrative, IRC rule references, hand-checked numbers               |
| `build.py`               | submission| Emits `out.step` + `meta.json` (selectors NFIXED, NLOAD, NALL)            |
| `meta.json`              | submission| Selector vocabulary consumed by `wire_bcs.py`                             |
| `out.step`               | submission| Single-body STEP geometry                                                 |

## Pipeline

```
build.py  --(out.step + meta.json)-->  gmsh  --(mesh.inp)-->
   wire_bcs.py + analysis_template.inp  --(model.inp)-->
   ccx_2.22  --(model.frd, model.dat)-->  check.py  --(check.log)
```

Run end-to-end:

```bash
/opt/anaconda3/envs/cadquery/bin/python \
   ./scripts/ccx_eval/grade_ccx.py \
   ./docs/eval/ccx_50/single/301_i_sa5_irc_2026_rover_manipulator_arm_link
```

## Load cases (analysis_template.inp)

| Step | LC   | Description                                                          |
|------|------|----------------------------------------------------------------------|
| 1    | LC1  | Earth full-extension. 80 N down (+ 4 N-m bending) at proximal end    |
| 2    | LC2  | Mars operational. 19 N down (+ 1.5 N-m bending) at proximal end      |
| 3    | LC3  | E-stop tip deceleration. 100 N lateral (Y) at proximal end           |
| 4    | LC4  | Stow / hard-traverse impact. 30 g body force in +Y on the entire link|
| 5    | LC5  | Thermal: dT = -100 K with both ends fully clamped + Earth gravity    |
| 6    | buck | Linear `*BUCKLE`, 1 N reference axial (-X) load on proximal end      |

Loads are applied as per-node `*CLOAD` over `NLOAD` (the proximal
end-face NSET) with magnitudes calibrated for a representative
end-face count of 76 nodes; the `*DLOAD GRAV` body forces (LC4, LC5)
are mesh-independent. The HAZ band is **not** a separate elset in the
template - it is derived in `check.py` from node x-coordinate
(`x <= 25 mm` or `x >= L - 25 mm`).

## Pass / fail (R1..R6, evaluated by check.py)

| Req  | Metric                                  | Limit       | Applies to       |
|------|-----------------------------------------|-------------|------------------|
| R1   | Max von Mises in BASE metal             | <= 184 MPa  | LC1, LC3, LC4    |
| R2   | Max von Mises in HAZ band (+/- 25 mm)   | <= 92 MPa   | LC1, LC3, LC4    |
| R3   | Tip deflection (proximal end resultant) | <= 2.5 mm   | LC1              |
| R4   | First-mode buckling factor              | >= 3.0      | LC4 (axial ref)  |
| R5   | Link mass                               | <= 850 g    | design           |
| R6a  | Combined thermal + gravity, BASE        | <= 184 MPa  | LC5              |
| R6b  | Combined thermal + gravity, HAZ         | <= 92 MPa   | LC5              |

R1 / R2 enforce a 1.5 yield FoS on 6061-T6 (276 MPa base, 138 MPa
HAZ). R3 protects manipulator tool-tip placement accuracy. R4 is the
classical buckling SF on a `30 g * link_mass` axial reference. R5
enforces the arm mass budget propagated from the IRC 2026 rover total
of 65 kg. R6 is the textbook clamped-bar thermal stress
(`E * alpha * dT ~= 163 MPa`) raised by Poisson coupling at the
clamped face - in the reference geometry R5/R6 fail and the `notes.md`
documents the design-review actions (wall-thickness reduction,
elastic axial release at one bearing).

## Engineering interpretation

- Closed-box section is heavily overbuilt for static manipulator
  loads (R1..R4 pass with 1-2 orders of margin). Realistic: wall
  thickness here is set by weldability, bearing-boss reinforcement
  and the buckling target on the impact case rather than by
  static stress.
- R5 fails because 60 x 40 OD x 3 mm wall x 600 mm gives 338 400
  mm^3 -> 913.7 g at 6061 density vs. the 850 g target. To meet
  R5 the wall would drop to ~2.7 mm or the section to ~58 x 38 OD.
- R6 (LC5 fully-fixed thermal) is conservative; in the real arm
  the joints permit a few um of axial release via bearing
  clearance + harmonic-drive backlash, which would relieve most
  of the 163-323 MPa peak.
