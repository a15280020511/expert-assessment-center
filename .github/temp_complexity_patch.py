from __future__ import annotations

from pathlib import Path

path = Path("open-model-market/v5_independent_artifact_revalidation.py")
text = path.read_text(encoding="utf-8")
old = '''def _audited_degraded_delivery(
    result: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> bool:
    if result.get("status") != "success" or summary.get("status") != "success":
        return False
    if result.get("completion_mode") != "degraded" or summary.get("completion_mode") != "degraded":
        return False
    if result.get("quality_status") != "degraded_success" or summary.get("quality_status") != "degraded_success":
        return False
    if not str(result.get("final_answer") or "").strip():
        return False
    integrity = summary.get("quality_integrity")
    delivery = summary.get("delivery_policy")
    coverage = summary.get("work_coverage")
    if not isinstance(integrity, Mapping) or integrity.get("status") != "DEGRADED":
        return False
    if not isinstance(delivery, Mapping) or not isinstance(coverage, Mapping):
        return False
    if delivery.get("allow_degraded_success") is not True:
        return False
    if delivery.get("blockers") or delivery.get("missing_non_degradable_work_ids"):
        return False
    try:
        observed = float(coverage.get("coverage_ratio") or 0.0)
        minimum = float(coverage.get("minimum_degraded_coverage") or 1.0)
        strict_nodes = int(coverage.get("successful_content_nodes") or 0)
    except (TypeError, ValueError):
        return False
    return observed + 1e-12 >= minimum and strict_nodes >= 1
'''
new = '''def _degraded_status_matches(
    result: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> bool:
    return all(
        (
            result.get("status") == "success",
            summary.get("status") == "success",
            result.get("completion_mode") == "degraded",
            summary.get("completion_mode") == "degraded",
            result.get("quality_status") == "degraded_success",
            summary.get("quality_status") == "degraded_success",
        )
    )


def _degraded_policy_evidence(
    summary: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    integrity = summary.get("quality_integrity")
    delivery = summary.get("delivery_policy")
    coverage = summary.get("work_coverage")
    if not isinstance(integrity, Mapping):
        return None
    if integrity.get("status") != "DEGRADED":
        return None
    if not isinstance(delivery, Mapping):
        return None
    if not isinstance(coverage, Mapping):
        return None
    if delivery.get("allow_degraded_success") is not True:
        return None
    if delivery.get("blockers"):
        return None
    if delivery.get("missing_non_degradable_work_ids"):
        return None
    return delivery, coverage


def _degraded_coverage_is_sufficient(
    coverage: Mapping[str, Any],
) -> bool:
    try:
        observed = float(coverage.get("coverage_ratio") or 0.0)
        minimum = float(coverage.get("minimum_degraded_coverage") or 1.0)
        strict_nodes = int(coverage.get("successful_content_nodes") or 0)
    except (TypeError, ValueError):
        return False
    coverage_met = observed + 1e-12 >= minimum
    strict_content_exists = strict_nodes >= 1
    return all((coverage_met, strict_content_exists))


def _audited_degraded_delivery(
    result: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> bool:
    if not _degraded_status_matches(result, summary):
        return False
    if not str(result.get("final_answer") or "").strip():
        return False
    evidence = _degraded_policy_evidence(summary)
    if evidence is None:
        return False
    _, coverage = evidence
    return _degraded_coverage_is_sufficient(coverage)
'''
if old not in text:
    raise SystemExit("complexity patch anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
