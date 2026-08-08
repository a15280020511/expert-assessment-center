"""Cost-effectiveness-first current-run recovery ordering.

The engine preserves replacement-truncation same-model rebind and continuous
spatiotemporal feedback, but changes only the soft candidate ordering:
quality/current failure risk -> cost -> company heterogeneity -> stable identity.
No cost threshold, company gate, Provider restriction or cross-task history exists.
"""
from __future__ import annotations

from typing import Any, Mapping

from v5_model_company import canonical_model_company
from v5_replacement_truncation_rebind import (
    ReplacementTruncationRebindExecutionEngine,
)
from v5_runtime import FailureCategory, ProductionRuntime


class CostEffectiveContinuousExecutionEngine(
    ReplacementTruncationRebindExecutionEngine
):
    """Prefer lower expected spend when quality/risk signals tie."""

    @classmethod
    def _failure_rank_key(
        cls,
        row: Mapping[str, Any],
        category: Any,
        tried_companies: set[str],
    ) -> tuple[Any, ...]:
        category_value = str(getattr(category, "value", category))
        quality = cls._number(row, "estimated_quality", 0.0)
        failure = cls._number(row, "failure_probability", 1.0)
        if bool(row.get("estimated_task_cost_available")):
            cost = cls._number(row, "estimated_task_cost_usd", 0.0)
            cost_source = "estimated-task-cost-usd"
        elif row.get("cost_rank_signal") not in {None, ""}:
            cost = cls._number(row, "cost_rank_signal", 0.0)
            cost_source = "catalog-price-rank-signal"
        else:
            cost = cls._number(row, "estimated_cost", 0.0)
            cost_source = "compatibility-estimated-cost"
        model = str(row.get("model") or "")
        company = canonical_model_company(model)
        repeated = int(company in tried_companies and company != "unknown")
        # Keep source only as a deterministic final discriminator so rows with an
        # equal numeric signal retain a stable ordering without changing the
        # quality/risk/cost/company priority hierarchy.
        if category_value == FailureCategory.QUALITY_GATE_FAILED.value:
            return (-quality, failure, cost, repeated, cost_source, model)
        return (failure, -quality, cost, repeated, cost_source, model)

    def _rerank_standby_for_failure(self, category: Any) -> None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            before = [
                str(row.get("model") or "")
                for row in self._standby_inventory
                if str(row.get("model") or "") not in self._standby_claimed
            ]
            tried = set(self._attempted_company_sequence)
            self._standby_inventory = sorted(
                (dict(row) for row in self._standby_inventory),
                key=lambda row: self._failure_rank_key(row, category, tried),
            )
            after = [
                str(row.get("model") or "")
                for row in self._standby_inventory
                if str(row.get("model") or "") not in self._standby_claimed
            ]
            self._standby_rerank_events.append(
                {
                    "trigger_category": str(
                        getattr(category, "value", category)
                    ),
                    "candidate_count": len(after),
                    "order_changed": before != after,
                    "top_before": before[:8],
                    "top_after": after[:8],
                    "policy": (
                        "current-quality-risk-then-preserved-cost-signal-then-"
                        "company-heterogeneity"
                    ),
                    "standby_catalog_price_signal_supported": True,
                    "cost_effectiveness_priority": True,
                    "cost_is_execution_gate": False,
                    "company_diversity_is_execution_gate": False,
                    "cross_task_history_used": False,
                }
            )

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        value.update(
            {
                "cost_effectiveness_priority": True,
                "runtime_candidate_priority": [
                    "current-task-quality-and-current-failure-risk",
                    "current-task-expected-cost-or-preserved-catalog-price-signal",
                    "company-heterogeneity-on-higher-priority-tie",
                    "stable-model-identity",
                ],
                "standby_catalog_price_signal_supported": True,
                "token_and_cost_soft_control": True,
                "cost_is_execution_gate": False,
                "company_diversity_is_execution_gate": False,
                "continuous_spatiotemporal_resource_recomputation": True,
            }
        )
        return value


def install_cost_effective_continuous_runtime(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    runtime.execution_engine = CostEffectiveContinuousExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "CostEffectiveContinuousExecutionEngine",
    "install_cost_effective_continuous_runtime",
]
