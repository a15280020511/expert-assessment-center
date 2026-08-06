"""Top-50 evidence facade for zero-governance expert execution."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import v5_price_ranked_evidence_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

_original_request_document = _legacy._request_document
_original_selection_document = _legacy._selection_document
_original_routing_document = _legacy._routing_document


def _top50(source: Any) -> bool:
    ticket = source.ticket if isinstance(source.ticket, Mapping) else {}
    return (
        ticket.get("optimizer") == "ortools-cp-sat"
        or int(ticket.get("top50_reasoning_pool_size") or 0) == 50
    )


def _request_document(source: Any, approved: Any) -> dict[str, Any]:
    value = dict(_original_request_document(source, approved))
    fallback_allowed = any(
        isinstance(row, Mapping)
        and isinstance(row.get("provider"), Mapping)
        and row["provider"].get("allow_fallbacks") is True
        for row in source.requests
    )
    value.update(
        {
            "provider_fallback_allowed": fallback_allowed,
            "provider_fallback_scope": (
                "same-model-audited-qualified-provider-whitelist"
                if fallback_allowed
                else "legacy-exact-single-endpoint"
            ),
            "unrestricted_provider_fallback_allowed": False,
            "provider_lock_contract": (
                "legacy-exact-single-endpoint-or-audited-same-model-provider-pool"
            ),
        }
    )
    return value


def _selection_document(source: Any, prepared: Any) -> dict[str, Any]:
    value = dict(_original_selection_document(source, prepared))
    active = _top50(source)
    value.update(
        {
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
            "optimizer_used": active,
            "optimizer": "ortools-cp-sat" if active else None,
            "optimizer_optimality_proven": bool(
                source.ticket.get("optimizer_optimality_proven")
            ) if active else False,
        }
    )
    return value


def _routing_document() -> dict[str, Any]:
    value = dict(_original_routing_document())
    value.update(
        {
            "mode": "signed-candidate-pool-ortools-networkx-dag",
            "candidate_pool_authority": "decision-system-governance",
            "model_assignment_authority": "expert-assessment-center-ortools",
            "provider_fallback_scope": (
                "same-model-audited-qualified-provider-whitelist"
            ),
            "unrestricted_provider_fallback_allowed": False,
        }
    )
    return value


_legacy._request_document = _request_document
_legacy._selection_document = _selection_document
_legacy._routing_document = _routing_document

RUNTIME_VERSION = _legacy.RUNTIME_VERSION
ApprovedContext = _legacy.ApprovedContext
EvidenceSource = _legacy.EvidenceSource
PreparedEvidence = _legacy.PreparedEvidence


def normalize_price_ranked_evidence(
    root: Path,
    *,
    approved_total_calls: int,
    approved_recovery_calls: int,
    cost_anomaly_usd: float | None,
    require_report: bool,
) -> dict[str, Any]:
    return _legacy.normalize_price_ranked_evidence(
        root,
        approved_total_calls=approved_total_calls,
        approved_recovery_calls=approved_recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
        require_report=require_report,
    )


__all__ = [
    "RUNTIME_VERSION",
    "ApprovedContext",
    "EvidenceSource",
    "PreparedEvidence",
    "normalize_price_ranked_evidence",
]
