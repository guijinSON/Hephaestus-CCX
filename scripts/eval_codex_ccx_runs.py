#!/usr/bin/env python3
"""Evaluate generated Codex CCX submissions against docs/eval/ccx_50 kits."""

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CCX_ROOT = REPO_ROOT / "docs" / "eval" / "ccx_50"
GRADE = REPO_ROOT / "scripts" / "ccx_eval" / "grade_ccx.py"
MULTI_ENGINEERING_CHECK = REPO_ROOT / "scripts" / "ccx_eval" / "multi_engineering_check.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from fire_codex_step_50 import CURATED_20_SINGLE, CURATED_30_CCX_MULTI  # noqa: E402
from ccx_eval.engineering_requirements import (  # noqa: E402
    aggregate_plan_counts,
    apply_evaluation_statuses,
    build_engineering_plan,
)


SUMMARY_RE = re.compile(r"SUMMARY PASS=(\d+) FAIL=(\d+) SKIP=(\d+) TOTAL=(\d+)")
CHECK_ROW_RE = re.compile(r"^\[(PASS|FAIL|SKIP)\]\s+(\S+)\s+class=(\S+)\s+source=(.*)$")
VERDICT_RE = re.compile(r"\b(PASS|FAIL|SKIP)\b", re.IGNORECASE)
REQ_ID_RE = r"R(?:_[A-Za-z0-9][A-Za-z0-9_.-]*|[A-Za-z0-9][A-Za-z0-9_.-]*)"
REQ_SUMMARY_RES = [
    re.compile(
        r"\b(?:Totals?|SUMMARY|Summary):?\s*"
        r"PASS=(\d+),?\s*FAIL=(\d+),?\s*SKIP=(\d+)"
        r"(?:\s*(?:TOTAL=|\(of\s+)(\d+)\)?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bSummary:\s*(\d+)\s+PASS,\s*(\d+)\s+FAIL,\s*(\d+)\s+SKIP",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bSUMMARY:\s*(\d+)\s+PASS\s*/\s*(\d+)\s+FAIL\s+out\s+of\s+(\d+)",
        re.IGNORECASE,
    ),
]
REQ_VERDICT_LINE_RES = [
    re.compile(rf"^\s*\[(PASS|FAIL|SKIP)\]\s+({REQ_ID_RE})\b", re.IGNORECASE),
    re.compile(rf"^\s*(PASS|FAIL|SKIP)\s+({REQ_ID_RE})\b", re.IGNORECASE),
    re.compile(rf"^\s*({REQ_ID_RE})\s*:\s*(PASS|FAIL|SKIP)\b", re.IGNORECASE),
    re.compile(rf"^\s*({REQ_ID_RE})\b.*?(?:=>|->)\s*(PASS|FAIL|SKIP)\b", re.IGNORECASE),
    re.compile(rf"^\s*({REQ_ID_RE})\b", re.IGNORECASE),
]

PRECHECK_STAGES = ("build", "gmsh", "wire_bcs", "ccx")
CHECKER_COVERAGE_ONLY = "coverage_only"
CHECKER_ENGINEERING = "engineering_requirements"
CHECKER_UNKNOWN = "unknown"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_summary(check_log: Path) -> dict[str, int] | None:
    if not check_log.exists():
        return None
    match = SUMMARY_RE.search(check_log.read_text(errors="ignore"))
    if not match:
        return None
    return {
        "pass": int(match.group(1)),
        "fail": int(match.group(2)),
        "skip": int(match.group(3)),
        "total": int(match.group(4)),
    }


def parse_check_rows(check_log: Path) -> list[dict[str, str]]:
    if not check_log.exists():
        return []
    rows = []
    for line in check_log.read_text(errors="ignore").splitlines():
        match = CHECK_ROW_RE.match(line.strip())
        if not match:
            continue
        verdict, requirement_id, requirement_class, source = match.groups()
        rows.append(
            {
                "verdict": verdict.lower(),
                "requirement_id": requirement_id,
                "requirement_class": requirement_class,
                "source": source,
            }
        )
    return rows


def _summary_from_counts(passed: int, failed: int, skipped: int, total: int | None = None) -> dict[str, int]:
    if total is None:
        total = passed + failed + skipped
    total = max(total, passed + failed + skipped)
    return {
        "pass": passed,
        "fail": failed,
        "skip": skipped,
        "total": total,
    }


def parse_requirement_summary(check_log: Path) -> dict[str, int] | None:
    """Parse requirement verdict counts from case-specific checker logs.

    The single-part checkers are handwritten and use several print formats.
    This parser intentionally keys only on explicit R-id rows so non-requirement
    PASS/FAIL text does not inflate partial scores.
    """
    if not check_log.exists():
        return None
    text = check_log.read_text(errors="ignore")
    for pattern in REQ_SUMMARY_RES:
        match = pattern.search(text)
        if not match:
            continue
        groups = match.groups()
        passed = int(groups[0])
        failed = int(groups[1])
        if pattern is REQ_SUMMARY_RES[2]:
            skipped = 0
            total = int(groups[2])
        else:
            skipped = int(groups[2])
            total = int(groups[3]) if len(groups) > 3 and groups[3] else None
        return _summary_from_counts(passed, failed, skipped, total)

    rows = parse_requirement_rows(check_log)
    if not rows:
        return None
    counts = {"pass": 0, "fail": 0, "skip": 0}
    for row in rows:
        counts[row["verdict"]] += 1
    return _summary_from_counts(counts["pass"], counts["fail"], counts["skip"])


def parse_requirement_rows(check_log: Path) -> list[dict[str, str]]:
    if not check_log.exists():
        return []
    verdicts: dict[str, str] = {}
    for raw_line in check_log.read_text(errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or "OVERALL" in line.upper():
            continue

        matched = False
        for idx, pattern in enumerate(REQ_VERDICT_LINE_RES):
            match = pattern.search(line)
            if not match:
                continue
            groups = match.groups()
            if idx in (0, 1):
                verdict, requirement_id = groups[0].lower(), groups[1]
            elif idx in (2, 3):
                requirement_id, verdict = groups[0], groups[1].lower()
            else:
                requirement_id = groups[0]
                verdict_hits = [hit.group(1).lower() for hit in VERDICT_RE.finditer(line)]
                if not verdict_hits:
                    continue
                verdict = verdict_hits[-1]
            verdicts[requirement_id] = verdict
            matched = True
            break
        if matched:
            continue

    return [
        {"requirement_id": requirement_id, "verdict": verdict}
        for requirement_id, verdict in sorted(
            verdicts.items(),
            key=lambda item: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", item[0])],
        )
    ]


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def inspect_checker(spec_dir: Path) -> dict[str, Any]:
    """Classify whether check.py is a coverage harness or an engineering checker."""
    spec = read_json(spec_dir / "spec.json")
    check_path = spec_dir / "check.py"
    try:
        text = check_path.read_text(errors="ignore")
    except OSError:
        text = ""

    reasons: list[str] = []
    coverage = spec.get("generated_eval_coverage")
    if isinstance(coverage, dict):
        if coverage.get("fidelity") == "generic_coverage_harness":
            reasons.append("spec.generated_eval_coverage.fidelity=generic_coverage_harness")
        if "coverage" in str(coverage.get("purpose") or "").lower():
            reasons.append("spec.generated_eval_coverage.purpose mentions coverage")

    coverage_markers = [
        "Generated CCX coverage checker",
        "def requirement_covered(",
        "def dat_features(",
        "geometry/spec assertion",
        "static stress/displacement block",
    ]
    marker_hits = [marker for marker in coverage_markers if marker in text]
    if len(marker_hits) >= 3:
        reasons.append("check.py matches generated coverage checker markers")

    if reasons:
        return {
            "kind": CHECKER_COVERAGE_ONLY,
            "confidence": "high",
            "reasons": reasons,
        }

    if text:
        return {
            "kind": CHECKER_ENGINEERING,
            "confidence": "medium",
            "reasons": ["case-specific check.py; no generated coverage harness markers"],
        }

    return {
        "kind": CHECKER_UNKNOWN,
        "confidence": "low",
        "reasons": ["check.py missing or unreadable"],
    }


def selected_ids(set_name: str, scope: str) -> list[str]:
    if scope == "full":
        base = CCX_ROOT / set_name
        return sorted(d.name for d in base.iterdir() if d.is_dir())
    return CURATED_20_SINGLE if set_name == "single" else CURATED_30_CCX_MULTI


def discover_generated(run_dirs: list[Path], set_name: str) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for run_dir in run_dirs:
        base = run_dir / set_name
        if not base.exists():
            continue
        for case_dir in sorted(base.iterdir()):
            if not case_dir.is_dir():
                continue
            step = case_dir / "out.step"
            if (
                (case_dir / "build.py").exists()
                and (case_dir / "meta.json").exists()
                and step.exists()
                and step.stat().st_size > 1024
            ):
                found[case_dir.name] = case_dir
    return found


def prepare_work(set_name: str, sid: str, generated: Path, out_root: Path) -> Path:
    work = out_root / set_name / sid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)

    spec_dir = CCX_ROOT / set_name / sid
    for name in ("analysis_template.inp", "check.py", "spec.json"):
        shutil.copy2(spec_dir / name, work / name)
    for name in ("build.py", "notes.md"):
        src = generated / name
        if src.exists():
            shutil.copy2(src, work / name)
    return work


def legacy_classify(result: dict) -> str:
    if result.get("status") == "missing_generation":
        return "missing_generation"
    check = result.get("check_summary") or {}
    if check.get("fail", 0) > 0:
        return "requirement_failure"
    if result.get("final_rc") == 0:
        return "pass"
    return "infrastructure_failure"


def stage_rc(result: dict[str, Any], stage: str) -> int | None:
    info = (result.get("stages") or {}).get(stage)
    if not isinstance(info, dict):
        return None
    rc = info.get("rc")
    return rc if isinstance(rc, int) else None


def first_failed_precheck_stage(result: dict[str, Any]) -> str | None:
    stages = result.get("stages") or {}
    if not isinstance(stages, dict):
        stages = {}
    for stage in PRECHECK_STAGES:
        rc = stage_rc(result, stage)
        if rc is not None and rc != 0:
            return stage
        if stage not in stages and result.get("final_rc") not in (0, None):
            return stage
    return None


def check_failed(result: dict[str, Any]) -> bool:
    req_summary = result.get("requirement_check_summary")
    if isinstance(req_summary, dict):
        if int(req_summary.get("total") or 0) <= 0:
            return True
        return int(req_summary.get("fail") or 0) > 0 or int(req_summary.get("skip") or 0) > 0

    check = result.get("check_summary") or {}
    if check:
        return int(check.get("fail") or 0) > 0
    rc = stage_rc(result, "check")
    if rc is not None:
        return rc != 0
    return result.get("final_rc") not in (0, None)


def engineering_check_failed(result: dict[str, Any]) -> bool | None:
    summary = result.get("engineering_check_summary")
    if not isinstance(summary, dict):
        return None
    if int(summary.get("total") or 0) <= 0:
        return True
    return int(summary.get("fail") or 0) > 0 or int(summary.get("skip") or 0) > 0


def apply_result_statuses(result: dict[str, Any]) -> dict[str, Any]:
    """Add separated coverage and engineering statuses to one eval result."""
    result["legacy_category"] = legacy_classify(result)

    if result.get("status") == "eval_timeout":
        result["failed_stage"] = "eval_timeout"
        result["coverage_status"] = "infrastructure_failure"
        result["engineering_status"] = "infrastructure_failure"
        result["category"] = "infrastructure_failure"
        return finalize_engineering_plan(result)

    if result.get("status") == "missing_generation":
        result["coverage_status"] = "missing_generation"
        result["engineering_status"] = "missing_generation"
        result["category"] = "missing_generation"
        return finalize_engineering_plan(result)

    failed_stage = first_failed_precheck_stage(result)
    if failed_stage:
        result["failed_stage"] = failed_stage
        result["coverage_status"] = "infrastructure_failure"
        result["engineering_status"] = "infrastructure_failure"
        result["category"] = "infrastructure_failure"
        return finalize_engineering_plan(result)

    checker_kind = (result.get("checker") or {}).get("kind") or CHECKER_UNKNOWN
    failed_check = check_failed(result)

    if checker_kind == CHECKER_COVERAGE_ONLY:
        result["coverage_status"] = "fail" if failed_check else "pass"
        eng_failed = engineering_check_failed(result)
        if failed_check:
            result["engineering_status"] = "fail" if eng_failed is not None else "not_evaluated_coverage_only"
            result["category"] = "coverage_failure"
        elif eng_failed is None:
            result["engineering_status"] = "not_evaluated_coverage_only"
            result["category"] = "coverage_pass_only"
        else:
            result["engineering_status"] = "fail" if eng_failed else "pass"
            result["category"] = "engineering_failure" if eng_failed else "engineering_pass"
        return finalize_engineering_plan(result)

    if checker_kind == CHECKER_ENGINEERING:
        # The CCX stage completed, so solver coverage exists. The case-specific
        # check.py verdict is treated as the engineering requirement result.
        result["coverage_status"] = "pass"
        result["engineering_status"] = "fail" if failed_check else "pass"
        result["category"] = "engineering_failure" if failed_check else "engineering_pass"
        return finalize_engineering_plan(result)

    result["coverage_status"] = "pass" if stage_rc(result, "ccx") == 0 else "unknown"
    result["engineering_status"] = "unknown"
    result["category"] = "unknown_checker"
    return finalize_engineering_plan(result)


def finalize_engineering_plan(result: dict[str, Any]) -> dict[str, Any]:
    plan = result.get("engineering_plan")
    if not isinstance(plan, dict):
        result["per_requirement_results"] = build_per_requirement_results(result)
        return result
    checker_kind = (result.get("checker") or {}).get("kind") or CHECKER_UNKNOWN
    result["engineering_plan"] = apply_evaluation_statuses(
        plan,
        checker_kind=checker_kind,
        coverage_status=str(result.get("coverage_status") or ""),
        status=str(result.get("status") or ""),
    )
    result["engineering_requirement_counts"] = {
        "support": result["engineering_plan"].get("support_counts", {}),
        "evaluation": result["engineering_plan"].get("evaluation_counts", {}),
        "check_type": result["engineering_plan"].get("check_type_counts", {}),
    }
    summary = result.get("engineering_check_summary") or result.get("requirement_check_summary")
    if isinstance(summary, dict):
        result["engineering_requirement_counts"]["verdict"] = {
            "pass": int(summary.get("pass") or 0),
            "fail": int(summary.get("fail") or 0),
            "skip": int(summary.get("skip") or 0),
            "total": int(summary.get("total") or 0),
        }
    result["per_requirement_results"] = build_per_requirement_results(result)
    return result


def natural_requirement_sort_key(requirement_id: str) -> list[int | str]:
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", requirement_id)]


def requirement_plan_lookup(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    plan = result.get("engineering_plan") or {}
    rows = plan.get("requirements") or []
    return {
        str(row.get("requirement_id") or ""): row
        for row in rows
        if row.get("requirement_id") is not None
    }


def _per_requirement_row(
    result: dict[str, Any],
    *,
    requirement_id: str,
    verdict: str,
    source: str,
    plan_row: dict[str, Any] | None = None,
    detail: str = "",
    requirement_class: str = "",
    raw_verdict: str | None = None,
) -> dict[str, Any]:
    verdict = verdict.lower()
    return {
        "set": result.get("set"),
        "item_id": result.get("id"),
        "requirement_id": requirement_id,
        "verdict": verdict,
        "score_pass": verdict == "pass",
        "raw_verdict": raw_verdict or verdict,
        "source": source,
        "detail": detail,
        "requirement_class": requirement_class,
        "checker_kind": (result.get("checker") or {}).get("kind") or CHECKER_UNKNOWN,
        "coverage_status": result.get("coverage_status"),
        "engineering_status": result.get("engineering_status"),
        "category": result.get("category"),
        "failed_stage": result.get("failed_stage"),
        "evaluation_status": (plan_row or {}).get("evaluation_status"),
        "support_status": (plan_row or {}).get("support_status"),
        "check_type": (plan_row or {}).get("check_type"),
        "metric": (plan_row or {}).get("metric"),
        "operator": (plan_row or {}).get("operator"),
        "limit": (plan_row or {}).get("limit"),
        "applies_to": (plan_row or {}).get("applies_to") or [],
    }


def build_per_requirement_results(result: dict[str, Any]) -> list[dict[str, Any]]:
    plan_by_id = requirement_plan_lookup(result)
    rows: list[dict[str, Any]] = []

    if isinstance(result.get("engineering_check_rows"), list) and result["engineering_check_rows"]:
        for check_row in result["engineering_check_rows"]:
            rid = str(check_row.get("requirement_id") or "")
            if not rid:
                continue
            rows.append(
                _per_requirement_row(
                    result,
                    requirement_id=rid,
                    verdict=str(check_row.get("verdict") or "fail"),
                    source="multi_engineering_check.py",
                    plan_row=plan_by_id.get(rid),
                    detail=str(check_row.get("source") or ""),
                    requirement_class=str(check_row.get("requirement_class") or ""),
                )
            )
        return sorted(rows, key=lambda row: natural_requirement_sort_key(str(row["requirement_id"])))

    if isinstance(result.get("requirement_check_rows"), list) and result["requirement_check_rows"]:
        for check_row in result["requirement_check_rows"]:
            rid = str(check_row.get("requirement_id") or "")
            if not rid:
                continue
            rows.append(
                _per_requirement_row(
                    result,
                    requirement_id=rid,
                    verdict=str(check_row.get("verdict") or "fail"),
                    source="case_specific_check.py",
                    plan_row=plan_by_id.get(rid),
                )
            )
        return sorted(rows, key=lambda row: natural_requirement_sort_key(str(row["requirement_id"])))

    fallback_verdict = "pass" if result.get("category") == "engineering_pass" else "fail"
    raw_verdict = fallback_verdict if fallback_verdict == "pass" else "not_run"
    fallback_source = "engineering_plan_fallback" if fallback_verdict == "pass" else "engineering_plan_not_run"
    detail = str(result.get("failed_stage") or result.get("category") or result.get("final_msg") or "")
    for rid, plan_row in sorted(plan_by_id.items(), key=lambda item: natural_requirement_sort_key(item[0])):
        rows.append(
            _per_requirement_row(
                result,
                requirement_id=rid,
                verdict=fallback_verdict,
                source=fallback_source,
                plan_row=plan_row,
                detail=detail,
                raw_verdict=raw_verdict,
            )
        )
    return rows


def flatten_per_requirement_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        per_req = result.get("per_requirement_results")
        if not isinstance(per_req, list):
            per_req = build_per_requirement_results(result)
            result["per_requirement_results"] = per_req
        rows.extend(per_req)
    return rows


PER_CHECK_CSV_COLUMNS = [
    "set",
    "item_id",
    "requirement_id",
    "verdict",
    "score_pass",
    "raw_verdict",
    "source",
    "detail",
    "requirement_class",
    "checker_kind",
    "coverage_status",
    "engineering_status",
    "category",
    "failed_stage",
    "evaluation_status",
    "support_status",
    "check_type",
    "metric",
    "operator",
    "limit",
    "applies_to",
]


def write_per_check_outputs(out_root: Path, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = flatten_per_requirement_results(results)
    (out_root / "per_check_results.json").write_text(json.dumps(rows, indent=2))
    with (out_root / "per_check_results.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=PER_CHECK_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(row.get(key), sort_keys=True)
                    if isinstance(row.get(key), (dict, list))
                    else row.get(key)
                )
                for key in PER_CHECK_CSV_COLUMNS
            })
    return rows


def run_grade(work: Path, driver_log: Path, timeout_s: float | None) -> tuple[int, bool]:
    with driver_log.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, str(GRADE), str(work)],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            log.write(f"\n=== eval timed out after {timeout_s}s; terminating grade process group ===\n")
            log.flush()
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                return proc.wait(timeout=10), True
            except subprocess.TimeoutExpired:
                log.write("\n=== grade process group did not exit after SIGTERM; sending SIGKILL ===\n")
                log.flush()
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                return proc.returncode, True


def run_multi_engineering_check(work: Path, timeout_s: float | None) -> tuple[int | None, bool]:
    log_path = work / "engineering_check.log"
    with log_path.open("w") as log:
        proc = subprocess.Popen(
            [sys.executable, str(MULTI_ENGINEERING_CHECK), str(work)],
            cwd=REPO_ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            return proc.wait(timeout=timeout_s), False
        except subprocess.TimeoutExpired:
            log.write(f"\n=== engineering check timed out after {timeout_s}s ===\n")
            log.flush()
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                return proc.wait(timeout=10), True
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                return proc.returncode, True


def eval_one(
    set_name: str,
    sid: str,
    generated: Path | None,
    out_root: Path,
    timeout_s: float | None,
) -> dict:
    spec_dir = CCX_ROOT / set_name / sid
    spec = read_json(spec_dir / "spec.json")
    checker = inspect_checker(spec_dir)
    engineering_plan = build_engineering_plan(spec)
    if generated is None:
        return apply_result_statuses({
            "set": set_name,
            "id": sid,
            "status": "missing_generation",
            "checker": checker,
            "engineering_plan": engineering_plan,
        })

    work = prepare_work(set_name, sid, generated, out_root)
    driver_log = work / "eval_driver.log"
    started = time.time()
    driver_rc, timed_out = run_grade(work, driver_log, timeout_s)

    grade_path = work / "grade.json"
    grade = json.loads(grade_path.read_text()) if grade_path.exists() else {}
    engineering_check_rc: int | None = None
    engineering_check_timed_out = False
    if (
        set_name == "multi"
        and not timed_out
        and isinstance(grade.get("stages"), dict)
        and ((grade.get("stages") or {}).get("ccx") or {}).get("rc") == 0
    ):
        engineering_timeout = min(timeout_s, 300) if timeout_s else 300
        engineering_check_rc, engineering_check_timed_out = run_multi_engineering_check(
            work,
            engineering_timeout,
        )
    final_msg = grade.get("final_msg", "grade.json missing")
    if timed_out and not grade:
        final_msg = f"eval timed out after {timeout_s}s"
    result = {
        "set": set_name,
        "id": sid,
        "status": "eval_timeout" if timed_out else "evaluated",
        "source": str(generated),
        "workdir": str(work),
        "driver_rc": driver_rc,
        "final_rc": grade.get("final_rc", 124 if timed_out else driver_rc),
        "final_msg": final_msg,
        "stages": grade.get("stages", {}),
        "check_summary": parse_summary(work / "check.log"),
        "check_rows": parse_check_rows(work / "check.log"),
        "requirement_check_summary": (
            parse_requirement_summary(work / "check.log")
            if checker.get("kind") == CHECKER_ENGINEERING
            else None
        ),
        "requirement_check_rows": (
            parse_requirement_rows(work / "check.log")
            if checker.get("kind") == CHECKER_ENGINEERING
            else []
        ),
        "engineering_check_rc": engineering_check_rc,
        "engineering_check_timed_out": engineering_check_timed_out,
        "engineering_check_summary": parse_summary(work / "engineering_check.log"),
        "engineering_check_rows": parse_check_rows(work / "engineering_check.log"),
        "checker": checker,
        "engineering_plan": engineering_plan,
        "elapsed_s": round(time.time() - started, 2),
    }
    return apply_result_statuses(result)


def count_by(results: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        value = str(result.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def count_checker_kinds(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        value = str((result.get("checker") or {}).get("kind") or CHECKER_UNKNOWN)
        counts[value] = counts.get(value, 0) + 1
    return counts


def engineering_verdict(result: dict[str, Any]) -> dict[str, int] | None:
    for key in ("engineering_check_summary", "requirement_check_summary"):
        summary = result.get(key)
        if isinstance(summary, dict):
            return {
                "pass": int(summary.get("pass") or 0),
                "fail": int(summary.get("fail") or 0),
                "skip": int(summary.get("skip") or 0),
                "total": int(summary.get("total") or 0),
            }
    if result.get("category") == "engineering_pass":
        total = int((result.get("engineering_plan") or {}).get("total") or 0)
        return {"pass": total, "fail": 0, "skip": 0, "total": total}
    if result.get("category") == "engineering_failure":
        total = int((result.get("engineering_plan") or {}).get("total") or 0)
        check = result.get("check_summary") or {}
        passed = int(check.get("pass") or 0)
        failed = int(check.get("fail") or max(total - passed, 0))
        if total <= 0:
            total = passed + failed + int(check.get("skip") or 0)
        return {"pass": passed, "fail": failed, "skip": int(check.get("skip") or 0), "total": total}
    total = int((result.get("engineering_plan") or {}).get("total") or 0)
    if total > 0:
        return {"pass": 0, "fail": total, "skip": 0, "total": total}
    return None


def aggregate_engineering_verdict_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    out = {"pass": 0, "fail": 0, "skip": 0, "total": 0}
    for result in results:
        verdict = engineering_verdict(result)
        if verdict is None:
            continue
        for key in out:
            out[key] += int(verdict.get(key) or 0)
    return out


def partial_engineering_score(results: list[dict[str, Any]]) -> dict[str, float | int]:
    ratios: list[float] = []
    passed = failed = skipped = total = 0
    for result in results:
        verdict = engineering_verdict(result)
        if verdict is None:
            continue
        item_total = int(verdict.get("total") or 0)
        item_pass = int(verdict.get("pass") or 0)
        if item_total <= 0:
            continue
        ratios.append(item_pass / item_total)
        passed += item_pass
        failed += int(verdict.get("fail") or 0)
        skipped += int(verdict.get("skip") or 0)
        total += item_total
    return {
        "item_count": len(ratios),
        "mean_item_score": round(sum(ratios) / len(ratios), 6) if ratios else 0.0,
        "aggregate_pass_fraction": round(passed / total, 6) if total else 0.0,
        "pass": passed,
        "fail": failed,
        "skip": skipped,
        "total": total,
    }


def main() -> int:
    run_started = time.time()
    run_started_at = iso_now()
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["single", "multi"], required=True)
    ap.add_argument("--scope", choices=["curated", "full"], default="curated")
    ap.add_argument("--run-dir", action="append", required=True,
                    help="generation timestamp dir; may be repeated")
    ap.add_argument("--out-root", default=None,
                    help="eval output root; defaults to runs/ccx_eval_<set>_<timestamp>")
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument("--only", nargs="*", default=None)
    ap.add_argument("--timeout", type=float, default=0,
                    help="per-case eval timeout in seconds; 0 disables timeout")
    args = ap.parse_args()
    timeout_s = args.timeout if args.timeout and args.timeout > 0 else None

    run_dirs = [Path(p).expanduser().resolve() for p in args.run_dir]
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_root = (
        Path(args.out_root).expanduser().resolve()
        if args.out_root
        else REPO_ROOT / "runs" / f"ccx_eval_{args.set}_{ts}"
    )
    out_root.mkdir(parents=True, exist_ok=True)

    ids = selected_ids(args.set, args.scope)
    if args.only:
        requested = set(args.only)
        ids = [sid for sid in ids if sid in requested]

    generated = discover_generated(run_dirs, args.set)
    print(
        f"Eval plan: {len(ids)} {args.set} ids, {len(generated)} generated "
        f"sources, jobs={args.jobs}, scope={args.scope}, out={out_root}",
        flush=True,
    )

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futures = {
            ex.submit(eval_one, args.set, sid, generated.get(sid), out_root, timeout_s): sid
            for sid in ids
        }
        for fut in as_completed(futures):
            result = fut.result()
            results.append(result)
            print(
                f"  [{result['category']:<24}] "
                f"cov={result.get('coverage_status', '-'):<22} "
                f"eng={result.get('engineering_status', '-'):<28} "
                f"{result['id']:<55} "
                f"rc={result.get('final_rc', '-')!s:<4} "
                f"{result.get('final_msg', '')}",
                flush=True,
            )

    order = {sid: i for i, sid in enumerate(ids)}
    results.sort(key=lambda r: order.get(r["id"], 9999))
    counts: dict[str, int] = {}
    for result in results:
        counts[result["category"]] = counts.get(result["category"], 0) + 1

    per_check_rows = write_per_check_outputs(out_root, results)
    (out_root / "results.json").write_text(json.dumps(results, indent=2))
    run_ended_at = iso_now()
    (out_root / "summary.json").write_text(json.dumps({
        "set": args.set,
        "run_dirs": [str(p) for p in run_dirs],
        "out_root": str(out_root),
        "started_at": run_started_at,
        "ended_at": run_ended_at,
        "elapsed_s": round(time.time() - run_started, 2),
        "timeout_s": timeout_s,
        "counts": counts,
        "coverage_counts": count_by(results, "coverage_status"),
        "engineering_counts": count_by(results, "engineering_status"),
        "checker_counts": count_checker_kinds(results),
        "legacy_counts": count_by(results, "legacy_category"),
        "engineering_requirement_support_counts": aggregate_plan_counts(results, "support_counts"),
        "engineering_requirement_evaluation_counts": aggregate_plan_counts(results, "evaluation_counts"),
        "engineering_requirement_check_type_counts": aggregate_plan_counts(results, "check_type_counts"),
        "engineering_requirement_verdict_counts": aggregate_engineering_verdict_counts(results),
        "partial_engineering_score": partial_engineering_score(results),
        "per_check_result_total": len(per_check_rows),
        "engineering_requirement_total": sum(
            int((result.get("engineering_plan") or {}).get("total") or 0)
            for result in results
        ),
        "category_meaning": {
            "coverage_pass_only": "Generated coverage harness passed; engineering limits were not evaluated.",
            "coverage_failure": "Generated coverage harness did not produce required solver output coverage.",
            "engineering_pass": "Case-specific engineering requirement checker passed.",
            "engineering_failure": "Case-specific engineering requirement checker failed.",
            "infrastructure_failure": "Build, mesh, BC wiring, or CCX failed before requirement checking.",
            "missing_generation": "No usable generated build/out.step/meta.json source was found.",
        },
        "total": len(results),
    }, indent=2))
    print(f"Summary counts: {counts}", flush=True)
    print(f"Coverage counts: {count_by(results, 'coverage_status')}", flush=True)
    print(f"Engineering counts: {count_by(results, 'engineering_status')}", flush=True)
    print(
        "Engineering requirement evaluation counts: "
        f"{aggregate_plan_counts(results, 'evaluation_counts')}",
        flush=True,
    )
    print(f"Results: {out_root / 'results.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
