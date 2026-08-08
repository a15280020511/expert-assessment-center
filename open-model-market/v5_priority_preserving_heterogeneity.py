"""Preserve quality/risk priority while making recovery finite and heterogeneous.

Standby selection keeps the production priority order:
1. current-run quality / failure risk;
2. company heterogeneity on a true quality-risk tie;
3. cost and stable model identity from the existing reranked inventory.

Run #396 also proved that deriving one recovery wave directly from sqrt(standby)
can promote dozens of models. The active batch depth is therefore derived from the
number of *distinct currently eligible companies* and current-run failure pressure.
This is a dynamic finite exploration batch, not a fixed call or company-count gate.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from v5_model_company import canonical_model_company
from v5_run387_hardening import HeterogeneousEvidenceExecutionEngine
from v5_runtime import ProductionRuntime


def first_ranked_eligible_standby(
    inventory: Sequence[Mapping[str, Any]],
    claimed: set[str],
    hard_failed: set[str],
) -> dict[str, Any] | None:
    for row in inventory:
        model = str(row.get("model") or "").strip()
        if not model or model in claimed or model in hard_failed:
            continue
        return dict(row)
    return None


def _number(row: Mapping[str, Any], key: str, fallback: float) -> float:
    try:
        value = float(row.get(key, fallback))
    except (TypeError, ValueError):
        return fallback
    return value if math.isfinite(value) else fallback


def _quality_risk_key(row: Mapping[str, Any]) -> tuple[float, float]:
    """Return only the two higher-priority observable dimensions."""
    return (
        -_number(row, "estimated_quality", 0.0),
        _number(row, "failure_probability", 1.0),
    )


def first_equivalent_quality_heterogeneous_standby(
    inventory: Sequence[Mapping[str, Any]],
    claimed: set[str],
    hard_failed: set[str],
    tried_companies: set[str],
) -> dict[str, Any] | None:
    """Prefer a new company only inside the first quality/risk equivalence class."""
    eligible = [
        dict(row)
        for row in inventory
        if str(row.get("model") or "").strip()
        and str(row.get("model") or "").strip() not in claimed
        and str(row.get("model") or "").strip() not in hard_failed
    ]
    if not eligible:
        return None
    first_key = _quality_risk_key(eligible[0])
    equivalent = [row for row in eligible if _quality_risk_key(row) == first_key]
    for row in equivalent:
        company = canonical_model_company(str(row.get("model") or ""))
        if company and company != "unknown" and company not in tried_companies:
            return row
    return eligible[0]


class PriorityPreservingHeterogeneousExecutionEngine(HeterogeneousEvidenceExecutionEngine):
    """Use small current-run recovery batches without weakening priority order."""

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            chosen = first_equivalent_quality_heterogeneous_standby(
                self._standby_inventory,
                self._standby_claimed,
                self._hard_failed_model_ids,
                set(self._attempted_company_sequence),
            )
            if chosen is None:
                return None
            model = str(chosen.get("model") or "").strip()
            self._standby_claimed.add(model)
            return chosen

    def _dynamic_promotion_depth(self, node_attempts: Sequence[Any]) -> int:
        """Derive one finite exploration batch from company breadth and live pressure."""
        parent_depth = int(super()._dynamic_promotion_depth(node_attempts))
        if parent_depth <= 0:
            return 0
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            eligible = [
                row
                for row in self._standby_inventory
                if str(row.get("model") or "").strip()
                and str(row.get("model") or "").strip() not in self._standby_claimed
                and str(row.get("model") or "").strip() not in self._hard_failed_model_ids
            ]
            if not eligible:
                return 0
            companies = {
                canonical_model_company(str(row.get("model") or ""))
                for row in eligible
            }
            companies.discard("")
            companies.discard("unknown")
            distinct_companies = max(1, len(companies))
            attempts = max(1, int(self._feedback_attempts))
            failure_rate = self._feedback_failures / attempts
            quality_rate = self._feedback_quality_failures / attempts
            node_failures = sum(
                1
                for row in node_attempts
                if str(getattr(row, "status", "")) != "passed"
            )
            node_pressure = min(1.0, node_failures / max(1, self._feedback_primary_count))
            feedback_pressure = min(
                1.0,
                max(failure_rate, quality_rate, node_pressure),
            )
            company_entropy_breadth = max(1, math.ceil(math.log2(distinct_companies + 1)))
            batch = max(1, math.ceil(company_entropy_breadth * feedback_pressure))
            self._last_dynamic_promotion_batch = {
                "eligible_standby_count": len(eligible),
                "eligible_distinct_company_count": distinct_companies,
                "company_entropy_breadth": company_entropy_breadth,
                "feedback_pressure": round(feedback_pressure, 6),
                "parent_structural_depth": parent_depth,
                "effective_batch_depth": min(parent_depth, len(eligible), batch),
                "fixed_call_ceiling_used": False,
                "fixed_company_count_used": False,
                "source": "current-eligible-company-entropy-and-current-run-feedback",
            }
            return min(parent_depth, len(eligible), batch)

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        value["standby_claim_preserves_reranked_priority"] = True
        value["standby_priority_order"] = [
            "current-run-quality-or-failure-risk",
            "company-heterogeneity-on-quality-risk-tie",
            "cost",
            "stable-model-identity",
        ]
        value["company_diversity_overrides_higher_priority_candidate"] = False
        value["dynamic_promotion_batch"] = dict(
            getattr(self, "_last_dynamic_promotion_batch", {})
        )
        value["promotion_batch_is_fixed_call_ceiling"] = False
        return value


def install_priority_preserving_heterogeneity(runtime: ProductionRuntime) -> ProductionRuntime:
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
    "first_equivalent_quality_heterogeneous_standby",
    "first_ranked_eligible_standby",
    "install_priority_preserving_heterogeneity",
]
