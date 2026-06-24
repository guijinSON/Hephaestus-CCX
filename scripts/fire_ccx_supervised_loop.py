#!/usr/bin/env python3
"""Supervised CCX repair loop for Codex/Claude/OpenCode STEP generation.

The model only generates or repairs CAD artifacts in an isolated attempt
directory. The harness runs canonical CCX evaluation separately, summarizes the
result, and feeds concise feedback into the next attempt.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
ASSET_ROOT = REPO_ROOT / "agent_assets" / "ccx_loop"
DEFAULT_OUT_ROOT = Path.home() / "hephaestus_ccx_loop_runs"
DEFAULT_MODEL = {
    "codex": "gpt-5.4",
    "claude": "claude-opus-4-7",
    "opencode": "opencode-go/deepseek-v4-pro",
}
TRANSIENT_MODEL_ERROR_MARKERS = (
    "UNKNOWN_CERTIFICATE_VERIFICATION_ERROR",
    "unknown certificate verification error",
    "certificate verification",
    "fetch failed",
)
TRANSIENT_MODEL_ERROR_CODE_RE = re.compile(r"\b(ECONNRESET|ETIMEDOUT|EAI_AGAIN|ENOTFOUND)\b")

sys.path.insert(0, str(SCRIPTS_DIR))

from eval_codex_ccx_runs import (  # noqa: E402
    PER_CHECK_CSV_COLUMNS,
    eval_one,
    flatten_per_requirement_results,
    selected_ids,
    write_per_check_outputs,
)
from fire_codex_step_50 import (  # noqa: E402
    MULTIPART_DIR,
    SAMPLES_DIR,
    find_codex_cli,
    load_short_prompt,
    render_eval_contract,
    validate_generated_outputs,
)


INITIAL_PROMPT_TEMPLATE = """You are producing a single STEP (AP242) CAD file from the engineering design brief at the bottom of this message.

Work only inside this attempt directory. Do not read, list, or inspect files outside it.
Do not look for the canonical eval files, check.py, analysis templates, raw CCX logs, or repository source files.

Your task:
1. Read the brief carefully. Identify required geometry, dimensions, load/support regions, and part families.
2. Write `build.py` using cadquery if possible, falling back to build123d only if needed.
3. `build.py` must export `out.step` and write `meta.json` every time it runs.
4. Run `python build.py` and confirm `out.step` is larger than 1 KB and `meta.json` is valid JSON.
5. Write `notes.md` explaining design choices, approximations, and selector mapping.

{workflow_instructions}

{rich_view_instructions}

== ACTIVE NATIVE SKILL REFERENCE ==
{skill_text}

== FEA EVAL METADATA CONTRACT ==
{fea_contract}

== DESIGN BRIEF ==
{short_prompt}
"""


REPAIR_PROMPT_TEMPLATE = """You are repairing a previous CAD submission after canonical CCX evaluation.

Work only inside this attempt directory. Do not read, list, or inspect files outside it.
Do not look for the canonical eval files, check.py, analysis templates, raw CCX logs, or repository source files.

The prior attempt's `build.py`, `notes.md` when available, and prior `meta.json` as `prior_meta.json` have been copied into this directory. Use the feedback below as the source of truth. Preserve requirements that already passed unless fixing a failure requires a coordinated geometry change.

Your task:
1. Edit or rewrite `build.py` to address the CCX feedback.
2. Ensure `build.py` writes fresh `out.step` and fresh `meta.json` every time it runs.
3. Run `python build.py`.
4. Update `notes.md` with what changed and how selectors map to load/support regions.

{workflow_instructions}

{rich_view_instructions}

== ACTIVE NATIVE SKILL REFERENCE ==
{skill_text}

== FEA EVAL METADATA CONTRACT ==
{fea_contract}

== CCX FEEDBACK ==
{feedback}

== PRIOR BUILD.PY EXCERPT ==
{prior_build}

== PRIOR NOTES EXCERPT ==
{prior_notes}

== PRIOR DESIGN BRIEF EXCERPT ==
{prior_design_brief}

== PRIOR BLUEPRINT EXCERPT ==
{prior_blueprint}

== PRIOR META.JSON EXCERPT ==
{prior_meta}
"""


@dataclass(frozen=True)
class LoopConfig:
    backend: str
    model: str
    reasoning: str
    effort: str
    codex_bin: str | None
    codex_sandbox: str
    claude_bin: str | None
    opencode_bin: str | None
    claude_skip_permissions: bool
    opencode_skip_permissions: bool
    max_attempts: int
    timeout_model: int
    timeout_eval: float | None
    model_launch_retries: int
    skill_mode: str
    feedback_mode: str
    require_rich_view: bool
    cleanup_render_images: bool
    run_root: Path
    isolation_mode: str


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def isolation_mode_for(out_root: Path) -> str:
    return "nonisolated_debug" if _is_within(out_root, REPO_ROOT) else "isolated_external"


def timestamped_run_root(out_root: Path) -> Path:
    return out_root.expanduser().resolve() / datetime.now().strftime("%Y%m%d_%H%M%S")


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def active_skill_names(skill_mode: str) -> list[str]:
    if skill_mode == "none":
        return []
    if skill_mode == "cad":
        return ["ccx_cad_repair"]
    if skill_mode == "blueprint":
        return ["ccx_blueprint_generation"]
    if skill_mode == "all":
        return ["ccx_cad_repair", "ccx_blueprint_generation"]
    raise ValueError(f"unknown skill mode: {skill_mode}")


def load_skill_text(skill_mode: str) -> str:
    sections: list[str] = []
    for name in active_skill_names(skill_mode):
        path = ASSET_ROOT / "skills" / name / "SKILL.md"
        sections.append(path.read_text(encoding="utf-8"))
    if skill_mode == "all":
        sections.append(
            "Rich-view tool available for future agent-tool mode: "
            "`python tools/render_richview_cli.py --step-path out.step --output-dir render_sets "
            "--prefix attempt --target-type part --target-id <case_id> --attempt <n>`."
        )
    return "\n\n---\n\n".join(sections) if sections else "(no native skill text loaded)"


def copy_native_assets(
    attempt_dir: Path,
    *,
    backend: str,
    skill_mode: str,
    run_root: Path,
    isolation_mode: str,
    claude_permission_mode: str | None = None,
    opencode_permission_mode: str | None = None,
    include_rich_view_tool: bool = True,
) -> dict[str, Any]:
    skills_dir = attempt_dir / "skills"
    tools_dir = attempt_dir / "tools"
    skills_dir.mkdir(parents=True, exist_ok=True)

    loaded_skills: list[dict[str, str]] = []
    for name in active_skill_names(skill_mode):
        src_dir = ASSET_ROOT / "skills" / name
        dst_dir = skills_dir / name
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        shutil.copytree(src_dir, dst_dir)
        skill_path = dst_dir / "SKILL.md"
        loaded_skills.append({"name": name, "sha256": sha256_file(skill_path)})

    loaded_tools: list[dict[str, str]] = []
    if include_rich_view_tool:
        tools_dir.mkdir(parents=True, exist_ok=True)
        tool_src = ASSET_ROOT / "tools" / "render_richview_cli.py"
        tool_dst = tools_dir / "render_richview_cli.py"
        shutil.copy2(tool_src, tool_dst)
        loaded_tools.append({"name": "render_richview_cli.py", "sha256": sha256_file(tool_dst)})

    active = "\n".join(f"- `{entry['name']}`" for entry in loaded_skills) or "- none"
    if backend in {"codex", "opencode"}:
        template_backend = "codex"
        template_name = "AGENTS.template.md"
        target_name = "AGENTS.md"
    elif backend == "claude":
        template_backend = "claude"
        template_name = "CLAUDE.template.md"
        target_name = "CLAUDE.md"
    else:
        raise ValueError(f"unsupported backend: {backend}")
    template = (ASSET_ROOT / template_backend / template_name).read_text(encoding="utf-8")
    (attempt_dir / target_name).write_text(template.format(active_skills=active), encoding="utf-8")

    manifest = {
        "backend": backend,
        "skill_mode": skill_mode,
        "loaded_skills": loaded_skills,
        "tools": loaded_tools,
        "repo_root": str(REPO_ROOT),
        "run_root": str(run_root),
        "isolation_mode": isolation_mode,
        "claude_permission_mode": claude_permission_mode,
        "opencode_permission_mode": opencode_permission_mode,
        "created_at": iso_now(),
    }
    (attempt_dir / "agent_asset_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def deny_entries_for(path: Path) -> list[str]:
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


def write_claude_settings(attempt_dir: Path, *, run_root: Path, isolation_mode: str) -> Path:
    deny: list[str] = []
    repo = REPO_ROOT.resolve()
    attempt = attempt_dir.resolve()
    if _is_within(attempt, repo):
        for name in (".git", ".github", ".codex", "configs", "docs", "scripts", "tests", "tools", "agent_assets"):
            candidate = repo / name
            if candidate.exists() and not _is_within(attempt, candidate):
                deny.extend(deny_entries_for(candidate))
    else:
        deny.extend(deny_entries_for(repo))
    deny.extend(deny_entries_for(run_root / "evals"))

    settings = {
        "permissions": {
            "deny": deny,
        },
        "ccx_loop": {
            "isolation_mode": isolation_mode,
            "attempt_dir": str(attempt),
        },
    }
    settings_dir = attempt_dir / ".claude"
    settings_dir.mkdir(parents=True, exist_ok=True)
    path = settings_dir / "settings.local.json"
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def resolve_claude_cli(explicit: str | None = None) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        return explicit
    return shutil.which("claude")


def resolve_opencode_cli(explicit: str | None = None) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
        return explicit
    return shutil.which("opencode")


def read_text_capped(path: Path, max_chars: int) -> str:
    if not path.exists():
        return "(missing)"
    text = path.read_text(errors="ignore")
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n[truncated to {max_chars} chars]"


def src_dir_for_set(set_name: str) -> Path:
    return SAMPLES_DIR if set_name == "single" else MULTIPART_DIR


def workflow_instructions(skill_mode: str, *, repair: bool) -> str:
    if "ccx_blueprint_generation" not in active_skill_names(skill_mode):
        return ""
    if repair:
        return """Blueprint-first H-CCX repair workflow:
1. Before changing CAD, update `design_brief.md` and `blueprint.yaml` from the CCX feedback and any prior rich-view observations recorded in `notes.md`.
2. Treat the revised `blueprint.yaml` as the explicit source of truth for construction units, load paths, support/load selectors, materials, and engineering checks.
3. If FEA feedback reports stress, displacement, buckling, modal, mass, selector, or support/load failures, encode the intended fix in the blueprint first, then implement it in CAD.
4. If rich-view feedback shows missing bodies, bad proportions, hidden intersections, poor clearances, or mismatched load/support geometry, update the blueprint envelopes, construction units, interfaces, or selectors before editing CAD.
5. Only then repair `build.py`, `out.step`, and `meta.json` from the updated blueprint.
6. In `notes.md`, include a short "Blueprint to H-CCX handoff" section naming which blueprint parts/selectors became the CCX load/support regions and a short "Blueprint revision" section listing which FEA/rich-view findings changed the blueprint."""
    return """Blueprint-first H-CCX workflow:
1. First write `design_brief.md`, summarizing requirements, assumptions, load paths, and verification targets from the prompt.
2. Then write `blueprint.yaml` before CAD. Use `assembly_schema_version: 4`, filename-safe part names, construction units, materials, load/support zones, selectors, and engineering checks.
3. Treat `blueprint.yaml` as the source of truth for CAD. Only after it exists, write `build.py`, generate `out.step`, and write `meta.json`.
4. In `notes.md`, include a short "Blueprint to H-CCX handoff" section explaining how blueprint parts/selectors map into the CCX harness."""


def rich_view_instructions(*, enabled: bool, case_id: str, attempt_index: int) -> str:
    if not enabled:
        return ""
    return f"""Rich-view self-check:
1. After `python build.py` produces `out.step`, run the local rich-view renderer:
   `python tools/render_richview_cli.py --step-path out.step --output-dir render_sets --prefix attempt --target-type part --target-id {case_id} --attempt {attempt_index}`.
2. Inspect the generated `render_sets/**/view_*.png` images for missing bodies, broken topology, implausible proportions, hidden intersections, and selector/load/support regions that do not correspond to real exterior geometry.
3. When `blueprint.yaml` exists, translate the visual findings into blueprint changes first: update envelopes, construction units, interface features, selector names, or acceptance claims before changing CAD.
4. Use the updated blueprint to revise `build.py`, regenerate `out.step` and `meta.json`, and rerun the rich-view check when it changes the design.
5. In `notes.md`, include a short "Rich-view check" section with the render manifest path, visual issues found, which blueprint entries changed, and what CAD changed. If rich-view fails for tooling reasons, record the failure and continue with CAD/metadata repair."""


def build_initial_prompt(
    set_name: str,
    case_id: str,
    skill_mode: str,
    *,
    require_rich_view: bool = False,
    attempt_index: int = 0,
) -> str:
    short_prompt = load_short_prompt(src_dir_for_set(set_name), case_id)
    return INITIAL_PROMPT_TEMPLATE.format(
        workflow_instructions=workflow_instructions(skill_mode, repair=False),
        rich_view_instructions=rich_view_instructions(
            enabled=require_rich_view,
            case_id=case_id,
            attempt_index=attempt_index,
        ),
        skill_text=load_skill_text(skill_mode),
        fea_contract=render_eval_contract(set_name, case_id),
        short_prompt=short_prompt,
    )


def build_repair_prompt(
    set_name: str,
    case_id: str,
    skill_mode: str,
    attempt_dir: Path,
    feedback: str,
    *,
    require_rich_view: bool = False,
    attempt_index: int = 0,
) -> str:
    return REPAIR_PROMPT_TEMPLATE.format(
        workflow_instructions=workflow_instructions(skill_mode, repair=True),
        rich_view_instructions=rich_view_instructions(
            enabled=require_rich_view,
            case_id=case_id,
            attempt_index=attempt_index,
        ),
        skill_text=load_skill_text(skill_mode),
        fea_contract=render_eval_contract(set_name, case_id),
        feedback=feedback,
        prior_build=read_text_capped(attempt_dir / "build.py", 60000),
        prior_notes=read_text_capped(attempt_dir / "notes.md", 10000),
        prior_design_brief=read_text_capped(attempt_dir / "design_brief.md", 20000),
        prior_blueprint=read_text_capped(attempt_dir / "blueprint.yaml", 40000),
        prior_meta=read_text_capped(attempt_dir / "prior_meta.json", 10000),
    )


def build_codex_command(
    *,
    codex_bin: str,
    model: str,
    reasoning: str,
    attempt_dir: Path,
    sandbox: str,
) -> list[str]:
    return [
        codex_bin,
        "exec",
        "--model",
        model,
        "-c",
        f"model_reasoning_effort={reasoning}",
        "-c",
        "approval_policy=never",
        "--sandbox",
        sandbox,
        "--skip-git-repo-check",
        "--ephemeral",
        "--cd",
        str(attempt_dir),
        "--output-last-message",
        str(attempt_dir / "final_message.txt"),
        "-",
    ]


def build_claude_command(
    *,
    claude_bin: str,
    prompt: str,
    model: str,
    effort: str,
    skip_permissions: bool,
) -> list[str]:
    cmd = [claude_bin, "-p", prompt, "--model", model, "--effort", effort]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    return cmd


def build_opencode_command(
    *,
    opencode_bin: str,
    model: str,
    effort: str,
    attempt_dir: Path,
    skip_permissions: bool,
) -> list[str]:
    prompt_path = attempt_dir / "prompt.md"
    cmd = [
        opencode_bin,
        "--pure",
        "run",
        "--model",
        model,
        "--variant",
        effort,
        "--dir",
        str(attempt_dir),
        "--file",
        str(prompt_path),
    ]
    if skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append("Use the attached prompt.md as the complete task prompt. Follow it exactly.")
    return cmd


def terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        proc.terminate()


def run_subprocess_with_prompt(cmd: list[str], attempt_dir: Path, prompt: str, timeout_s: int) -> tuple[str, int | None]:
    env = os.environ.copy()
    env.setdefault("HEPHAESTUS_REPO_ROOT", str(REPO_ROOT))
    with (attempt_dir / "run.log").open("w") as log_fp:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(attempt_dir),
                env=env,
                input=prompt if cmd[-1] == "-" else None,
                text=True,
                stdout=log_fp,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
            return ("ok" if proc.returncode == 0 else f"exit_{proc.returncode}", proc.returncode)
        except subprocess.TimeoutExpired:
            return "timeout", None


def run_cli_until_done(cmd: list[str], attempt_dir: Path, timeout_s: int, set_name: str, case_id: str) -> tuple[str, int | None, str]:
    env = os.environ.copy()
    env.setdefault("HEPHAESTUS_REPO_ROOT", str(REPO_ROOT))
    with (attempt_dir / "run.log").open("w") as log_fp:
        proc = subprocess.Popen(
            cmd,
            cwd=str(attempt_dir),
            env=env,
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        start = time.time()
        stable_since: float | None = None
        last_step_size = -1
        while True:
            rc = proc.poll()
            if rc is not None:
                return ("ok" if rc == 0 else f"exit_{rc}", rc, "process_exit")
            if time.time() - start > timeout_s:
                terminate_process_group(proc)
                try:
                    rc = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.wait()
                return "timeout", rc, "timeout"

            step = attempt_dir / "out.step"
            size = step.stat().st_size if step.exists() else 0
            if size > 1024 and size == last_step_size:
                if stable_since is None:
                    stable_since = time.time()
                elif time.time() - stable_since >= 20:
                    if not validate_generated_outputs(attempt_dir, set_name, case_id):
                        terminate_process_group(proc)
                        try:
                            rc = proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            rc = proc.wait()
                        return "ok", rc, "valid_deliverables"
            else:
                stable_since = None
                last_step_size = size
            time.sleep(5)


def run_claude_until_done(cmd: list[str], attempt_dir: Path, timeout_s: int, set_name: str, case_id: str) -> tuple[str, int | None, str]:
    return run_cli_until_done(cmd, attempt_dir, timeout_s, set_name, case_id)


def run_log_has_transient_model_error(attempt_dir: Path) -> bool:
    path = attempt_dir / "run.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    folded = text.lower()
    return (
        any(marker.lower() in folded for marker in TRANSIENT_MODEL_ERROR_MARKERS)
        or TRANSIENT_MODEL_ERROR_CODE_RE.search(text) is not None
    )


def archive_retry_run_log(attempt_dir: Path, launch_index: int) -> str | None:
    path = attempt_dir / "run.log"
    if not path.exists():
        return None
    archive = attempt_dir / f"run.launch_retry_{launch_index:02d}.log"
    try:
        shutil.move(str(path), str(archive))
    except OSError:
        return None
    return archive.name


def run_model_attempt(config: LoopConfig, set_name: str, case_id: str, attempt_dir: Path, prompt: str) -> dict[str, Any]:
    (attempt_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    started = time.time()
    validation_errors: list[str] = []
    launch_attempts: list[dict[str, Any]] = []
    if config.backend == "codex":
        if not config.codex_bin:
            return {"status": "missing_codex_cli", "rc": None, "elapsed_s": 0.0}
        cmd = build_codex_command(
            codex_bin=config.codex_bin,
            model=config.model,
            reasoning=config.reasoning,
            attempt_dir=attempt_dir,
            sandbox=config.codex_sandbox,
        )
        status, rc = run_subprocess_with_prompt(cmd, attempt_dir, prompt, config.timeout_model)
        completion_reason = "process_exit"
        validation_errors = validate_generated_outputs(attempt_dir, set_name, case_id)
    elif config.backend == "claude":
        if not config.claude_bin:
            return {"status": "missing_claude_cli", "rc": None, "elapsed_s": 0.0}
        cmd = build_claude_command(
            claude_bin=config.claude_bin,
            prompt=prompt,
            model=config.model,
            effort=config.effort,
            skip_permissions=config.claude_skip_permissions,
        )
        status, rc, completion_reason = run_claude_until_done(
            cmd,
            attempt_dir,
            config.timeout_model,
            set_name,
            case_id,
        )
        validation_errors = validate_generated_outputs(attempt_dir, set_name, case_id)
    elif config.backend == "opencode":
        if not config.opencode_bin:
            return {"status": "missing_opencode_cli", "rc": None, "elapsed_s": 0.0}
        cmd = build_opencode_command(
            opencode_bin=config.opencode_bin,
            model=config.model,
            effort=config.effort,
            attempt_dir=attempt_dir,
            skip_permissions=config.opencode_skip_permissions,
        )
        status = "not_started"
        rc = None
        completion_reason = "not_started"
        for launch_index in range(config.model_launch_retries + 1):
            if launch_index:
                time.sleep(min(30, 5 * launch_index))
            status, rc, completion_reason = run_cli_until_done(
                cmd,
                attempt_dir,
                config.timeout_model,
                set_name,
                case_id,
            )
            validation_errors = validate_generated_outputs(attempt_dir, set_name, case_id)
            transient_error = bool(validation_errors) and run_log_has_transient_model_error(attempt_dir)
            launch_record = {
                "launch_index": launch_index,
                "status": status,
                "rc": rc,
                "completion_reason": completion_reason,
                "validation_errors": validation_errors,
                "transient_model_error": transient_error,
            }
            if transient_error and launch_index < config.model_launch_retries:
                launch_record["archived_log"] = archive_retry_run_log(attempt_dir, launch_index)
                launch_attempts.append(launch_record)
                continue
            launch_attempts.append(launch_record)
            break
    else:
        return {"status": f"unsupported_backend_{config.backend}", "rc": None, "elapsed_s": 0.0}

    if status == "ok" and validation_errors:
        status = "invalid_output"
    return {
        "status": status,
        "rc": rc,
        "elapsed_s": round(time.time() - started, 2),
        "completion_reason": completion_reason,
        "validation_errors": validation_errors,
        "launch_attempt_count": len(launch_attempts) if launch_attempts else 1,
        "transient_model_retries": sum(1 for item in launch_attempts if item.get("archived_log")),
        "launch_attempts": launch_attempts,
        "command": cmd[:2] + ["..."],
    }


def cleanup_render_images(attempt_dir: Path) -> dict[str, Any]:
    started = time.time()
    removed: list[str] = []
    removed_bytes = 0
    for path in sorted((attempt_dir / "render_sets").glob("**/view_*.png")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError as exc:
            removed.append(f"ERROR {path.relative_to(attempt_dir)}: {exc}")
            continue
        removed_bytes += size
        removed.append(str(path.relative_to(attempt_dir)))
    record = {
        "removed_count": sum(1 for item in removed if not item.startswith("ERROR ")),
        "removed_bytes": removed_bytes,
        "elapsed_s": round(time.time() - started, 3),
        "removed_paths": removed,
    }
    (attempt_dir / "render_image_cleanup.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def prepare_attempt_dir(
    *,
    config: LoopConfig,
    set_name: str,
    case_id: str,
    attempt_index: int,
    previous_attempt_dir: Path | None,
    feedback: str | None,
) -> Path:
    attempt_dir = config.run_root / set_name / case_id / f"attempt_{attempt_index:02d}"
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    permission_mode = None
    if config.backend == "claude":
        permission_mode = "dangerously_skip_permissions" if config.claude_skip_permissions else "settings_enforced"
    opencode_permission_mode = None
    if config.backend == "opencode":
        opencode_permission_mode = (
            "dangerously_skip_permissions" if config.opencode_skip_permissions else "default_permissions"
        )
    copy_native_assets(
        attempt_dir,
        backend=config.backend,
        skill_mode=config.skill_mode,
        run_root=config.run_root,
        isolation_mode=config.isolation_mode,
        claude_permission_mode=permission_mode,
        opencode_permission_mode=opencode_permission_mode,
        include_rich_view_tool=(config.require_rich_view or config.skill_mode == "all"),
    )
    if config.backend == "claude":
        write_claude_settings(attempt_dir, run_root=config.run_root, isolation_mode=config.isolation_mode)

    if previous_attempt_dir:
        for name in ("build.py", "notes.md", "design_brief.md", "blueprint.yaml", "blueprint.yml", "blueprint.md"):
            src = previous_attempt_dir / name
            if src.exists():
                shutil.copy2(src, attempt_dir / name)
        prior_meta = previous_attempt_dir / "meta.json"
        if prior_meta.exists():
            shutil.copy2(prior_meta, attempt_dir / "prior_meta.json")
    if feedback:
        (attempt_dir / "ccx_feedback.md").write_text(feedback, encoding="utf-8")
    return attempt_dir


def strict_pass(result: dict[str, Any]) -> bool:
    if result.get("coverage_status") != "pass" or result.get("engineering_status") != "pass":
        return False
    rows = result.get("per_requirement_results") or []
    return all(str(row.get("verdict") or "").lower() == "pass" for row in rows)


NUMBER_RE = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?")


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = NUMBER_RE.search(value.replace(",", ""))
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def read_json_dict(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def flatten_numeric_paths(data: dict[str, Any]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []

    def walk(value: Any, path: list[str]) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, path + [str(key)])
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, path + [str(index)])
            return
        number = as_float(value)
        if number is not None and path:
            out.append((".".join(path), number))

    walk(data, [])
    return out


def row_tokens(row: dict[str, Any]) -> list[str]:
    raw = [
        str(row.get("requirement_id") or ""),
        str(row.get("metric") or ""),
        str(row.get("check_type") or ""),
        str(row.get("detail") or ""),
    ]
    tokens: list[str] = []
    for item in raw:
        for token in normalize_key(item).split("_"):
            if len(token) >= 3 and token not in {"fail", "pass", "source", "generic", "checker"}:
                tokens.append(token)
    return sorted(set(tokens))


def matching_meta_paths(meta: dict[str, Any], row: dict[str, Any], limit: int = 8) -> list[str]:
    tokens = row_tokens(row)
    if not tokens:
        return []
    matches: list[str] = []
    for path, value in flatten_numeric_paths(meta):
        key = normalize_key(path)
        if any(token in key for token in tokens):
            matches.append(f"{path}={value:g}")
        if len(matches) >= limit:
            break
    return matches


def suggested_meta_paths(row: dict[str, Any]) -> list[str]:
    rid = str(row.get("requirement_id") or "").strip()
    metric = normalize_key(str(row.get("metric") or "")).strip("_")
    limit = row.get("limit") if isinstance(row.get("limit"), dict) else {}
    limit_key = normalize_key(str(limit.get("key") or ""))
    suggestions: list[str] = []
    if rid:
        suggestions.append(f"declared_limits.{rid}")
    if metric:
        suggestions.append(f"engineering_metrics.{metric}")
        suggestions.append(f"declared_limits.{metric}")
    if metric and limit_key.startswith("limit_"):
        unit = limit_key.removeprefix("limit_")
        suggestions.append(f"engineering_metrics.{metric}_{unit}")
        suggestions.append(f"declared_limits.{metric}_{unit}")
    if "mass" in metric or "weight" in metric or "mass_geometry" in str(row.get("check_type") or ""):
        suggestions.extend([
            "material_properties.density_tonne_per_mm3",
            "material with a *DENSITY card",
            "engineering_metrics.mass_kg or engineering_metrics.mass_g",
        ])
    return list(dict.fromkeys(suggestions))


def parse_mesh_bbox(mesh_path: Path) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    if not mesh_path.exists():
        return None
    nodes: list[tuple[float, float, float]] = []
    in_node = False
    with mesh_path.open(errors="ignore") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("*"):
                in_node = line.upper().startswith("*NODE")
                continue
            if not in_node:
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 4:
                continue
            try:
                nodes.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError:
                continue
    if not nodes:
        return None
    xs, ys, zs = zip(*nodes)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def parse_inp_names(inp_path: Path) -> dict[str, list[str]]:
    names: dict[str, list[str]] = {"nsets": [], "elsets": [], "surfaces": []}
    if not inp_path.exists():
        return names
    patterns = {
        "nsets": re.compile(r"\bNSET\s*=\s*([^,\s]+)", re.I),
        "elsets": re.compile(r"\bELSET\s*=\s*([^,\s]+)", re.I),
        "surfaces": re.compile(r"\bNAME\s*=\s*([^,\s]+)", re.I),
    }
    for line in inp_path.read_text(errors="ignore").splitlines():
        if not line.lstrip().startswith("*"):
            continue
        upper = line.upper()
        for kind, pattern in patterns.items():
            if kind == "surfaces" and not upper.startswith("*SURFACE"):
                continue
            match = pattern.search(line)
            if match:
                value = match.group(1)
                if value not in names[kind]:
                    names[kind].append(value)
    return names


def selector_box_diagnostics(meta: dict[str, Any], eval_dir: Path) -> list[str]:
    selectors = meta.get("selectors")
    if not isinstance(selectors, dict):
        return ["- `meta.json.selectors` is missing or not an object."]
    mesh_bbox = parse_mesh_bbox(eval_dir / "mesh.inp") or parse_mesh_bbox(eval_dir / "model.inp")
    lines = [f"- Selector names in `meta.json`: {', '.join(sorted(selectors)) or '(none)'}."]
    if mesh_bbox:
        lo, hi = mesh_bbox
        lines.append(
            "- Mesh bbox from eval mesh: "
            f"x[{lo[0]:.3g},{hi[0]:.3g}] y[{lo[1]:.3g},{hi[1]:.3g}] z[{lo[2]:.3g},{hi[2]:.3g}]."
        )
    outside_count = 0
    for name, spec in selectors.items():
        specs = spec.get("any_of") if isinstance(spec, dict) else None
        if not isinstance(specs, list):
            continue
        box_count = 0
        for item in specs:
            box = item.get("box") if isinstance(item, dict) else None
            if not (isinstance(box, list) and len(box) == 6):
                continue
            box_count += 1
            if not mesh_bbox:
                continue
            lo, hi = mesh_bbox
            deltas = [
                max(lo[0] - float(box[3]), float(box[0]) - hi[0], 0.0),
                max(lo[1] - float(box[4]), float(box[1]) - hi[1], 0.0),
                max(lo[2] - float(box[5]), float(box[2]) - hi[2], 0.0),
            ]
            if any(delta > 1.0e-6 for delta in deltas) and outside_count < 5:
                outside_count += 1
                lines.append(f"- Selector `{name}` box {box} does not intersect mesh bbox; axis gap approx {deltas}.")
        if box_count:
            lines.append(f"- Selector `{name}` has {box_count} explicit box region(s).")
    return lines[:12]


def diagnostic_log_excerpt(eval_dir: Path, failed_stage: str | None) -> list[str]:
    candidates = [
        eval_dir / "grade.log",
        eval_dir / "eval_driver.log",
        eval_dir / "check.log",
    ]
    markers = re.compile(r"(\*ERROR|\bERROR\b|Traceback|does not exist|does not contain|missing|failed)", re.I)
    out: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        hits = [line.strip() for line in path.read_text(errors="ignore").splitlines() if markers.search(line)]
        if hits:
            out.append(f"- `{path.name}` diagnostic lines:")
            out.extend(f"  - {line[:220]}" for line in hits[-8:])
            break
    if not out and failed_stage:
        out.append(f"- No detailed `{failed_stage}` log excerpt matched the diagnostic markers.")
    return out


def missing_ccx_name_diagnostics(eval_dir: Path, meta: dict[str, Any]) -> list[str]:
    grade = eval_dir / "grade.log"
    if not grade.exists():
        return []
    text = grade.read_text(errors="ignore")
    missing = sorted(set(re.findall(r"\b([A-Z][A-Z0-9_]+)\b(?=[^\n]{0,80}(?:does not exist|does not contain))", text)))
    for match in re.finditer(r"\b(?:surface|set)\s+([A-Z][A-Z0-9_]+)\b", text, re.I):
        name = match.group(1).upper()
        window = text[match.start(): match.start() + 260].lower()
        if "does not exist" in window or "does not contain" in window:
            missing.append(name)
    missing = sorted(set(missing))
    if not missing:
        return []
    names = parse_inp_names(eval_dir / "model.inp")
    selectors = meta.get("selectors") if isinstance(meta.get("selectors"), dict) else {}
    lines = []
    for name in missing[:5]:
        lines.append(f"- CCX references `{name}`, but the solver log says it is missing or empty.")
        if name not in selectors and name.replace("SURF_", "N") not in selectors:
            lines.append(f"  - `meta.json.selectors` has no direct `{name}` entry.")
    for kind, values in names.items():
        if values:
            lines.append(f"- Names present in `model.inp` {kind}: {', '.join(values[:12])}.")
    return lines[:14]


def format_deep_feedback(result: dict[str, Any], attempt_dir: Path | None) -> list[str]:
    if attempt_dir is None:
        source = result.get("source")
        attempt_dir = Path(str(source)) if source else None
    eval_dir = Path(str(result.get("workdir"))) if result.get("workdir") else Path()
    meta = read_json_dict((attempt_dir / "meta.json") if attempt_dir else Path()) or read_json_dict(eval_dir / "meta.json")

    lines = [
        "",
        "Deep diagnostic hints:",
        "- These hints are deterministic checks over `meta.json`, eval logs, and the eval mesh. Treat them as repair guidance, not as model-generated speculation.",
    ]

    stage_lines = diagnostic_log_excerpt(eval_dir, result.get("failed_stage"))
    missing_name_lines = missing_ccx_name_diagnostics(eval_dir, meta)
    if stage_lines or missing_name_lines:
        lines.extend(["", "Stage/log diagnosis:"])
        lines.extend(stage_lines)
        lines.extend(missing_name_lines)

    failed_rows = [
        row for row in (result.get("per_requirement_results") or [])
        if str(row.get("verdict") or "").lower() != "pass"
    ]
    if failed_rows:
        lines.extend(["", "Failed requirement diagnosis:"])
    for row in failed_rows[:8]:
        rid = row.get("requirement_id")
        detail = str(row.get("detail") or "")
        metric = row.get("metric") or "(unknown metric)"
        check_type = row.get("check_type") or "(unknown check type)"
        support = row.get("support_status") or row.get("evaluation_status") or "(unknown support status)"
        lines.append(f"- `{rid}` metric `{metric}` / check `{check_type}` failed: {detail[:180]}")
        lines.append(f"  - support/eval status: {support}")
        limit = row.get("limit") if isinstance(row.get("limit"), dict) else {}
        if limit:
            lines.append(f"  - declared plan limit: {limit.get('key')}={limit.get('value')}, operator `{row.get('operator') or '<='}`")
        if "unsupported" in detail.lower():
            if result.get("set") == "multi" or row.get("checker_kind") == "coverage_only":
                lines.append("  - Current generic multipart checker has no CAD-only mapping for this metric; geometry edits alone may not make this row pass.")
            else:
                lines.append("  - Consider adding an explicit numeric value for this metric under `engineering_metrics` if the checker supports submitted metrics.")
        if "missing numeric declared limit" in detail.lower() or "missing" in detail.lower():
            suggestions = suggested_meta_paths(row)
            if suggestions:
                lines.append("  - Candidate fields to add/check: " + "; ".join(f"`{item}`" for item in suggestions[:8]) + ".")
            matches = matching_meta_paths(meta, row)
            if matches:
                lines.append("  - Related numeric fields currently present: " + "; ".join(f"`{item}`" for item in matches) + ".")
            else:
                lines.append("  - No related numeric fields were found in `meta.json` by token search.")

    selector_lines = selector_box_diagnostics(meta, eval_dir)
    if selector_lines:
        lines.extend(["", "Selector/mesh diagnosis:"])
        lines.extend(selector_lines)
    return lines[:80]


def format_feedback(
    result: dict[str, Any],
    attempt_dir: Path | None = None,
    feedback_mode: str = "standard",
) -> str:
    lines = [
        f"CCX attempt result: {'PASS' if strict_pass(result) else 'FAIL'}",
        f"Category: {result.get('category')}",
        f"Coverage status: {result.get('coverage_status')}",
        f"Engineering status: {result.get('engineering_status')}",
        f"Failed stage: {result.get('failed_stage') or '(none recorded)'}",
        f"Final message: {result.get('final_msg') or ''}",
        "",
        "Stage summary:",
    ]
    stages = result.get("stages") or {}
    for stage in ("build", "gmsh", "wire_bcs", "ccx", "check"):
        info = stages.get(stage) or {}
        lines.append(f"- {stage}: rc={info.get('rc', 'not_run')} elapsed={info.get('elapsed_s', 'n/a')}")

    rows = result.get("per_requirement_results") or []
    if rows:
        lines.extend(["", "Per-requirement summary:"])
        for row in rows:
            detail = str(row.get("detail") or row.get("source") or "").replace("\n", " ")
            lines.append(f"- {row.get('requirement_id')}: {row.get('verdict')} {detail[:240]}")
    else:
        lines.extend(["", "Per-requirement summary: no parsed requirement rows were available."])

    if feedback_mode == "deep-feedback":
        lines.extend(format_deep_feedback(result, attempt_dir))

    lines.extend([
        "",
        "Repair target:",
        "- Fix the earliest failed stage first.",
        "- Preserve requirements that already passed.",
        "- Ensure build.py regenerates out.step and meta.json from scratch.",
        "- Keep selectors broad, named correctly, and mapped to real exterior regions.",
    ])
    return "\n".join(lines)


def run_eval_attempt(config: LoopConfig, set_name: str, case_id: str, attempt_index: int, attempt_dir: Path) -> dict[str, Any]:
    eval_attempt_root = config.run_root / "evals" / f"attempt_{attempt_index:02d}"
    return eval_one(set_name, case_id, attempt_dir, eval_attempt_root, config.timeout_eval)


def _attempt_index(attempt: dict[str, Any]) -> int:
    return int(attempt.get("attempt_index") or 0)


def _finalize_loop_item(
    *,
    set_name: str,
    case_id: str,
    attempts: list[dict[str, Any]],
    selected_result: dict[str, Any],
) -> dict[str, Any]:
    final_attempt = attempts[-1]
    final_result = dict(selected_result)
    final_result["loop_attempt_index"] = final_attempt["attempt_index"]
    final_result["loop_attempt_workdir"] = final_attempt["attempt_workdir"]
    final_result["loop_eval_workdir"] = final_attempt["eval_workdir"]
    return {
        "set": set_name,
        "id": case_id,
        "status": "pass" if strict_pass(selected_result) else "max_attempts_exhausted",
        "selected_attempt_index": final_attempt["attempt_index"],
        "attempt_count": len(attempts),
        "attempts": attempts,
        "final_result": final_result,
    }


def normalize_loop_item(item: dict[str, Any]) -> dict[str, Any]:
    attempts = list(item.get("attempts") or [])
    final_result = item.get("final_result") or {}
    if not attempts:
        return dict(item)
    set_name = str(item.get("set") or final_result.get("set") or "")
    case_id = str(item.get("id") or final_result.get("id") or "")
    selected_attempt = item.get("selected_attempt_index")
    if selected_attempt is None:
        selected_attempt = _attempt_index(attempts[-1])
    selected_result = final_result
    if not selected_result:
        selected = next(
            (attempt for attempt in attempts if _attempt_index(attempt) == int(selected_attempt)),
            attempts[-1],
        )
        selected_result = selected.get("eval_result") or {}
    return _finalize_loop_item(
        set_name=set_name,
        case_id=case_id,
        attempts=attempts,
        selected_result=selected_result,
    )


LIMIT_DEAD_MARKERS = (
    "monthly usage limit",
    "usage limit",
)


def item_latest_attempt_limit_dead(item: dict[str, Any] | None) -> bool:
    if not item:
        return False
    normalized = normalize_loop_item(item)
    attempts = list(normalized.get("attempts") or [])
    if not attempts:
        return False
    latest = max(attempts, key=_attempt_index)
    attempt_workdir = latest.get("attempt_workdir")
    if not attempt_workdir:
        return False
    run_log = Path(str(attempt_workdir)) / "run.log"
    try:
        text = run_log.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(marker in text for marker in LIMIT_DEAD_MARKERS)


def should_extend_item(
    item: dict[str, Any] | None,
    max_attempts: int,
    *,
    limit_dead_only: bool = False,
) -> bool:
    if not item:
        return True
    normalized = normalize_loop_item(item)
    if strict_pass(normalized.get("final_result") or {}):
        return False
    if limit_dead_only and not item_latest_attempt_limit_dead(normalized):
        return False
    return len(normalized.get("attempts") or []) < max_attempts


def run_item(
    config: LoopConfig,
    set_name: str,
    case_id: str,
    existing_item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    selected_result: dict[str, Any] | None = None
    previous_attempt: Path | None = None
    feedback: str | None = None
    next_attempt_index = 0

    if existing_item:
        normalized = normalize_loop_item(existing_item)
        attempts = list(normalized.get("attempts") or [])
        selected_result = dict(normalized.get("final_result") or {})
        if attempts:
            last_attempt = max(attempts, key=_attempt_index)
            previous_attempt_raw = last_attempt.get("attempt_workdir")
            previous_attempt = Path(previous_attempt_raw) if previous_attempt_raw else None
            feedback = format_feedback(
                last_attempt.get("eval_result") or selected_result,
                previous_attempt,
                config.feedback_mode,
            )
            next_attempt_index = _attempt_index(last_attempt) + 1

    if selected_result and strict_pass(selected_result):
        return _finalize_loop_item(
            set_name=set_name,
            case_id=case_id,
            attempts=attempts,
            selected_result=selected_result,
        )

    while len(attempts) < config.max_attempts:
        attempt_index = next_attempt_index
        attempt_dir = prepare_attempt_dir(
            config=config,
            set_name=set_name,
            case_id=case_id,
            attempt_index=attempt_index,
            previous_attempt_dir=previous_attempt,
            feedback=feedback,
        )
        prompt = (
            build_initial_prompt(
                set_name,
                case_id,
                config.skill_mode,
                require_rich_view=config.require_rich_view,
                attempt_index=attempt_index,
            )
            if attempt_index == 0
            else build_repair_prompt(
                set_name,
                case_id,
                config.skill_mode,
                attempt_dir,
                feedback or "",
                require_rich_view=config.require_rich_view,
                attempt_index=attempt_index,
            )
        )
        model_result = run_model_attempt(config, set_name, case_id, attempt_dir, prompt)
        if config.cleanup_render_images:
            model_result["render_image_cleanup"] = cleanup_render_images(attempt_dir)
        eval_result = run_eval_attempt(config, set_name, case_id, attempt_index, attempt_dir)
        passed = strict_pass(eval_result)
        attempt_record = {
            "attempt_index": attempt_index,
            "attempt_workdir": str(attempt_dir),
            "eval_workdir": eval_result.get("workdir"),
            "model_result": model_result,
            "eval_result": eval_result,
            "strict_pass": passed,
            "started_at": iso_now(),
        }
        attempts.append(attempt_record)
        selected_result = eval_result
        feedback = format_feedback(eval_result, attempt_dir, config.feedback_mode)
        (attempt_dir / "ccx_feedback.md").write_text(feedback, encoding="utf-8")
        previous_attempt = attempt_dir
        next_attempt_index = attempt_index + 1
        if passed:
            break

    assert selected_result is not None
    return _finalize_loop_item(
        set_name=set_name,
        case_id=case_id,
        attempts=attempts,
        selected_result=selected_result,
    )


def write_loop_per_check(out_root: Path, loop_results: list[dict[str, Any]]) -> None:
    columns = [
        "attempt_index",
        "selected_final_attempt",
        "attempt_workdir",
        "eval_workdir",
    ] + PER_CHECK_CSV_COLUMNS
    with (out_root / "loop_per_check_results.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for item in loop_results:
            selected = item["selected_attempt_index"]
            for attempt in item["attempts"]:
                result = attempt["eval_result"]
                rows = flatten_per_requirement_results([result])
                for row in rows:
                    enriched_row = dict(row)
                    enriched_row.setdefault("set", result.get("set"))
                    enriched_row.setdefault("item_id", result.get("id"))
                    writer.writerow({
                        "attempt_index": attempt["attempt_index"],
                        "selected_final_attempt": attempt["attempt_index"] == selected,
                        "attempt_workdir": attempt["attempt_workdir"],
                        "eval_workdir": attempt["eval_workdir"],
                        **{
                            key: (
                                json.dumps(enriched_row.get(key), sort_keys=True)
                                if isinstance(enriched_row.get(key), (dict, list))
                                else enriched_row.get(key)
                            )
                            for key in PER_CHECK_CSV_COLUMNS
                        },
                    })


def summarize(final_results: list[dict[str, Any]], loop_results: list[dict[str, Any]], config: LoopConfig) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for result in final_results:
        category = str(result.get("category") or "unknown")
        counts[category] = counts.get(category, 0) + 1
    return {
        "backend": config.backend,
        "model": config.model,
        "reasoning": config.reasoning if config.backend == "codex" else None,
        "effort": config.effort if config.backend in {"claude", "opencode"} else None,
        "variant": config.effort if config.backend == "opencode" else None,
        "skill_mode": config.skill_mode,
        "feedback_mode": config.feedback_mode,
        "isolation_mode": config.isolation_mode,
        "max_attempts": config.max_attempts,
        "model_launch_retries": config.model_launch_retries,
        "total": len(final_results),
        "strict_pass_count": sum(1 for result in final_results if strict_pass(result)),
        "category_counts": counts,
        "attempt_count_distribution": {
            str(n): sum(1 for item in loop_results if item["attempt_count"] == n)
            for n in range(1, config.max_attempts + 1)
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["codex", "claude", "opencode"], required=True)
    parser.add_argument("--set", choices=["single", "multi", "all"], default="all")
    parser.add_argument("--scope", choices=["curated", "full"], default="curated")
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reasoning", default="high", choices=["minimal", "low", "medium", "high", "xhigh"])
    parser.add_argument("--effort", default="max", choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--codex-bin", default=None)
    parser.add_argument(
        "--codex-sandbox",
        default=None,
        choices=["read-only", "workspace-write", "danger-full-access"],
        help=(
            "Sandbox mode passed to `codex exec`. Rich-view rendering needs "
            "`danger-full-access` because the OCP viewer binds a local 127.0.0.1 port. "
            "Defaults to danger-full-access for Codex rich-view runs and workspace-write otherwise."
        ),
    )
    parser.add_argument("--claude-bin", default=None)
    parser.add_argument("--opencode-bin", default=None)
    parser.add_argument("--claude-skip-permissions", action="store_true")
    parser.add_argument("--opencode-skip-permissions", action="store_true")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument(
        "--resume-run-root",
        default=None,
        help=(
            "Existing loop run root to extend in place. Existing strict-pass "
            "items are reused, failed items continue from their latest attempt, "
            "and new attempts receive the currently selected skill/tool bundle."
        ),
    )
    parser.add_argument(
        "--resume-limit-dead-only",
        action="store_true",
        help=(
            "When resuming, only extend items whose latest attempt run.log "
            "contains a model-provider usage/quota limit message."
        ),
    )
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout-model", type=int, default=2400)
    parser.add_argument("--timeout-eval", type=float, default=900)
    parser.add_argument(
        "--model-launch-retries",
        type=int,
        default=2,
        help=(
            "Retry transient model-provider launch failures inside the same "
            "attempt when no valid deliverables were produced. Currently used "
            "for OpenCode transport/certificate errors."
        ),
    )
    parser.add_argument("--skill-mode", choices=["none", "cad", "blueprint", "all"], default="cad")
    parser.add_argument(
        "--feedback-mode",
        choices=["standard", "deep-feedback"],
        default="standard",
        help=(
            "Controls the CCX retry feedback payload. `deep-feedback` appends "
            "deterministic diagnostics for metadata keys, unsupported metrics, "
            "selector/mesh mismatches, and solver log errors."
        ),
    )
    parser.add_argument(
        "--require-rich-view",
        action="store_true",
        help="Explicitly instruct model attempts to render and inspect rich-view images before finalizing.",
    )
    parser.add_argument(
        "--cleanup-render-images",
        action="store_true",
        help=(
            "After each model attempt exits, delete render_sets/**/view_*.png "
            "while keeping manifests and a render_image_cleanup.json record."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace, run_root: Path) -> LoopConfig:
    model = args.model or DEFAULT_MODEL[args.backend]
    codex_bin = find_codex_cli(args.codex_bin) if args.backend == "codex" else None
    claude_bin = resolve_claude_cli(args.claude_bin) if args.backend == "claude" else None
    opencode_bin = resolve_opencode_cli(args.opencode_bin) if args.backend == "opencode" else None
    codex_sandbox = args.codex_sandbox or (
        "danger-full-access"
        if args.backend == "codex" and args.require_rich_view
        else "workspace-write"
    )
    return LoopConfig(
        backend=args.backend,
        model=model,
        reasoning=args.reasoning,
        effort=args.effort,
        codex_bin=codex_bin,
        codex_sandbox=codex_sandbox,
        claude_bin=claude_bin,
        opencode_bin=opencode_bin,
        claude_skip_permissions=bool(args.claude_skip_permissions),
        opencode_skip_permissions=bool(args.opencode_skip_permissions),
        max_attempts=args.max_attempts,
        timeout_model=args.timeout_model,
        timeout_eval=args.timeout_eval if args.timeout_eval > 0 else None,
        model_launch_retries=args.model_launch_retries,
        skill_mode=args.skill_mode,
        feedback_mode=args.feedback_mode,
        require_rich_view=bool(args.require_rich_view),
        cleanup_render_images=bool(args.cleanup_render_images),
        run_root=run_root,
        isolation_mode=isolation_mode_for(run_root),
    )


def selected_items(set_arg: str, scope: str, only: list[str] | None) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    sets = ["single", "multi"] if set_arg == "all" else [set_arg]
    requested = set(only or [])
    for set_name in sets:
        for case_id in selected_ids(set_name, scope):
            if requested and case_id not in requested:
                continue
            items.append((set_name, case_id))
    return items


def load_resume_loop_results(run_root: Path) -> dict[tuple[str, str], dict[str, Any]]:
    candidates = [run_root / "loop_results.json", run_root / "partial_summary.json"]
    out: dict[tuple[str, str], dict[str, Any]] = {}

    def item_score(item: dict[str, Any]) -> tuple[int, int]:
        attempts = list(item.get("attempts") or [])
        if not attempts:
            return (0, 0)
        return (len(attempts), max(_attempt_index(attempt) for attempt in attempts))

    for path in candidates:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for raw_item in data:
            item = normalize_loop_item(raw_item)
            key = (str(item.get("set")), str(item.get("id")))
            existing = out.get(key)
            if existing is None or item_score(item) > item_score(existing):
                out[key] = item
    return out


def build_run_metadata(args: argparse.Namespace, config: LoopConfig, items: list[tuple[str, str]]) -> dict[str, Any]:
    run_metadata_path = config.run_root / "run_metadata.json"
    previous_metadata: dict[str, Any] = {}
    if args.resume_run_root and run_metadata_path.exists():
        previous_metadata = read_json_file(run_metadata_path)

    resume_history = list(previous_metadata.get("resume_history") or [])
    if args.resume_run_root:
        resume_history.append({
            "resumed_at": iso_now(),
            "target_max_attempts": config.max_attempts,
            "skill_mode": config.skill_mode,
            "feedback_mode": config.feedback_mode,
            "backend": config.backend,
            "model": config.model,
            "reasoning": config.reasoning if config.backend == "codex" else None,
            "effort": config.effort if config.backend in {"claude", "opencode"} else None,
            "variant": config.effort if config.backend == "opencode" else None,
            "require_rich_view": config.require_rich_view,
            "cleanup_render_images": config.cleanup_render_images,
            "codex_sandbox": config.codex_sandbox if config.backend == "codex" else None,
            "model_launch_retries": config.model_launch_retries,
            "resume_limit_dead_only": bool(args.resume_limit_dead_only),
        })

    return {
        "backend": config.backend,
        "model": config.model,
        "reasoning": config.reasoning if config.backend == "codex" else None,
        "effort": config.effort if config.backend in {"claude", "opencode"} else None,
        "variant": config.effort if config.backend == "opencode" else None,
        "codex_bin": config.codex_bin,
        "codex_sandbox": config.codex_sandbox if config.backend == "codex" else None,
        "claude_bin": config.claude_bin,
        "opencode_bin": config.opencode_bin,
        "claude_permission_mode": (
            "dangerously_skip_permissions"
            if config.backend == "claude" and config.claude_skip_permissions
            else "settings_enforced"
            if config.backend == "claude"
            else None
        ),
        "opencode_permission_mode": (
            "dangerously_skip_permissions"
            if config.backend == "opencode" and config.opencode_skip_permissions
            else "default_permissions"
            if config.backend == "opencode"
            else None
        ),
        "isolation_guarantee": (
            "workspace_layout_and_prompt_only"
            if (
                (config.backend == "claude" and config.claude_skip_permissions)
                or (config.backend == "opencode" and config.opencode_skip_permissions)
            )
            else "settings_enforced_or_workspace_isolated"
        ),
        "skill_mode": config.skill_mode,
        "feedback_mode": config.feedback_mode,
        "require_rich_view": config.require_rich_view,
        "cleanup_render_images": config.cleanup_render_images,
        "isolation_mode": config.isolation_mode,
        "run_root": str(config.run_root),
        "max_attempts": config.max_attempts,
        "model_launch_retries": config.model_launch_retries,
        "jobs": args.jobs,
        "timeout_model": config.timeout_model,
        "timeout_eval": config.timeout_eval,
        "started_at": previous_metadata.get("started_at") or iso_now(),
        "current_session_started_at": iso_now(),
        "items": [{"set": set_name, "id": case_id} for set_name, case_id in items],
        "status": "running",
        "resume_run_root": str(config.run_root) if args.resume_run_root else None,
        "resume_limit_dead_only": bool(args.resume_limit_dead_only),
        "resume_history": resume_history,
    }


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_attempts < 1:
        print("ERROR: --max-attempts must be >= 1", file=sys.stderr)
        return 2
    if args.jobs < 1:
        print("ERROR: --jobs must be >= 1", file=sys.stderr)
        return 2
    if args.model_launch_retries < 0:
        print("ERROR: --model-launch-retries must be >= 0", file=sys.stderr)
        return 2

    run_root = (
        Path(args.resume_run_root).expanduser().resolve()
        if args.resume_run_root
        else timestamped_run_root(Path(args.out_root))
    )
    config = build_config(args, run_root)
    items = selected_items(args.set, args.scope, args.only)
    resume_items = load_resume_loop_results(run_root) if args.resume_run_root else {}
    selected_keys = set(items)

    print(
        f"Plan: {len(items)} items backend={config.backend} model={config.model} "
        f"attempts={config.max_attempts} launch_retries={config.model_launch_retries} "
        f"feedback={config.feedback_mode} jobs={args.jobs} out={run_root}"
    )
    if args.resume_run_root:
        extend_count = sum(
            1
            for item in items
            if should_extend_item(
                resume_items.get(item),
                config.max_attempts,
                limit_dead_only=bool(args.resume_limit_dead_only),
            )
        )
        print(
            f"Resume: loaded={len(resume_items)} extend_or_start={extend_count} "
            f"reuse_without_new_attempts={len(items) - extend_count}",
            flush=True,
        )
    for set_name, case_id in items:
        print(f"  [{set_name}] {case_id}")
    if args.dry_run:
        return 0
    if config.backend == "codex" and not config.codex_bin:
        print("ERROR: codex CLI not found; pass --codex-bin", file=sys.stderr)
        return 2
    if config.backend == "claude" and not config.claude_bin:
        print("ERROR: claude CLI not found; pass --claude-bin", file=sys.stderr)
        return 2
    if config.backend == "opencode" and not config.opencode_bin:
        print("ERROR: opencode CLI not found; pass --opencode-bin", file=sys.stderr)
        return 2

    run_root.mkdir(parents=True, exist_ok=True)
    started = time.time()
    run_metadata = build_run_metadata(args, config, items)
    (run_root / "run_metadata.json").write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")

    loop_results: list[dict[str, Any]] = []
    partial_path = run_root / "partial_summary.json"
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(run_item, config, set_name, case_id, resume_items.get((set_name, case_id))): (set_name, case_id)
            for set_name, case_id in items
            if should_extend_item(
                resume_items.get((set_name, case_id)),
                config.max_attempts,
                limit_dead_only=bool(args.resume_limit_dead_only),
            )
        }
        for set_name, case_id in items:
            existing = resume_items.get((set_name, case_id))
            if existing and not should_extend_item(
                existing,
                config.max_attempts,
                limit_dead_only=bool(args.resume_limit_dead_only),
            ):
                loop_results.append(normalize_loop_item(existing))
        if loop_results:
            partial_path.write_text(json.dumps(loop_results, indent=2), encoding="utf-8")
        for future in as_completed(futures):
            result = future.result()
            loop_results.append(result)
            partial_path.write_text(json.dumps(loop_results, indent=2), encoding="utf-8")
            print(
                f"  [{result['set']:<6}] {result['id']:<55} {result['status']:<24} "
                f"attempts={result['attempt_count']}"
            )

    if args.resume_run_root and args.only:
        existing_keys = {(item["set"], item["id"]) for item in loop_results}
        for key, existing in resume_items.items():
            if key not in selected_keys and key not in existing_keys:
                loop_results.append(normalize_loop_item(existing))

    if args.resume_run_root and resume_items:
        order = {key: idx for idx, key in enumerate(resume_items)}
        for idx, key in enumerate(items, start=len(order)):
            order.setdefault(key, idx)
    else:
        order = {item: idx for idx, item in enumerate(items)}
    loop_results.sort(key=lambda item: order.get((item["set"], item["id"]), 999999))
    final_results = [item["final_result"] for item in loop_results]
    write_per_check_outputs(run_root, final_results)
    write_loop_per_check(run_root, loop_results)

    (run_root / "loop_results.json").write_text(json.dumps(loop_results, indent=2), encoding="utf-8")
    (run_root / "results.json").write_text(json.dumps(final_results, indent=2), encoding="utf-8")
    summary = summarize(final_results, loop_results, config)
    ended_at = iso_now()
    try:
        started_at_dt = datetime.fromisoformat(str(run_metadata["started_at"]).replace("Z", "+00:00"))
        total_elapsed_s = round((datetime.now(timezone.utc) - started_at_dt).total_seconds(), 2)
    except ValueError:
        total_elapsed_s = round(time.time() - started, 2)
    summary.update({
        "started_at": run_metadata["started_at"],
        "current_session_started_at": run_metadata["current_session_started_at"],
        "ended_at": ended_at,
        "elapsed_s": total_elapsed_s,
        "session_elapsed_s": round(time.time() - started, 2),
        "out_root": str(run_root),
        "resumed": bool(args.resume_run_root),
    })
    (run_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    run_metadata.update({
        "status": "complete",
        "ended_at": summary["ended_at"],
        "elapsed_s": summary["elapsed_s"],
        "session_elapsed_s": summary["session_elapsed_s"],
        "strict_pass_count": summary["strict_pass_count"],
        "total": summary["total"],
    })
    (run_root / "run_metadata.json").write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")
    print(f"Done. strict_pass={summary['strict_pass_count']}/{summary['total']}")
    print(f"Summary: {run_root / 'summary.json'}")
    return 0 if summary["strict_pass_count"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
