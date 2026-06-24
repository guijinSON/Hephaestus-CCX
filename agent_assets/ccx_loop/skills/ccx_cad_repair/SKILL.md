# CCX CAD Repair Skill

Provenance: rewritten for native Codex/Claude CLI use from
`skills/cadquery_architect_skill`. This file intentionally omits Hephaestus
artifact-tool instructions, pipeline role names, and host-path assumptions.

Use this skill when producing or repairing a CadQuery/build123d `build.py` for
the CCX eval harness.

## Required Deliverables

- `build.py`: runnable in the current attempt directory.
- `out.step`: STEP AP242 export, non-empty and solid where the brief requires a
  load-bearing body.
- `meta.json`: written by `build.py` every time it runs.
- `notes.md`: concise choices, approximations, selector mapping, and deviations.

## CCX Metadata Rules

- Generate `meta.json` inside `build.py`; do not rely on a hand-edited file.
- Keep `jobname` as `"model"` unless the prompt says otherwise.
- Include a `material` value.
- Include all required `selectors` and `pressure_surfaces` from the FEA metadata
  contract.
- Prefer stable face, box, and broad region selectors over tiny point-like
  selectors.
- Every selector should map to a real exterior region after meshing.
- Explain selector intent in `notes.md`.

## Repair Priorities

- Build failure: simplify geometry first, preserve required envelope and load
  path, then rerun `build.py`.
- Meshing failure: remove sliver features, zero-thickness surfaces, overlapping
  solids, and excessive tiny fillets.
- BC wiring failure: repair selector names, selector coordinates, tolerances, or
  pressure surfaces.
- CCX failure: check constraints, load/support regions, material sections,
  body/elset names, and unconstrained rigid-body motion.
- Engineering failure: change dimensions/material distribution to improve the
  failed metric while preserving already-passing requirements.

## Constraints

- Work only inside the current attempt directory.
- Do not inspect canonical eval files, raw solver logs, or repository files.
- Do not install packages.
- Preserve passing requirements unless the feedback says they are invalid.
