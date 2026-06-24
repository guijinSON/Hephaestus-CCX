#!/usr/bin/env python3
"""
Fire `claude -p` once per item across the curated 20 (single-part) and
curated 30 CCX (multipart) sets, asking each invocation to produce a STEP
file from the sample's `short_prompt`.

For each item:
  - Per-item working dir under <out-root>/<timestamp>/{single,multi}/<id>/
  - Default out-root is OUTSIDE this repo (~/hephaestus_step_runs) so the
    spawned `claude` process does NOT walk up into Hephaestus and inherit
    its CLAUDE.md / AGENTS.md / .claude/settings.json. Each item sees only
    its own dir and the prompt.
  - Reads short_prompt from local docs/eval/{samples,multipart}/<id>.json
    (read once, here, then passed to claude as text — claude itself never
    touches this repo).
  - Writes prompt.md and asks Claude to produce out.step via cadquery
  - Captures stdout/stderr to run.log and reports out.step presence + size
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLES_DIR = REPO_ROOT / "docs" / "eval" / "samples"
MULTIPART_DIR = REPO_ROOT / "docs" / "eval" / "multipart"
CCX_SINGLE_DIR = REPO_ROOT / "docs" / "eval" / "ccx_50" / "single"
CCX_MULTI_DIR = REPO_ROOT / "docs" / "eval" / "ccx_50" / "multi"

CURATED_20_SINGLE = [
    "034_s3_nasa_5020b_bolted_joint",
    "056_jp1_student_formula_japan",
    "038_s7_aisc_360_22_steel_column",
    "090_ng5_iso10328_p5_prosthetic_pylon",
    "093_ng8_build_change_seismic_retrofit",
    "291_i_ea6_robomaster_rmuc_2025_17mm_launcher",
    "301_i_sa5_irc_2026_rover_manipulator_arm_link",
    "011_pt11_misumi_aluminum_frame_bracket",
    "252_a12_asme_hpvc_rps",
    "039_s8_fia_art253_rollcage",
    "061_jp6_coma_wars",
    "126_hv2_hyperloop_pod_shell",
    "026_pt26_fra_49cfr238_rail",
    "115_e5_chem_e_car_pressurized_reservoir",
    "050_s19_abs_hsc_hull_bottom_panel",
    "134_hv10_fs_switzerland_wheel_hub",
    "127_hv3_eurobot_robot_chassis",
    "142_hv18_fsgp_solar_array_mount",
    "326_i_me6_teknofest_tarim_ugv_tool_arm",
    "186_sa3_kibocube",
]

CURATED_30_CCX_MULTI = [
    # T1 (2-4 parts)
    "033_s2_ecss_32_10c_spacecraft_panel",
    "041_s10_stanag_4703_light_uas",
    "046_s15_nasa_hdbk_7005_randomvib",
    "049_s18_easa_csvla_glider_spar",
    "055_s24_cmh17_cfrp_rotor_blade",
    "222_df6_aiaa_uas",
    "224_df8_vfs_dbvf",
    "242_a2_irec_copv_nitrogen_tank",
    "064_jp9_aij_structural_design",
    "232_df16_onr_neec",
    # T2 (5-7 parts)
    "058_jp3_noshiro_cansat_arliss",
    "208_oc8_ekka_farm_innovation",
    "147_hv23_ghc_iitm_hyperloop_pod_frame",
    "350_i_eu13_igvc_2026_frame",
    "057_jp2_kosen_dezacon_structural",
    "095_ng10_cawst_ferrocement_tank",
    "230_df14_auvsi_robosub",
    "085_kr16_drone_soccer",
    "207_oc7_anu_cubesat",
    "219_df3_aiaa_space_lunar_base",
    # T3 (8+ parts)
    "067_jp12_jaxa_innovative_satellite",
    "333_i_eu1_esa_rexus_experiment_module",
    "094_ng9_ose_ceb_liberator_press",
    "214_af2_sasol_solar_challenge",
    "081_kr6_ksae_baja_fsae",
    "256_a16_aisc_ssbc_steel_bridge",
    "325_i_me5_teknofest_auv_pressure_hull",
    "291_i_ea6_robomaster_rmuc_2025_17mm_launcher",
    "201_oc1_arch_rover",
    "246_a6_cubesat_1u_chassis",
]

PROMPT_TEMPLATE = """You are producing a single STEP (AP242) CAD file from the engineering design brief at the bottom of this message.

Your task:
1. Read the brief carefully. Identify required geometry, dimensions, and (for multi-part assemblies) each part family.
2. Write a Python script `build.py` in the current directory that uses **cadquery** (preferred; fall back to **build123d** only if cadquery cannot express the geometry) to construct the geometry, export it to `out.step`, and write `meta.json`.
3. Run `build.py` with `python build.py`. Confirm `out.step` exists and is non-empty (> 1 KB), and confirm `meta.json` exists and is valid JSON.
4. If the export fails, debug and retry. If a constraint in the brief is genuinely impossible to model in plain cadquery (e.g. requires explicit topology operations not supported), make the closest reasonable approximation and note the deviation in `notes.md`.

Constraints:
- Do NOT read, list, or otherwise touch any file outside this working directory. The brief below is fully self-contained — you do not need any other context.
- Do NOT run any FEA, meshing, or analysis. Only produce the STEP file.
- Do NOT install heavy non-CAD packages. cadquery and build123d are already available in the environment.
- Use realistic dimensions per the brief. Where the brief lists multiple alternatives, pick one reasonable design point and document the choice in `notes.md`.
- For multi-part assemblies, export ALL parts into a single STEP file (`cq.exporters.export(assy, 'out.step')` for cadquery Assemblies, or equivalent in build123d).
- Think hard about dimensions and units before writing code.

Deliverables in this directory:
- `build.py` — the CAD script
- `out.step` — the resulting STEP file
- `meta.json` — FEA runner metadata emitted by `build.py`
- `notes.md` — short notes on design choices, deviations, and any constraints you couldn't satisfy

== FEA EVAL METADATA CONTRACT ==
{fea_contract}

== DESIGN BRIEF ==
{short_prompt}
"""


def load_short_prompt(src_dir: Path, sid: str) -> str:
    fp = src_dir / f"{sid}.json"
    with fp.open() as f:
        d = json.load(f)
    return d.get("short_prompt") or d.get("full_prompt") or ""


def all_ccx_ids(set_name: str) -> list[str]:
    base = CCX_SINGLE_DIR if set_name == "single" else CCX_MULTI_DIR
    return sorted(d.name for d in base.iterdir() if d.is_dir())


def item_ids(set_name: str, scope: str) -> list[str]:
    if scope == "full":
        return all_ccx_ids(set_name)
    return CURATED_20_SINGLE if set_name == "single" else CURATED_30_CCX_MULTI


def generated_ids_from(run_dirs: list[str], set_name: str) -> set[str]:
    out: set[str] = set()
    for raw in run_dirs:
        base = Path(raw).expanduser().resolve() / set_name
        if not base.exists():
            continue
        for case_dir in base.iterdir():
            step = case_dir / "out.step"
            if (
                case_dir.is_dir()
                and (case_dir / "build.py").exists()
                and (case_dir / "meta.json").exists()
                and step.exists()
                and step.stat().st_size > 1024
            ):
                out.add(case_dir.name)
    return out


def eval_case_dir(set_name: str, sid: str) -> Path:
    return (CCX_SINGLE_DIR if set_name == "single" else CCX_MULTI_DIR) / sid


def _card_name(line: str) -> str:
    return line.split(",", 1)[0].strip().upper()


def _csv_parts(line: str) -> list[str]:
    return [part.strip() for part in line.split(",") if part.strip()]


def extract_eval_metadata_contract(template_path: Path) -> dict[str, list[str] | bool]:
    selectors: list[str] = []
    pressure_surfaces: list[str] = []
    current_card = ""

    def add_unique(items: list[str], value: str) -> None:
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", value or ""):
            return
        if value.upper() not in {"EALL", "URM", "HOUSING"} and value not in items:
            items.append(value)

    text = template_path.read_text()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("**"):
            continue
        if line.startswith("*"):
            current_card = _card_name(line)
            if current_card != "*NODE":
                for match in re.finditer(r"\bNSET\s*=\s*([A-Za-z_][A-Za-z0-9_]*)", line, re.I):
                    add_unique(selectors, match.group(1))
            continue

        parts = _csv_parts(line)
        if not parts:
            continue
        name = parts[0]
        if current_card in {"*BOUNDARY", "*CLOAD", "*INITIAL CONDITIONS"}:
            add_unique(selectors, name)
        elif current_card in {"*DLOAD", "*DSLOAD"} and len(parts) >= 2:
            if parts[1].upper() == "P" and name.upper().startswith(("S", "SURF")):
                add_unique(pressure_surfaces, name)
            else:
                add_unique(selectors, name)

    return {
        "selectors": selectors,
        "pressure_surfaces": pressure_surfaces,
        "uses_material_placeholder": "__MATERIAL__" in text,
        "uses_jobname_placeholder": "__JOBNAME__" in text,
    }


def render_eval_contract(set_name: str, sid: str) -> str:
    template_path = eval_case_dir(set_name, sid) / "analysis_template.inp"
    if not template_path.exists():
        return (
            "No CCX analysis_template.inp was found for this item. Still write "
            "`meta.json` with at least a `selectors` object whose names match "
            "the load/support regions you describe in `notes.md`."
        )

    contract = extract_eval_metadata_contract(template_path)
    selectors = contract["selectors"]
    pressure_surfaces = contract["pressure_surfaces"]
    material_note = (
        "- Include `\"material\"` in meta.json because the CCX template contains `__MATERIAL__`.\n"
        if contract["uses_material_placeholder"]
        else "- Include `\"material\"` in meta.json as a descriptive material identifier.\n"
    )
    jobname_note = (
        "- Include `\"jobname\": \"model\"` because the CCX template contains `__JOBNAME__`.\n"
        if contract["uses_jobname_placeholder"]
        else "- Include `\"jobname\": \"model\"`.\n"
    )
    surface_block = (
        "- Required `pressure_surfaces` names: "
        + ", ".join(f"`{name}`" for name in pressure_surfaces)
        + ". Each pressure surface selector must identify an exterior solid face so wire_bcs.py can emit an element-face *SURFACE.\n"
        if pressure_surfaces
        else "- No `pressure_surfaces` entries are required for this case.\n"
    )

    return f"""The downstream runner is `scripts/ccx_eval/grade_ccx.py`. It copies/reruns
`build.py` in a clean workdir, so `build.py` itself must write `meta.json`
every time it runs. A one-off manually-created `meta.json` is not sufficient.

Required `meta.json` contract for `{set_name}/{sid}`:
{jobname_note}{material_note}- Required `selectors` names: {", ".join(f"`{name}`" for name in selectors) if selectors else "`NALL`"}.
{surface_block}
Allowed selector forms are exactly those supported by `wire_bcs.py`:
- `{{"face": "x_min"|"x_max"|"y_min"|"y_max"|"z_min"|"z_max", "tol_mm": <positive number>}}`
- `{{"face_eq": "x"|"y"|"z", "value": <number>, "tol_mm": <positive number>}}`
- `{{"box": [xmin, ymin, zmin, xmax, ymax, zmax]}}`
- `{{"sphere": [cx, cy, cz, r]}}`
- `{{"radius_xy": <radius>, "tol_mm": <positive number>, "z_range": [zmin, zmax]}}`
- `{{"sphere_shell": [cx, cy, cz, r], "tol_mm": <positive number>}}`
- `{{"any_of": [<selector>, ...]}}`
- `{{"all": true}}`

Choose a clear coordinate convention in `build.py`, make every required selector
match a non-empty face/region after meshing, and explain the selector mapping in
`notes.md`. Use stable face or box selectors over tiny point selectors whenever
possible.

For requirements that are not directly recoverable from CCX output or mesh
volume, also write numeric values under `meta.json["engineering_metrics"]`
using the spec metric names, for example `{{"wheelbase_mm": 1600.0}}`.
Only put values there that `build.py` computes from the generated geometry or
explicitly chosen design parameters; the checker treats those as submitted
engineering report fields."""


def validate_generated_outputs(work: Path, set_name: str, sid: str) -> list[str]:
    errors: list[str] = []
    step = work / "out.step"
    build = work / "build.py"
    meta_path = work / "meta.json"

    if not build.exists():
        errors.append("missing build.py")
    elif "meta.json" not in build.read_text(errors="ignore"):
        errors.append("build.py does not appear to write meta.json")

    if not step.exists() or step.stat().st_size <= 1024:
        errors.append("out.step missing or <= 1 KB")

    contract_path = eval_case_dir(set_name, sid) / "analysis_template.inp"
    contract = (
        extract_eval_metadata_contract(contract_path)
        if contract_path.exists()
        else {"selectors": [], "pressure_surfaces": []}
    )

    if not meta_path.exists():
        errors.append("meta.json missing")
        return errors

    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError as exc:
        errors.append(f"meta.json is invalid JSON: {exc}")
        return errors

    selectors = meta.get("selectors")
    if not isinstance(selectors, dict) or not selectors:
        errors.append("meta.json has no non-empty selectors object")
    else:
        for name in contract["selectors"]:
            if name not in selectors:
                errors.append(f"meta.json selectors missing required `{name}`")

    pressure_surfaces = meta.get("pressure_surfaces") or {}
    if not isinstance(pressure_surfaces, dict):
        errors.append("meta.json pressure_surfaces must be an object when present")
    else:
        for name in contract["pressure_surfaces"]:
            if name not in pressure_surfaces:
                errors.append(f"meta.json pressure_surfaces missing required `{name}`")

    return errors


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _repo_deny_entries_for(path: Path) -> list[str]:
    raw = str(path.resolve())
    return [
        f"Read({raw}/**)",
        f"Write({raw}/**)",
        f"Edit({raw}/**)",
        f"Bash(cat {raw}*)",
        f"Bash(less {raw}*)",
        f"Bash(head {raw}*)",
        f"Bash(tail {raw}*)",
        f"Bash(grep * {raw}*)",
        f"Bash(rg * {raw}*)",
        f"Bash(find {raw}*)",
        f"Bash(ls {raw}*)",
        f"Bash(cd {raw}*)",
    ]


def write_sandbox_settings(work: Path) -> None:
    """Write a per-item .claude/settings.local.json for repo isolation.

    If the run directory is outside the repo, deny the whole repo. If the run
    directory is under the repo, avoid a broad deny that blocks Claude from
    writing its own deliverables and instead deny known source/eval roots.
    """
    settings_dir = work / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    repo = REPO_ROOT.resolve()
    work_resolved = work.resolve()

    if _is_within(work_resolved, repo):
        deny: list[str] = []
        for name in (
            ".git",
            ".github",
            ".codex",
            "configs",
            "docs",
            "scripts",
            "src",
            "tests",
            "tools",
        ):
            candidate = repo / name
            if candidate.exists() and not _is_within(work_resolved, candidate):
                deny.extend(_repo_deny_entries_for(candidate))
    else:
        deny = _repo_deny_entries_for(repo)

    settings = {
        "permissions": {
            "deny": deny,
        },
    }
    (settings_dir / "settings.local.json").write_text(json.dumps(settings, indent=2))


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()


def run_claude_until_done(cmd: list[str], work: Path, log_fp, timeout_s: int,
                          set_name: str, sid: str) -> tuple[str, int | None, str]:
    """Run Claude, but stop once valid deliverables are stable on disk."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(work),
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    start = time.time()
    last_step_size = -1
    stable_since: float | None = None

    while True:
        rc = proc.poll()
        if rc is not None:
            return ("ok" if rc == 0 else f"exit_{rc}", rc, "process_exit")

        if time.time() - start > timeout_s:
            _terminate_process_group(proc)
            try:
                rc = proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait()
            return ("timeout", rc, "timeout")

        step = work / "out.step"
        step_size = step.stat().st_size if step.exists() else 0
        if step_size > 1024 and step_size == last_step_size:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= 20:
                if not validate_generated_outputs(work, set_name, sid):
                    _terminate_process_group(proc)
                    try:
                        rc = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        rc = proc.wait()
                    return ("ok", rc, "valid_deliverables")
        else:
            stable_since = None
            last_step_size = step_size

        time.sleep(5)


def run_one(set_name: str, sid: str, src_dir: Path, out_root: Path,
            model: str, effort: str, timeout_s: int) -> dict:
    work = out_root / set_name / sid
    work.mkdir(parents=True, exist_ok=True)
    write_sandbox_settings(work)
    started_at = iso_now()
    started = time.time()
    short = load_short_prompt(src_dir, sid)
    if not short:
        ended_at = iso_now()
        result = {
            "set": set_name, "id": sid, "status": "no_prompt", "step_size": 0,
            "model": model, "effort": effort,
            "started_at": started_at, "ended_at": ended_at,
            "elapsed_s": round(time.time() - started, 1),
            "work": str(work), "validation_errors": [],
        }
        (work / "result.json").write_text(json.dumps(result, indent=2))
        return result

    prompt = PROMPT_TEMPLATE.format(
        short_prompt=short,
        fea_contract=render_eval_contract(set_name, sid),
    )
    (work / "prompt.md").write_text(prompt)

    cmd = [
        "claude",
        "-p", prompt,
        "--model", model,
        "--effort", effort,
        "--dangerously-skip-permissions",
    ]
    log_fp = (work / "run.log").open("w")
    rc = None
    completion_reason = "unknown"
    try:
        status, rc, completion_reason = run_claude_until_done(
            cmd, work, log_fp, timeout_s, set_name, sid
        )
    finally:
        log_fp.close()

    step = work / "out.step"
    step_size = step.stat().st_size if step.exists() else 0
    validation_errors = validate_generated_outputs(work, set_name, sid)
    if status == "ok" and validation_errors:
        status = "invalid_output"
    elapsed = time.time() - started
    ended_at = iso_now()
    result = {
        "set": set_name, "id": sid, "status": status, "rc": rc,
        "model": model, "effort": effort,
        "completion_reason": completion_reason,
        "started_at": started_at, "ended_at": ended_at,
        "step_size": step_size, "elapsed_s": round(elapsed, 1),
        "work": str(work), "validation_errors": validation_errors,
    }
    (work / "result.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-root",
                    default=str(Path.home() / "hephaestus_step_runs"),
                    help="parent dir for per-run timestamped output. Default "
                         "is OUTSIDE the repo so spawned `claude` processes "
                         "don't inherit Hephaestus's CLAUDE.md / settings.")
    ap.add_argument("--model", default="claude-opus-4-7",
                    help="claude model id (default: claude-opus-4-7)")
    ap.add_argument("--effort", default="max",
                    choices=["low", "medium", "high", "xhigh", "max"],
                    help="Claude Code effort level (default: max)")
    ap.add_argument("--timeout", type=int, default=2400,
                    help="per-item timeout in seconds (default: 2400 = 40 min)")
    ap.add_argument("--jobs", type=int, default=10,
                    help="concurrent claude processes (default: 10). Each "
                         "process is a full Opus 4.7 + extended-thinking "
                         "session — watch your rate limits.")
    ap.add_argument("--set", choices=["single", "multi", "all"], default="all",
                    help="which curated set to run")
    ap.add_argument("--scope", choices=["curated", "full"], default="curated",
                    help="curated=20 single/30 multi; full=all ccx_50 dirs")
    ap.add_argument("--only", nargs="*", default=None,
                    help="optional subset of ids to run (matches both sets)")
    ap.add_argument("--exclude-generated-from", action="append", default=[],
                    help="skip ids with build.py+meta.json+out.step in this "
                         "generation timestamp dir; may be repeated")
    ap.add_argument("--dry-run", action="store_true",
                    help="print plan and exit; do not invoke claude")
    args = ap.parse_args()

    if not shutil.which("claude") and not args.dry_run:
        print("ERROR: `claude` CLI not on PATH", file=sys.stderr)
        return 2

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_root = Path(args.out_root) / ts
    out_root.mkdir(parents=True, exist_ok=True)

    items: list[tuple[str, str, Path]] = []
    if args.set in ("single", "all"):
        for sid in item_ids("single", args.scope):
            items.append(("single", sid, SAMPLES_DIR))
    if args.set in ("multi", "all"):
        for sid in item_ids("multi", args.scope):
            items.append(("multi", sid, MULTIPART_DIR))
    if args.only:
        items = [it for it in items if it[1] in args.only]
    if args.exclude_generated_from:
        skip_by_set = {
            "single": generated_ids_from(args.exclude_generated_from, "single"),
            "multi": generated_ids_from(args.exclude_generated_from, "multi"),
        }
        items = [it for it in items if it[1] not in skip_by_set[it[0]]]

    print(f"Plan: {len(items)} items, model={args.model} (effort={args.effort}), "
          f"jobs={args.jobs}, timeout={args.timeout}s, scope={args.scope}, out={out_root}")
    for set_name, sid, _ in items:
        print(f"  [{set_name}] {sid}")
    if args.dry_run:
        return 0

    run_started_at = iso_now()
    run_started = time.time()
    metadata_fp = out_root / "run_metadata.json"
    metadata = {
        "model": args.model,
        "effort": args.effort,
        "jobs": args.jobs,
        "timeout_s": args.timeout,
        "scope": args.scope,
        "set": args.set,
        "out_root": str(out_root),
        "status": "running",
        "started_at": run_started_at,
        "items": [
            {"set": set_name, "id": sid}
            for set_name, sid, _ in items
        ],
    }
    metadata_fp.write_text(json.dumps(metadata, indent=2))

    results: list[dict] = []
    partial_summary_fp = out_root / "partial_summary.json"
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {
            ex.submit(run_one, set_name, sid, src, out_root,
                      args.model, args.effort, args.timeout): sid
            for set_name, sid, src in items
        }
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            partial_summary_fp.write_text(json.dumps(results, indent=2))
            kb = r["step_size"] // 1024
            print(f"  [{r['set']:<6}] {r['id']:<55} {r['status']:<10} "
                  f"step={kb:>5} KB  elapsed={r.get('elapsed_s', 0):>6.1f}s")

    summary_fp = out_root / "summary.json"
    summary_fp.write_text(json.dumps(results, indent=2))
    n_ok = sum(1 for r in results if r["status"] == "ok" and r["step_size"] > 1024)
    run_elapsed = time.time() - run_started
    metadata.update({
        "status": "complete" if n_ok == len(results) else "incomplete",
        "ended_at": iso_now(),
        "elapsed_s": round(run_elapsed, 1),
        "ok_count": n_ok,
        "total": len(results),
        "summary": str(summary_fp),
    })
    metadata_fp.write_text(json.dumps(metadata, indent=2))
    print(f"\nDone. {n_ok}/{len(results)} produced a STEP > 1 KB.")
    print(f"Elapsed: {run_elapsed:.1f}s")
    print(f"Summary: {summary_fp}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
