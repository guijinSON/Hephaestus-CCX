# H-CCX benchmark data

```
briefs/single/   318 single-part briefs (*.json)   ┐ 466-case candidate pool
briefs/multi/    148 multi-part briefs  (*.json)    ┘
kits/single/     20 single-part evaluation kits     ┐ curated 50-case benchmark
kits/multi/      30 multi-part  evaluation kits      ┘
manifest.json    the curated 50 (kind, id, path, spec, check)
```

## briefs/ — the candidate pool (466)

One JSON file per brief: the narrative `full_prompt`, a `short_prompt`, and a
typed list of pass/fail `requirements`. Briefs are de-identified — source
identity (author, host, URL) is not persisted, and numeric limits are written
inline so each brief is self-contained. Some specs carry an `original_sample`
field; it is an opaque internal handle (a path string), not a runtime input.

## kits/ — the curated benchmark (50)

Each kit is a runnable directory:

| file | role |
|------|------|
| `spec.json` | the brief + typed requirements |
| `check.py` | spec-side post-processor; prints `PASS/FAIL/SKIP` and exits non-zero on any FAIL |
| `analysis_template.inp` | spec-side CalculiX deck (materials, BCs by NSET name, loads, `*STEP`s) |
| `build.py` | reference geometry (a submission emits its own `build.py` → `out.step` + `meta.json`) |
| `README.md` | what the case is and which standard it derives from |

Single-part kits are handwritten; multi-part kits are produced by
`scripts/ccx_eval/generate_multipart_coverage_kits.py` from `briefs/multi/`.

## manifest.json — the curated 50

50 entries (20 single + 30 multi), 49 unique brief IDs:
`291_i_ea6_robomaster_rmuc_2025_17mm_launcher` appears as both a single-part and
a multi-part case. Each entry is `{kind, id, path, spec, check}` with
repo-relative paths.
