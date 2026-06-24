#!/usr/bin/env python3
"""Render one CCX eval case as a compact HTML coverage visualization."""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


CHECK_ROW_RE = re.compile(r"^\[(PASS|FAIL)\]\s+(\S+)\s+class=(\S+)\s+source=(.*)$")
SUMMARY_RE = re.compile(r"SUMMARY\s+PASS=(\d+)\s+FAIL=(\d+)\s+SKIP=(\d+)\s+TOTAL=(\d+)")


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def parse_check_log(path: Path) -> tuple[list[dict[str, str]], dict[str, int]]:
    rows: list[dict[str, str]] = []
    summary = {"pass": 0, "fail": 0, "skip": 0, "total": 0}
    if not path.exists():
        return rows, summary

    for line in path.read_text(errors="ignore").splitlines():
        if match := CHECK_ROW_RE.match(line):
            verdict, requirement_id, req_class, source = match.groups()
            rows.append(
                {
                    "verdict": verdict,
                    "requirement_id": requirement_id,
                    "class": req_class,
                    "source": source,
                }
            )
            continue
        if match := SUMMARY_RE.search(line):
            passed, failed, skipped, total = match.groups()
            summary = {
                "pass": int(passed),
                "fail": int(failed),
                "skip": int(skipped),
                "total": int(total),
            }
    return rows, summary


def requirements(spec: dict[str, Any]) -> list[dict[str, Any]]:
    reqs = (spec.get("requirements") or {}).get("pass_fail_criteria") or []
    return [req for req in reqs if isinstance(req, dict)]


def experiments(spec: dict[str, Any]) -> list[dict[str, Any]]:
    exps = spec.get("fea_experiments") or []
    return [exp for exp in exps if isinstance(exp, dict)]


def load_cases(spec: dict[str, Any]) -> list[dict[str, Any]]:
    prompt = spec.get("prompt") or {}
    cases = prompt.get("load_cases") or spec.get("load_cases") or []
    return [case for case in cases if isinstance(case, dict)]


def flatten_constraints(constraints: dict[str, Any]) -> list[tuple[str, str]]:
    rows = []
    for key, value in constraints.items():
        label = str(key).replace("_", " ")
        if isinstance(value, (dict, list)):
            value_text = json.dumps(value, sort_keys=True)
        else:
            value_text = str(value)
        rows.append((label, value_text))
    return rows


def value_mm(constraints: dict[str, Any], key: str) -> str:
    value = constraints.get(key)
    return "?" if value is None else str(value)


def render_geometry_svg(spec: dict[str, Any]) -> str:
    prompt = spec.get("prompt") or {}
    constraints = prompt.get("geometric_constraints") or {}
    od = value_mm(constraints, "outer_diameter_mm")
    id_ = value_mm(constraints, "inner_diameter_mm")
    height = value_mm(constraints, "axial_height_mm")
    bolts = constraints.get("flange_bolt_count") or constraints.get("bolt_count") or "?"
    bolt_size = constraints.get("flange_bolt_size") or constraints.get("bolt_size") or ""

    bolt_marks = []
    cx, cy, r = 210, 200, 118
    for i in range(24):
        angle = (i / 24) * 6.283185307179586
        x = cx + r * __import__("math").cos(angle)
        y = cy + r * __import__("math").sin(angle)
        bolt_marks.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.2" class="bolt"/>')

    return f"""
    <svg class="diagram" viewBox="0 0 920 430" role="img" aria-label="Geometry schematic">
      <defs>
        <linearGradient id="skin" x1="0" x2="1">
          <stop offset="0" stop-color="#dfe7ee"/>
          <stop offset="1" stop-color="#aebbc5"/>
        </linearGradient>
      </defs>
      <rect x="34" y="34" width="852" height="362" rx="8" class="canvas"/>
      <g>
        <text x="210" y="64" class="caption">End flange / bolt pattern</text>
        <circle cx="210" cy="200" r="142" class="outer"/>
        <circle cx="210" cy="200" r="104" class="inner"/>
        <circle cx="210" cy="200" r="118" class="bolt-circle"/>
        {''.join(bolt_marks)}
        <line x1="68" y1="364" x2="352" y2="364" class="dim"/>
        <text x="138" y="387" class="label">OD {esc(od)} mm</text>
        <line x1="106" y1="336" x2="314" y2="336" class="dim secondary"/>
        <text x="146" y="326" class="label">ID {esc(id_)} mm</text>
        <text x="120" y="112" class="note">{esc(bolts)} x {esc(bolt_size)} flange</text>
      </g>
      <g>
        <text x="610" y="64" class="caption">Axial assembly stack</text>
        <rect x="520" y="98" width="210" height="204" rx="2" fill="url(#skin)" class="body"/>
        <rect x="505" y="82" width="240" height="22" rx="3" class="bulkhead"/>
        <rect x="505" y="296" width="240" height="22" rx="3" class="bulkhead"/>
        <rect x="545" y="195" width="160" height="12" rx="2" class="deck"/>
        <rect x="532" y="116" width="18" height="168" rx="2" class="rail"/>
        <rect x="700" y="116" width="18" height="168" rx="2" class="rail"/>
        <rect x="730" y="150" width="54" height="62" rx="3" class="plate"/>
        <circle cx="733" cy="256" r="25" class="boss"/>
        <line x1="488" y1="82" x2="488" y2="318" class="dim"/>
        <text x="395" y="206" class="label">{esc(height)} mm height</text>
        <text x="558" y="191" class="note">payload deck</text>
        <text x="746" y="145" class="note">umbilical</text>
        <text x="765" y="263" class="note">boom hardpoint</text>
      </g>
    </svg>
    """


def render_chips(rows: list[tuple[str, str]]) -> str:
    return "".join(
        f'<div class="chip"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'
        for label, value in rows
    )


def render_load_cases(cases: list[dict[str, Any]]) -> str:
    cards = []
    for case in cases:
        rows = []
        for key, value in case.items():
            if key in {"id", "name"}:
                continue
            rows.append(f"<dt>{esc(key.replace('_', ' '))}</dt><dd>{esc(value)}</dd>")
        cards.append(
            f"""
            <article class="mini-card">
              <h3>{esc(case.get('id'))}: {esc(case.get('name'))}</h3>
              <dl>{''.join(rows)}</dl>
            </article>
            """
        )
    return "".join(cards)


def render_parts(spec: dict[str, Any]) -> str:
    parts = (spec.get("prompt") or {}).get("parts") or []
    rows = []
    for part in parts:
        constraints = part.get("geometric_constraints") or {}
        rows.append(
            "<tr>"
            f"<td><strong>{esc(part.get('part_id'))}</strong></td>"
            f"<td>{esc(part.get('name'))}</td>"
            f"<td>{esc(part.get('count_min'))}</td>"
            f"<td>{esc(part.get('role'))}</td>"
            f"<td>{esc(json.dumps(constraints, sort_keys=True))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_requirement_rows(reqs: list[dict[str, Any]], exps: list[dict[str, Any]]) -> str:
    exp_by_req = {str(exp.get("requirement_id")): exp for exp in exps}
    rows = []
    for index, req in enumerate(reqs):
        rid = str(req.get("id") or f"REQ_{index}")
        exp = exp_by_req.get(rid, {})
        req_text = req.get("description") or req.get("criterion") or req.get("text") or req.get("metric") or ""
        if not req_text:
            req_text = json.dumps(req, sort_keys=True)
        rows.append(
            "<tr>"
            f"<td><strong>{esc(rid)}</strong></td>"
            f"<td>{esc(exp.get('requirement_class'))}</td>"
            f"<td>{esc(exp.get('analysis_card'))}</td>"
            f"<td>{esc(exp.get('deck_step'))}</td>"
            f"<td>{esc(req_text)}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_feedback_rows(check_rows: list[dict[str, str]]) -> str:
    rows = []
    for row in check_rows:
        status = row["verdict"].lower()
        rows.append(
            "<tr>"
            f'<td><span class="status {status}">{esc(row["verdict"])}</span></td>'
            f"<td><strong>{esc(row['requirement_id'])}</strong></td>"
            f"<td>{esc(row['class'])}</td>"
            f"<td>{esc(row['source'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_stage_rows(grade: dict[str, Any]) -> str:
    rows = []
    stages = grade.get("stages") or {}
    for name, stage in stages.items():
        rc = stage.get("rc")
        status = "pass" if rc == 0 else "fail"
        rows.append(
            "<tr>"
            f"<td>{esc(name)}</td>"
            f'<td><span class="status {status}">rc={esc(rc)}</span></td>'
            f"<td>{esc(stage.get('elapsed_s'))} s</td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(case_dir: Path, out_path: Path) -> None:
    spec = load_json(case_dir / "spec.json")
    grade = load_json(case_dir / "grade.json")
    check_rows, summary = parse_check_log(case_dir / "check.log")
    prompt = spec.get("prompt") or {}
    global_geometry = flatten_constraints(prompt.get("geometric_constraints") or {})
    reqs = requirements(spec)
    exps = experiments(spec)
    cases = load_cases(spec)
    title = prompt.get("title") or spec.get("id") or case_dir.name

    html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} - FEA Coverage Visualization</title>
  <style>
    :root {{
      --ink: #172027;
      --muted: #5c6872;
      --line: #d9e0e6;
      --bg: #f6f8fa;
      --panel: #ffffff;
      --blue: #2f6f9f;
      --green: #237a57;
      --red: #b43b3b;
      --amber: #9a6a19;
      --steel: #60717f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    header {{ margin-bottom: 18px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; letter-spacing: 0; }}
    h2 {{ font-size: 18px; margin: 0 0 12px; letter-spacing: 0; }}
    h3 {{ font-size: 14px; margin: 0 0 8px; letter-spacing: 0; }}
    p {{ margin: 0; }}
    .subtle {{ color: var(--muted); max-width: 980px; }}
    .grid {{ display: grid; gap: 16px; }}
    .two {{ grid-template-columns: minmax(0, 1fr) minmax(320px, 0.72fr); align-items: start; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 18px;
    }}
    .prompt-text {{ white-space: pre-wrap; font-size: 14px; color: #26323b; }}
    .chips {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      background: #fbfcfd;
      min-width: 138px;
    }}
    .chip span {{ display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; }}
    .chip strong {{ display: block; font-size: 14px; margin-top: 2px; overflow-wrap: anywhere; }}
    .mini-grid {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
    .mini-card {{ border: 1px solid var(--line); border-radius: 6px; padding: 12px; background: #fbfcfd; }}
    dl {{ display: grid; grid-template-columns: 120px 1fr; gap: 4px 8px; margin: 0; font-size: 12px; }}
    dt {{ color: var(--muted); }}
    dd {{ margin: 0; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); padding: 9px 8px; }}
    th {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }}
    .table-wrap {{ overflow-x: auto; }}
    .status {{ display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; font-weight: 700; }}
    .status.pass {{ background: #e7f4ee; color: var(--green); }}
    .status.fail {{ background: #fdeaea; color: var(--red); }}
    .status.skip {{ background: #fff4d9; color: var(--amber); }}
    .summary {{ display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }}
    .summary .chip {{ min-width: 96px; }}
    .diagram {{ width: 100%; height: auto; display: block; }}
    .canvas {{ fill: #f9fbfc; stroke: var(--line); }}
    .outer {{ fill: #ccd6de; stroke: var(--steel); stroke-width: 2; }}
    .inner {{ fill: #f9fbfc; stroke: #8fa0ac; stroke-width: 2; }}
    .bolt-circle {{ fill: none; stroke: #8fa0ac; stroke-dasharray: 6 5; }}
    .bolt {{ fill: var(--blue); }}
    .body {{ stroke: var(--steel); }}
    .bulkhead {{ fill: #8999a6; stroke: #5d6c77; }}
    .deck {{ fill: #2f6f9f; }}
    .rail {{ fill: #5d8b70; }}
    .plate {{ fill: #c38b3a; stroke: #8f6426; }}
    .boss {{ fill: #b96d63; stroke: #8f473e; }}
    .dim {{ stroke: #27333b; stroke-width: 1.5; marker-start: none; marker-end: none; }}
    .secondary {{ stroke: #6c7982; }}
    .caption {{ font-size: 18px; font-weight: 700; fill: var(--ink); }}
    .label {{ font-size: 15px; fill: var(--ink); }}
    .note {{ font-size: 13px; fill: var(--muted); }}
    @media (max-width: 860px) {{
      main {{ padding: 16px; }}
      .two {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 22px; }}
    }}
  </style>
</head>
<body>
<main class="grid">
  <header>
    <h1>{esc(title)}</h1>
    <p class="subtle">Case {esc(spec.get('id') or case_dir.name)}. Static visualization of the prompt, geometry requirements, and live FEA coverage feedback from the generated CCX kit.</p>
  </header>

  <section class="grid two">
    <article class="panel">
      <h2>Prompt Snapshot</h2>
      <p class="prompt-text">{esc(spec.get('short_prompt') or prompt.get('brief') or spec.get('full_prompt'))}</p>
    </article>
    <article class="panel">
      <h2>Geometry Schematic</h2>
      {render_geometry_svg(spec)}
    </article>
  </section>

  <section class="panel">
    <h2>Global Geometry Requirements</h2>
    <div class="chips">{render_chips(global_geometry)}</div>
  </section>

  <section class="panel">
    <h2>Declared Load Cases</h2>
    <div class="mini-grid">{render_load_cases(cases)}</div>
  </section>

  <section class="panel">
    <h2>Part-Level Geometry Requirements</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>ID</th><th>Part</th><th>Min Count</th><th>Role</th><th>Geometry</th></tr></thead>
        <tbody>{render_parts(spec)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Requirement to Experiment Map</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Requirement</th><th>Class</th><th>Card</th><th>Deck Step</th><th>Requirement Text</th></tr></thead>
        <tbody>{render_requirement_rows(reqs, exps)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>FEA Feedback</h2>
    <div class="summary">
      <div class="chip"><span>Pass</span><strong>{summary['pass']}</strong></div>
      <div class="chip"><span>Fail</span><strong>{summary['fail']}</strong></div>
      <div class="chip"><span>Skip</span><strong>{summary['skip']}</strong></div>
      <div class="chip"><span>Total</span><strong>{summary['total']}</strong></div>
      <div class="chip"><span>Final RC</span><strong>{esc(grade.get('final_rc', 'n/a'))}</strong></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Status</th><th>Requirement</th><th>Class</th><th>Checker Feedback</th></tr></thead>
        <tbody>{render_feedback_rows(check_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="panel">
    <h2>Grader Stages</h2>
    <div class="table-wrap">
      <table>
        <thead><tr><th>Stage</th><th>Return Code</th><th>Elapsed</th></tr></thead>
        <tbody>{render_stage_rows(grade)}</tbody>
      </table>
    </div>
  </section>
</main>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html_doc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path, help="Generated CCX kit directory")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="HTML output path. Defaults to artifacts/fea_case_visualizations/<case>.html",
    )
    args = parser.parse_args()

    case_dir = args.case_dir.resolve()
    if args.out is None:
        out_path = Path("artifacts") / "fea_case_visualizations" / f"{case_dir.name}.html"
    else:
        out_path = args.out
    render_html(case_dir, out_path)
    print(out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
