"""Preserve quality/risk priority after heterogeneous standby reranking.

The run-387 heterogeneity engine already reranks standby candidates by observable
current-run quality/failure risk, then untried company, then cost. Claiming must
consume that ranked order directly. Searching the entire inventory for any new
company would incorrectly let diversity outrank a materially stronger/safer model.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_run387_hardening import HeterogeneousEvidenceExecutionEngine
from v5_runtime import ProductionRuntime


def first_ranked_eligible_standby(
    inventory: Sequence[Mapping[str, Any]],
    claimed: set[str],
    hard_failed: set[str],
) -> dict[str, Any] | None:
    """Return the first eligible row without bypassing the established ranking."""
    for row in inventory:
        model = str(row.get("model") or "").strip()
        if not model or model in claimed or model in hard_failed:
            continue
        return dict(row)
    return None


class PriorityPreservingHeterogeneousExecutionEngine(
    HeterogeneousEvidenceExecutionEngine
):
    """Consume heterogeneous standby order without reordering at claim time."""

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            chosen = first_ranked_eligible_standby(
                self._standby_inventory,
                self._standby_claimed,
                self._hard_failed_model_ids,
            )
            if chosen is None:
                return None
            model = str(chosen.get("model") or "").strip()
            self._standby_claimed.add(model)
            return chosen

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        value["standby_claim_preserves_reranked_priority"] = True
        value["standby_priority_order"] = [
            "current-run-quality-or-failure-risk",
            "company-heterogeneity",
            "cost",
            "stable-model-identity",
        ]
        value["company_diversity_overrides_higher_priority_candidate"] = False
        return value


def install_priority_preserving_heterogeneity(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    """Replace only the execution-engine instance; inherit all installed gates."""
    runtime.execution_engine = PriorityPreservingHeterogeneousExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "PriorityPreservingHeterogeneousExecutionEngine",
    "first_ranked_eligible_standby",
    "install_priority_preserving_heterogeneity",
]
