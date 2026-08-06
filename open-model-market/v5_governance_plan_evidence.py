"""Evidence facade for governance-signed top-50 and expert OR-Tools assignment."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import v5_governance_plan_evidence_legacy as _legacy
from v5_json_io import load_json_or_default, write_json

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

_original_validate_authority = _legacy._validate_authority
_original_request_document = _legacy._request_document
_original_selection_document = _legacy._selection_document
_original_summary_document = _legacy._summary_document
_original_result_document = _legacy._result_document
_original_write_bundle = _legacy._write_bundle


def _top50(plan: Mapping[str, Any]) -> bool:
    return plan.get("selected_from_top50_reasoning_pool_only") is True


def _authority_fields(plan: Mapping[str, Any]) -> dict[str, Any]:
    active = _top50(plan)
    return {
        "candidate_pool_authority": "decision-system-governance",
        "selection_authority": (
            "expert-assessment-center-ortools"
            if active
            else "decision-system-governance"
        ),
        "model_assignment_authority": (
            "expert-assessment-center-ortools"
            if active
            else "decision-system-governance"
        ),
        "model_selection_performed_locally": active,
        "candidate_pool_reranking_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "optimizer_used": active,
        "optimizer": plan.get("optimizer") if active else None,
        "optimizer_optimality_proven": bool(
            plan.get("optimizer_audit", {}).get("optimality_proven")
        ) if active else False,
    }


def _validate_authority(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> None:
    if not _top50(plan):
        _original_validate_authority(source, plan)
        return
    runtime = source["runtime"]
    config = source["runtime_config"]
    selection = source["selection"]
    _legacy._require(
        runtime.get("runtime_version") == _legacy.RUNTIME_VERSION,
        "governance-plan production runtime envelope is missing",
    )
    _legacy._require(
        config.get("candidate_pool_authority") == "decision-system-governance",
        "runtime config does not preserve governance candidate-pool authority",
    )
    _legacy._require(
        config.get("model_assignment_authority")
        == "expert-assessment-center-ortools",
        "runtime config does not preserve expert OR-Tools assignment authority",
    )
    _legacy._require(
        config.get("selection_authority")
        == "expert-assessment-center-ortools",
        "runtime config selection authority mismatch",
    )
    _legacy._require(
        config.get("model_selection_performed_locally") is True,
        "expert runtime does not report top-50 OR-Tools assignment",
    )
    _legacy._require(
        config.get("candidate_pool_reranking_performed_locally") is False,
        "expert runtime reports candidate-pool reranking",
    )
    _legacy._require(
        config.get("optimizer") == "ortools-cp-sat"
        and config.get("optimizer_optimality_proven") is True,
        "runtime config lacks OR-Tools optimality proof",
    )
    _legacy._require(
        config.get("governance_model_plan_sha256") == plan["plan_sha256"],
        "runtime config model plan digest mismatch",
    )
    _legacy._require(
        selection.get("status") == "PASS",
        "top-50 plan materialization did not pass",
    )
    _legacy._require(
        selection.get("candidate_pool_authority") == "decision-system-governance",
        "selection evidence candidate-pool authority mismatch",
    )
    _legacy._require(
        selection.get("model_assignment_authority")
        == "expert-assessment-center-ortools",
        "selection evidence assignment authority mismatch",
    )
    _legacy._require(
        selection.get("model_selection_performed_locally") is True,
        "selection evidence does not report expert OR-Tools assignment",
    )
    _legacy._require(
        selection.get("optimizer_used") is True
        and selection.get("optimizer_optimality_proven") is True,
        "selection evidence lacks OR-Tools optimality proof",
    )
    _legacy._require(
        int(source["governance"].get("actual_governance_calls") or 0) == 0,
        "expert center governance calls must equal zero",
    )


def _fallback_fields(source: Mapping[str, Any]) -> dict[str, Any]:
    fallback = any(
        isinstance(row, Mapping)
        and isinstance(row.get("provider"), Mapping)
        and row["provider"].get("allow_fallbacks") is True
        for row in source["requests"]
    )
    return {
        "provider_fallback_allowed": fallback,
        "provider_fallback_scope": (
            "same-model-audited-qualified-provider-whitelist"
            if fallback
            else "legacy-exact-single-endpoint"
        ),
        "unrestricted_provider_fallback_allowed": False,
        "provider_lock_contract": (
            "legacy-exact-single-endpoint-or-audited-same-model-provider-pool"
        ),
    }


def _request_document(
    source: Mapping[str, Any],
    total: int,
) -> dict[str, Any]:
    value = dict(_original_request_document(source, total))
    plan = source["plan"]
    value.update(_authority_fields(plan))
    value.update(_fallback_fields(source))
    return value


def _selection_document(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    value = dict(_original_selection_document(source, plan))
    value.update(_authority_fields(plan))
    value.update(
        {
            "provider_resolution_only": True,
            "provider_fallback_scope": (
                "same-model-audited-qualified-provider-whitelist"
                if _top50(plan)
                else "legacy-exact-single-endpoint"
            ),
        }
    )
    return value


def _summary_document(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    cost_anomaly_usd: float | None,
    exceeded: bool,
) -> dict[str, Any]:
    value = dict(
        _original_summary_document(
            source,
            plan,
            approved,
            evidence_sha,
            cost_anomaly_usd,
            exceeded,
        )
    )
    value.update(_authority_fields(plan))
    value.update(_fallback_fields(source))
    return value


def _result_document(
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    answer: str,
    actual_cost: float,
) -> dict[str, Any]:
    value = dict(
        _original_result_document(
            source,
            plan,
            approved,
            evidence_sha,
            answer,
            actual_cost,
        )
    )
    value.update(_authority_fields(plan))
    value.update(_fallback_fields(source))
    return value


def _write_bundle(
    root: Path,
    source: Mapping[str, Any],
    plan: Mapping[str, Any],
    approved: Mapping[str, Any],
    evidence_sha: str,
    documents: Mapping[str, Mapping[str, Any]],
) -> None:
    _original_write_bundle(
        root,
        source,
        plan,
        approved,
        evidence_sha,
        documents,
    )
    raw = load_json_or_default(root / "evidence-bundle.json", {})
    bundle = dict(raw) if isinstance(raw, Mapping) else {}
    bundle.update(_authority_fields(plan))
    bundle.update(_fallback_fields(source))
    write_json(root / "evidence-bundle.json", bundle)
    _legacy.write_manifest(root)


_legacy._validate_authority = _validate_authority
_legacy._request_document = _request_document
_legacy._selection_document = _selection_document
_legacy._summary_document = _summary_document
_legacy._result_document = _result_document
_legacy._write_bundle = _write_bundle

RUNTIME_VERSION = _legacy.RUNTIME_VERSION


def normalize_governance_plan_evidence(
    root: Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    return _legacy.normalize_governance_plan_evidence(
        root,
        approved_total_calls=approved_total_calls,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
        require_report=require_report,
    )


__all__ = ["RUNTIME_VERSION", "normalize_governance_plan_evidence"]
