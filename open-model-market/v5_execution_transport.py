"""Structural execution-transport compatibility for the active expert runtime.

This is not a business/model-quality gate. Governance may transport every live
OpenRouter candidate, but the current GitHub Actions executor can only execute
synchronous chat-completions identities. Route modifiers that require a different
transport are removed before OR-Tools assignment so they cannot fail later as a
model/tool contract violation.

No-tools remains the only hard *model* boundary. Exact model identity and executable
transport are protocol/structural invariants.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v5-execution-transport-compatibility-1"
ACTIVE_TRANSPORT = "openrouter-sync-chat-completions-v1"


def classify_model_route(model: str) -> dict[str, Any]:
    value = str(model or "").strip()
    folded = value.casefold()
    if not value or "/" not in value:
        return {
            "executable": False,
            "reason": "invalid-model-identity",
            "boundary": "exact-model-identity",
        }
    if folded.startswith("openrouter/"):
        return {
            "executable": False,
            "reason": "openrouter-pseudo-model-not-exact-identity",
            "boundary": "exact-model-identity",
        }
    if ":online" in folded:
        return {
            "executable": False,
            "reason": "online-route-can-enable-external-retrieval",
            "boundary": "no-tools",
        }
    if ":batch" in folded:
        return {
            "executable": False,
            "reason": "batch-route-requires-openrouter-async-batch-transport",
            "boundary": "execution-transport",
        }
    return {
        "executable": True,
        "reason": "",
        "boundary": "",
    }


def filter_executable_candidates(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    counts_by_reason: dict[str, int] = {}
    for row in candidates:
        candidate = dict(row)
        model = str(candidate.get("model") or candidate.get("id") or "").strip()
        classification = classify_model_route(model)
        if classification["executable"]:
            candidate["execution_transport"] = ACTIVE_TRANSPORT
            candidate["execution_transport_compatible"] = True
            accepted.append(candidate)
            continue
        reason = str(classification["reason"])
        boundary = str(classification["boundary"])
        rejected.append(
            {
                "model": model,
                "reason": reason,
                "boundary": boundary,
            }
        )
        counts_by_reason[reason] = counts_by_reason.get(reason, 0) + 1

    audit = {
        "schema_version": SCHEMA_VERSION,
        "active_transport": ACTIVE_TRANSPORT,
        "governance_candidate_count": len(candidates),
        "executable_candidate_count": len(accepted),
        "structurally_excluded_route_count": len(rejected),
        "excluded_counts_by_reason": counts_by_reason,
        "excluded_routes": rejected,
        "business_model_gate_used": False,
        "price_gate_used": False,
        "flagship_gate_used": False,
        "company_gate_used": False,
        "popularity_topn_gate_used": False,
        "provider_gate_used": False,
        "no_tools_model_boundary_preserved": True,
        "execution_transport_is_structural_boundary": True,
        "batch_route_supported_by_active_transport": False,
        "batch_base_models_remain_eligible_when_present": True,
        "cross_task_history_used": False,
    }
    return accepted, audit


__all__ = [
    "ACTIVE_TRANSPORT",
    "SCHEMA_VERSION",
    "classify_model_route",
    "filter_executable_candidates",
]
