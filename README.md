# Hephaestus-CCX (H-CCX)

H-CCX is a benchmark for engineering-grounded CAD generation. Each case pairs a
self-contained engineering brief with an executable, typed pass/fail requirement
set that is graded by finite-element analysis (CalculiX) plus geometric checks —
not by similarity to a reference mesh.

This repository contains the benchmark assets **and** the full evaluation
harness, including the engineering-code checkers that are not derivable from raw
FEA outputs alone.

## Repository layout

```
scripts/ccx_eval/                 # evaluation harness (see below)
docs/eval/
  samples/                        # 318 single-part candidate briefs (JSON)
  multipart/                      # 148 multi-part candidate briefs (JSON)  → 466-case pool
  ccx_50/
    single/<id>/                  # 20 curated single-part evaluation kits
    multi/<id>/                   # multi-part evaluation kits (incl. the 30 curated)
    manifest.json                 # the curated 50-case list (see note below)
```

## The evaluation harness (`scripts/ccx_eval/`)

| file | role |
|------|------|
| `grade_ccx.py` | end-to-end per-item runner: `build.py` → gmsh mesh → `wire_bcs.py` → CalculiX → `check.py`, emitting `grade.json` |
| `single_engineering_check.py` | artifact-driven engineering checker for single-part kits (solver values from `model.dat`, geometry/mass from the mesh, non-FEA fields from `meta.json`) |
| `multi_engineering_check.py` | engineering checker for multi-part assemblies (adds weld/mate/clearance interface checks) |
| `engineering_requirements.py` | shared requirement parsing/typing used by both checkers |
| `wire_bcs.py` | splices the submitted mesh + named selectors into the spec-side analysis template |
| `generate_multipart_coverage_kits.py` | regenerates multi-part coverage kits from the pool |
| `visualize_case_feedback.py` | renders per-case requirement feedback |

The engineering-code-based requirements (the ones not directly read off stress,
displacement, frequency, buckling, or mass) are evaluated by
`single_engineering_check.py` / `multi_engineering_check.py`. These import
`engineering_requirements.py` and are invoked by each case's `check.py`, so the
whole `scripts/ccx_eval/` directory is needed to reproduce the full benchmark.

## The curated 50 — and why a flat list shows 49

The curated benchmark is **50 evaluation instances**: **20 single-part + 30
multi-part**, enumerated in `docs/eval/ccx_50/manifest.json`.

One brief — `291_i_ea6_robomaster_rmuc_2025_17mm_launcher` — is evaluated in
**two distinct configurations**: a single-part case (`ccx_50/single/291…`) and a
multi-part assembly case (`ccx_50/multi/291…`). They are different specs and
different `check.py` files. Because both share the same brief `id`, a layout that
keys cases by `id` alone collapses them and shows only **49 unique IDs**. Keep the
`single/` vs `multi/` split (as in this repo) to see all 50.

## The 466-case candidate pool

`docs/eval/samples/` (318 single-part) and `docs/eval/multipart/` (148
multi-part) together form the **466-case candidate pool** from which the curated
50 were sampled. Source identity (author, host, URL) is not persisted in the
released specs; numeric limits are written inline.

## Environment

```bash
python3 -m pip install -r requirements.txt
export CCX=/opt/homebrew/bin/ccx_2.22                       # CalculiX binary
export EVAL_PYTHON=/opt/anaconda3/envs/cadquery/bin/python  # env with cadquery + gmsh
# optional: export GMSH=/path/to/gmsh   (else the Gmsh Python API via EVAL_PYTHON is used)
```

## Grade one submission

A working dir must contain `build.py` (the submission), `analysis_template.inp`
(spec-side deck), and `check.py` (spec-side post-processor):

```bash
python scripts/ccx_eval/grade_ccx.py <workdir>
```

## Citation

If you use H-CCX, please cite the accompanying paper (BibTeX to be added on
publication).
