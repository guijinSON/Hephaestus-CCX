#!/usr/bin/env python3
"""Classify CCX requirements by engineering-check feasibility.

The generated multipart CCX kits are coverage harnesses.  This module does not
pretend to certify those requirements; it builds an explicit per-requirement
plan that says which requirements are straightforward to check once real
loads/selectors exist, which need extra metadata, and which need physics beyond
the generic CalculiX smoke deck.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


SUPPORT_AUTO = "auto_ccx_supported"
SUPPORT_NEEDS_METADATA = "needs_metadata_or_model"
SUPPORT_UNSUPPORTED = "unsupported_physics"
SUPPORT_MANUAL = "manual_review"

EVAL_NOT_EVALUATED = "not_evaluated"
EVAL_REQUIRES_BINDING = "not_evaluated_requires_case_load_binding"
EVAL_NEEDS_METADATA = "not_evaluated_needs_metadata_or_model"
EVAL_UNSUPPORTED = "unsupported_physics"
EVAL_MANUAL = "manual_review"
EVAL_INFRASTRUCTURE = "not_evaluated_infrastructure_failure"
EVAL_COVERAGE_FAILED = "not_evaluated_coverage_failed"
EVAL_MISSING_GENERATION = "not_evaluated_missing_generation"
EVAL_CASE_CHECKER = "evaluated_by_case_specific_checker"

LIMIT_KEYS = (
    "limit",
    "threshold",
    "limit_MPa",
    "limit_mm",
    "limit_kg",
    "limit_Hz",
    "limit_N",
    "limit_bar",
    "limit_g",
    "limit_kPa",
    "limit_m",
    "limit_kN",
    "limit_kg_m2",
    "limit_fraction_of_yield",
    "allowable",
    "minimum",
    "maximum",
    "min",
    "max",
)


def requirement_id(req: dict[str, Any], index: int) -> str:
    return str(req.get("id") or f"REQ_{index}")


def requirement_text(req: dict[str, Any]) -> str:
    fields = (
        req.get("id"),
        req.get("type"),
        req.get("metric"),
        req.get("description"),
        req.get("note"),
        req.get("derivation"),
        req.get("scope"),
    )
    return " ".join(str(value) for value in fields if value is not None).lower()


def is_generated_coverage_requirement(req: dict[str, Any], rid: str) -> bool:
    return (
        req.get("generated_by") == "ccx_multipart_coverage_generator"
        or rid.startswith("R_GEOMETRY_COVERAGE")
        or rid.startswith("R_FEA_COVERAGE")
        or str(req.get("metric") or "") == "generated_static_response_norm"
    )


def source_requirements(spec: dict[str, Any]) -> list[dict[str, Any]]:
    reqs = (spec.get("requirements") or {}).get("pass_fail_criteria") or []
    reqs = [req for req in reqs if isinstance(req, dict)]

    coverage = spec.get("generated_eval_coverage") or {}
    source_ids = {
        str(rid) for rid in coverage.get("source_requirement_ids") or []
    } if isinstance(coverage, dict) else set()
    if source_ids:
        return [
            req for index, req in enumerate(reqs)
            if requirement_id(req, index) in source_ids
        ]

    return [
        req for index, req in enumerate(reqs)
        if not is_generated_coverage_requirement(req, requirement_id(req, index))
    ]


def limit_descriptor(req: dict[str, Any]) -> dict[str, Any] | None:
    for key in LIMIT_KEYS:
        if key in req:
            return {"key": key, "value": req.get(key)}
    return None


def classify_requirement(req: dict[str, Any]) -> dict[str, str]:
    text = requirement_text(req)

    if any(term in text for term in (
        "random", "psd", "rms", "3-sigma", "3sigma", "miles",
        "srs", "shock", "fatigue", "miner",
    )):
        return {
            "support_status": SUPPORT_UNSUPPORTED,
            "check_type": "random_dynamic",
            "reason": "requires random/dynamic response processing beyond the generic CCX static/modal deck",
        }

    if any(term in text for term in (
        "tsai", "composite", "ply", "fiber", "fibre", "matrix",
        "failure_index", "first-ply", "first_ply", "strain",
    )):
        return {
            "support_status": SUPPORT_UNSUPPORTED,
            "check_type": "composite_failure",
            "reason": "requires laminate/composite failure post-processing and material axes",
        }

    if any(term in text for term in ("autofrettage", "residual", "plastic", "permanent")):
        return {
            "support_status": SUPPORT_UNSUPPORTED,
            "check_type": "nonlinear_residual",
            "reason": "requires nonlinear load history or residual-stress evaluation",
        }

    if any(term in text for term in ("thermal", "temperature", "heat", "cavity", "collector")):
        return {
            "support_status": SUPPORT_NEEDS_METADATA,
            "check_type": "thermal_model",
            "reason": "requires a case-specific thermal field/model and thermal post-processing region",
        }

    if any(term in text for term in (
        "dcr", "connection", "weld", "bolt", "gap", "separation",
        "preload", "interface", "contact", "slip", "bond", "adhesive",
        "anchor", "fastener", "joint", "peel",
    )):
        return {
            "support_status": SUPPORT_NEEDS_METADATA,
            "check_type": "connection_contact",
            "reason": "requires interface/fastener/weld/contact selectors and a demand-capacity formula",
        }

    if any(term in text for term in (
        "frequency", "natural_frequency", "modal", "mode_hz", "eigenfrequency",
    )):
        return {
            "support_status": SUPPORT_AUTO,
            "check_type": "modal_frequency",
            "reason": "can be checked from modal/eigenfrequency output once modal BCs are bound to the case",
        }

    if any(term in text for term in ("buckl", "load_factor", "eigenvalue")):
        return {
            "support_status": SUPPORT_AUTO,
            "check_type": "buckling_factor",
            "reason": "can be checked from buckling eigenvalue output once the governing load case is bound",
        }

    if any(term in text for term in ("mass", "dry_mass", "weight", "areal_density")):
        return {
            "support_status": SUPPORT_AUTO,
            "check_type": "mass_geometry",
            "reason": "can be checked from generated geometry/mesh volume and material density metadata",
        }

    if any(term in text for term in ("deflection", "displacement", "clearance", "flatness")):
        return {
            "support_status": SUPPORT_NEEDS_METADATA,
            "check_type": "measurement_selector",
            "reason": "requires a node/region selector for the measurement location and load case",
        }

    if any(term in text for term in ("stress", "von_mises", "vm", "yield", "principal")):
        return {
            "support_status": SUPPORT_AUTO,
            "check_type": "global_stress_if_loads_valid",
            "reason": "can be checked from stress output after real load magnitudes, BCs, materials, and regions are bound",
        }

    if any(term in text for term in (
        "geometry", "geometric", "diameter", "thickness", "bbox", "bounding",
        "count", "envelope", "width", "height", "length", "od_mm", "id_mm",
    )):
        return {
            "support_status": SUPPORT_AUTO,
            "check_type": "geometry",
            "reason": "can be checked from CAD/mesh geometry metadata",
        }

    return {
        "support_status": SUPPORT_MANUAL,
        "check_type": "manual_review",
        "reason": "metric is not mapped to an automated engineering checker yet",
    }


def build_engineering_plan(spec: dict[str, Any]) -> dict[str, Any]:
    coverage = spec.get("generated_eval_coverage") or {}
    source_ids = (
        [str(rid) for rid in coverage.get("source_requirement_ids") or []]
        if isinstance(coverage, dict)
        else []
    )

    rows: list[dict[str, Any]] = []
    for index, req in enumerate(source_requirements(spec)):
        rid = requirement_id(req, index)
        classification = classify_requirement(req)
        rows.append(
            {
                "requirement_id": rid,
                "type": req.get("type"),
                "metric": req.get("metric"),
                "operator": req.get("operator"),
                "limit": limit_descriptor(req),
                "applies_to": req.get("applies_to") or [],
                "scope": req.get("scope"),
                "support_status": classification["support_status"],
                "check_type": classification["check_type"],
                "support_reason": classification["reason"],
                "evaluation_status": EVAL_NOT_EVALUATED,
            }
        )

    return finalize_plan_counts(
        {
            "source": (
                "generated_eval_coverage.source_requirement_ids"
                if source_ids else
                "requirements.pass_fail_criteria"
            ),
            "source_requirement_ids": source_ids,
            "total": len(rows),
            "requirements": rows,
        }
    )


def evaluation_status_for_requirement(
    row: dict[str, Any],
    *,
    checker_kind: str,
    coverage_status: str,
    status: str,
) -> str:
    if status == "missing_generation":
        return EVAL_MISSING_GENERATION
    if coverage_status == "infrastructure_failure":
        return EVAL_INFRASTRUCTURE
    if coverage_status == "fail":
        return EVAL_COVERAGE_FAILED
    if checker_kind == "engineering_requirements":
        return EVAL_CASE_CHECKER

    support = row.get("support_status")
    if support == SUPPORT_AUTO:
        return EVAL_REQUIRES_BINDING
    if support == SUPPORT_NEEDS_METADATA:
        return EVAL_NEEDS_METADATA
    if support == SUPPORT_UNSUPPORTED:
        return EVAL_UNSUPPORTED
    return EVAL_MANUAL


def apply_evaluation_statuses(
    plan: dict[str, Any],
    *,
    checker_kind: str,
    coverage_status: str,
    status: str,
) -> dict[str, Any]:
    rows = []
    for row in plan.get("requirements") or []:
        updated = dict(row)
        updated["evaluation_status"] = evaluation_status_for_requirement(
            updated,
            checker_kind=checker_kind,
            coverage_status=coverage_status,
            status=status,
        )
        rows.append(updated)

    out = dict(plan)
    out["requirements"] = rows
    return finalize_plan_counts(out)


def finalize_plan_counts(plan: dict[str, Any]) -> dict[str, Any]:
    rows = plan.get("requirements") or []
    plan["total"] = len(rows)
    plan["support_counts"] = dict(Counter(str(row.get("support_status") or "unknown") for row in rows))
    plan["check_type_counts"] = dict(Counter(str(row.get("check_type") or "unknown") for row in rows))
    plan["evaluation_counts"] = dict(Counter(str(row.get("evaluation_status") or EVAL_NOT_EVALUATED) for row in rows))
    return plan


def aggregate_plan_counts(results: list[dict[str, Any]], count_key: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for result in results:
        plan = result.get("engineering_plan") or {}
        for key, value in (plan.get(count_key) or {}).items():
            counts[str(key)] += int(value)
    return dict(counts)

