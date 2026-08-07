"""Structural transport compatibility for the active synchronous expert runtime.

The constitutional no-tools filter runs first. This module then removes only routes
that require a different model-plane protocol. In particular, OpenRouter ``:batch``
is asynchronous and cannot be executed by the current synchronous chat-completions
runtime. This is a transport invariant, not a model-quality/business eligibility gate.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "v5-sync-execution-transport-boundary-1"
ACTIVE_TRANSPORT = "openrouter-sync-chat-completions-v1"


def classify_transport(model: str) -> dict[str, Any]:
    value = str(model or "").strip()
    if ":batch" in value.casefold():
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


def partition_sync_transport(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, Any]]:
    executable: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    for row in candidates:
        candidate = dict(row)
        model = str(candidate.get("model") or "").strip()
        classification = classify_transport(model)
        if classification["executable"]:
            candidate["execution_transport"] = ACTIVE_TRANSPORT
            candidate["execution_transport_compatible"] = True
            executable.append(candidate)
            continue
        rejected.append(
            {
                "model": model,
                "reason": str(classification["reason"]),
                "boundary": str(classification["boundary"]),
            }
        )

    audit = {
        "schema_version": SCHEMA_VERSION,
        "active_transport": ACTIVE_TRANSPORT,
        "input_candidate_count": len(candidates),
        "executable_candidate_count": len(executable),
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": rejected,
        "batch_route_supported_by_active_transport": False,
        "batch_base_model_remains_eligible_when_present": True,
        "business_eligibility_gate": False,
        "price_gate": False,
        "company_gate": False,
        "provider_gate": False,
        "popularity_gate": False,
        "model_quality_gate": False,
        "structural_execution_transport_boundary": True,
        "cross_task_history_used": False,
    }
    return executable, rejected, audit


__all__ = [
    "ACTIVE_TRANSPORT",
    "SCHEMA_VERSION",
    "classify_transport",
    "partition_sync_transport",
]
