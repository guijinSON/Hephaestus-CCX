# CCX Blueprint Generation Skill

Provenance: rewritten for native Codex/Claude CLI use from
`skills/blueprint_creation_skill`. This file intentionally omits Hephaestus
artifact-tool instructions, pipeline role names, and host-path assumptions.

Use this skill when converting an engineering prompt into a schema-v4-style
design structure that can guide CAD generation.

## Blueprint Goals

- Identify functional requirements, load paths, interfaces, materials, limits,
  and verification requirements.
- Break assemblies into named parts with clear roles and interface logic.
- For each part, define construction units that are simple enough to model
  deterministically.
- Preserve hard numeric constraints exactly.
- State assumptions only when the prompt leaves a genuine gap.

## Schema-V4 Guidance

- Use `assembly_schema_version: 4`.
- Every part should define `construction_units`.
- Use simple construction primitives such as boxes, cylinders, annular sectors,
  and extruded polygons.
- Define support zones with axis-named footprint spans, not arbitrary host paths
  or pipeline artifact references.
- Keep part names stable, lowercase, and filename-safe.

## Verification Focus

- Record which requirements are geometric, mass/envelope, structural, modal,
  buckling, thermal, or unsupported by the available CCX harness.
- Tie each load case to a physical load path and support region.
- Do not invent hidden tests; make assumptions visible in notes.
