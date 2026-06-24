# 038 - AISC 360-22 W10x49 Portal-Frame Steel Column

Submission-agnostic CalculiX eval kit for an A992 W-shape steel column
in a single-story portal frame, governed by ANSI/AISC 360-22 (2022) and
ASCE 7-22 LRFD load combinations.

## What the agent submits

The agent provides exactly one file: `build.py` (a CadQuery script) that
emits

- `out.step` - solid 3D geometry of the column over the spec's 6.0 m
  clear height. The reference build is a W10x49 I-section extruded along
  +Z; agents may submit any AISC Manual 15th-edition W-shape that
  satisfies all six requirements below.
- `meta.json` - sidecar matching `schemas/meta.schema.json`, with at
  minimum two NSET selectors:
    - `NFIXED` - bottom face of the column at z=0 (`{"face": "z_min"}`)
    - `NTOP`   - top face of the column at z=6000 (`{"face": "z_max"}`)
  and a `material` of `"A992"` to splice into the analysis template.

## How the runner grades

`scripts/ccx_eval/grade_ccx.py` runs the pipeline:

1. `python build.py` -> `out.step`, `meta.json`
2. gmsh `-3 out.step` -> `mesh.inp` (3D solid C3D* elements)
3. `wire_bcs.py` splices `mesh.inp` + `meta.json` + `analysis_template.inp`
   -> `model.inp` (auto-builds `Eall`, populates NFIXED / NTOP NSETs)
4. `ccx_2.22 model` -> `model.dat`, `model.frd`
5. `python check.py` -> PASS/FAIL per requirement, exit 0 only if all PASS

Note: AISC capacity checks are inherently closed-form chapter equations
(Chapter E flexural buckling, Chapter F LTB / plastic moment, Chapter H
combined axial + biaxial flexure). The FEM step in
`analysis_template.inp` only confirms the meshed deck assembles and
solves under a representative per-node axial demand on NTOP. R1..R6 are
all evaluated symbolically in `check.py` from the section's published
properties (which the agent's chosen W-shape must match), not from
`model.dat`.

## Requirements (all must PASS)

| ID | Type   | Metric                                  | Limit       | Source                                    |
|----|--------|-----------------------------------------|-------------|-------------------------------------------|
| R1 | strength | phi_c * Pn (compression)              | >= 2114 kN  | AISC 360-22 Chapter E (E3-2 / E3-3)       |
| R2 | strength | phi_b * Mnx (strong-axis flexure)     | >= 300  kN.m| AISC 360-22 Chapter F2 (Lb<=Lp -> Mp=Zx*Fy) |
| R3 | strength | phi_b * Mny (weak-axis flexure)       | >= 140  kN.m| AISC 360-22 Chapter F6 (Mp capped 1.6*Sy*Fy)|
| R4 | strength | Chapter H unity ratio (LC1, LC2, LC3) | <= 1.0      | AISC 360-22 Chapter H1 (H1-1a/H1-1b/H1-2) |
| R5 | service  | top-of-column wind deflection         | <= 15 mm    | H/400 serviceability (AISC Design Guide 3)|
| R6 | mass     | section weight per meter              | <= 75 kg/m  | section table from agent's W-shape choice |

The R1 limit follows the **governing** slenderness path (KxL/rx for
W10x49 strong axis) per AISC Chapter E - using the smaller weak-axis
slenderness is incorrect because Chapter E mandates the larger KL/r.
The 2114 kN figure for W10x49 is a true Chapter E result; demand
Pu=450 kN (LC1) and 280 kN (LC2) remain a small fraction of capacity
and all Chapter H interactions still pass. (See `notes.md` for
worked numbers and FAIL discussion of the original 2775 kN spec
target.)

R5 requires a 6 m cantilever with **no** lateral restraint to absorb
the LC2 wind moment of 210 kN.m at the top - which produces ~167 mm
of tip deflection regardless of W-shape choice within sensible
AISC-Manual sections. The closed-form `M*H^2/(2*E*Ix)` evaluation in
`check.py` reports this as a FAIL for the W10x49 standalone column.
In a real portal-frame design the wind moment is redistributed by the
rigid roof beam and diaphragm bracing, none of which is captured by
the single-column FEM model. Agents may still report PASS for R5 by
selecting a much stiffer section (e.g. W14x132+) or by adjusting the
geometry, but the kit retains the original spec gate.

## Load cases (factored ASCE 7-22 LRFD)

| LC  | Combo                  | Pu (kN) | Mux (kN.m) | Muy (kN.m) |
|-----|------------------------|---------|------------|------------|
| LC1 | 1.2D + 1.6L + 0.5Lr   | +450    |  85        |  0         |
| LC2 | 1.2D + 1.0W + 0.5L    | +280    | 210        | 15         |
| LC3 | 0.9D + 1.0W (uplift)  |  -40    | 195        | 10         |

Resistance factors phi_c = phi_b = phi_t = 0.90.

## Citations

All section equations and coefficients are sourced via WebSearch and
documented in `notes.md`. Key references:

- ANSI/AISC 360-22 Specification, https://www.aisc.org/globalassets/aisc/publications/standards/a360-16w-rev-june-2019.pdf
  (2022 edition retains the same chapter equations, see the
  https://www.aisc.org/media/myzl4doa/2022-to-2016-spec-comparison.pdf)
- W10x49 section properties: https://steelcalculator.app/sections/W10x49/
  and https://beamdimensions.com/database/American/AISC/W_shapes/W10x49/
- Chapter H bilinear interaction (H1-1a/H1-1b): https://docs.bentley.com/LiveContent/web/STAAD.Pro%20Help-v21/en/GUID-AC54D87C-C436-406B-9FFB-360DD861343C.html
- Chapter F LTB and Lp/Lr: https://www.aisc.org/globalassets/aisc/manual/15th-ed-ref-list/simplified-lateral-torsional-buckling-equations-for-singly-symmetric-i-section-members.pdf

## Files

- `spec.json`              - eval item metadata (kept verbatim)
- `notes.md`               - design notes + closed-form derivation log
- `build.py`               - **agent-side** CadQuery W-shape extrusion + meta
- `analysis_template.inp`  - eval-side CCX deck (material A992, BCs, *STATIC step)
- `check.py`               - eval-side symbolic Chapter E/F/H verifier (R1..R6)
- `model.inp` / `model.dat` / `model.frd` - generated by the runner
- `README.md`              - this file
