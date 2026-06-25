# Hephaestus-CCX (H-CCX)

H-CCX is a benchmark for engineering-grounded CAD generation. Each case pairs a
self-contained engineering brief with an executable, typed pass/fail requirement
set that is graded by finite-element analysis (CalculiX) plus geometric checks —
not by similarity to a reference mesh.

This repository ships the benchmark data and the evaluation harness, including
the engineering-code checkers that are not derivable from raw FEA outputs alone.

## Layout

```
benchmark/
  briefs/single/   318 single-part briefs (*.json)  ┐ 466-case candidate pool
  briefs/multi/    148 multi-part briefs  (*.json)  ┘
  kits/single/<id>/   20 single-part evaluation kits  ┐ curated 50-case benchmark
  kits/multi/<id>/    30 multi-part  evaluation kits   ┘
  manifest.json       the curated 50 (see note below)
  README.md           data dictionary

scripts/ccx_eval/     evaluation harness (grading + checkers)
requirements.txt      evaluator dependencies (cadquery + gmsh)
```

A **brief** is prompt + typed requirements only (`*.json`). A **kit** is a
runnable directory: the brief's `spec.json` plus `check.py` (the pass/fail
post-processor), `analysis_template.inp` (the spec-side CalculiX deck), a
reference `build.py`, and a `README.md`. The 466 briefs are the candidate pool;
the 50 kits are the curated benchmark sampled from it.

## The curated 50 — and why a flat list shows 49

The benchmark is **50 evaluation instances**: **20 single-part + 30 multi-part**,
enumerated in `benchmark/manifest.json`.

One brief — `291_i_ea6_robomaster_rmuc_2025_17mm_launcher` — is evaluated in two
configurations: a single-part case (`kits/single/291…`) and a multi-part
assembly case (`kits/multi/291…`). They are different specs and different
`check.py` files but share the same brief `id`, so a layout keyed by `id` alone
collapses them and shows only **49 unique IDs**. Keep the `single/` vs `multi/`
split (as here) to see all 50.

## The 466-case candidate pool

`benchmark/briefs/single/` (318) and `benchmark/briefs/multi/` (148) together
form the 466-case candidate pool the curated 50 were sampled from. Source
identity (author, host, URL) is not persisted in the released specs; numeric
limits are written inline.

## Harness (`scripts/ccx_eval/`)

| file | role |
|------|------|
| `grade_ccx.py` | end-to-end per-item runner: `build.py` → gmsh mesh → `wire_bcs.py` → CalculiX → `check.py`, writing `grade.json` |
| `single_engineering_check.py` | artifact-driven engineering checker for single-part kits |
| `multi_engineering_check.py` | engineering checker for multi-part assemblies (adds weld/mate/clearance interface checks) |
| `engineering_requirements.py` | shared requirement parsing/typing used by both checkers |
| `wire_bcs.py` | splices the submitted mesh + named selectors into the spec-side analysis template |
| `generate_multipart_coverage_kits.py` | regenerates multi-part kits from `benchmark/briefs/multi/` |
| `visualize_case_feedback.py` | renders per-case requirement feedback |

The engineering-code requirements (not read directly off stress, displacement,
frequency, buckling, or mass) are evaluated by `single_engineering_check.py` /
`multi_engineering_check.py`. The pure checkers are standard-library only; the
full pipeline additionally needs `cadquery`, `gmsh`, and a CalculiX binary.

## Environment

```bash
python3 -m pip install -r requirements.txt   # cadquery + gmsh
export CCX=/opt/homebrew/bin/ccx_2.22         # CalculiX binary (e.g. brew install calculix)
export EVAL_PYTHON=$(which python3)            # python of the env above
# optional: export GMSH=/path/to/gmsh   (else the Gmsh Python API via EVAL_PYTHON is used)
```

## Grade one submission

A working dir must contain `build.py` (the submission), `analysis_template.inp`
(spec-side deck), and `check.py` (spec-side post-processor) — exactly what each
kit ships, so you can grade the reference build directly:

```bash
cp -r benchmark/kits/single/034_s3_nasa_5020b_bolted_joint /tmp/work
python scripts/ccx_eval/grade_ccx.py /tmp/work
cat /tmp/work/grade.json        # per-stage return codes
cat /tmp/work/check.log         # PASS/FAIL/SKIP per requirement
```

To grade your own model, replace `build.py` with a script that emits `out.step`
and `meta.json`, then run `grade_ccx.py` on that directory.

## Citation

If you use H-CCX, please cite the accompanying paper (BibTeX to be added on
publication).
