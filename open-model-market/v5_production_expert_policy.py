"""Production expert request and delivery policy.

Provider routing is completely open. Production requests contain no Provider
allowlist/order/ZDR/data-collection/price routing filters. Company identity is
retained as audit telemetry only and never invalidates an otherwise valid run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_production_answer_normalization import relabel_task_derived_fact_lines
from v5_runtime import ProductionRuntime, RuntimeAttempt, extract_actual_cost
from v5_soft_resource_governance import (
    SoftResourceExecutionEngine,
    SoftResourcePromptPolicy,
)
from v5_task_constraints import TaskConstraints

EXPERT_DATA_COLLECTION_POLICY = None
EXPERT_ZDR_REQUIRED = False


class ProductionExpertPromptPolicy(SoftResourcePromptPolicy):
    """Guarantee unrestricted OpenRouter Provider routing at send time."""

    provider_lock_required = False

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        # Historical prompt builders may construct a compatibility Provider
        # object internally. It is never sent to OpenRouter in production.
        payload.pop("provider", None)
        assert_request_has_no_tools(
            payload,
            context=f"production expert {node.node_id} request",
        )
        return payload


class EvidenceCompleteExecutionEngine(SoftResourceExecutionEngine):
    """Persist complete evidence without company/provider business gates."""

    def _normalize_attempt(
        self,
        node: SelectedNode,
        original_task: str,
        attempt: RuntimeAttempt,
        constraints: TaskConstraints,
    ) -> bool:
        earliest = attempt.raw_answer or attempt.answer
        if attempt.answer:
            repaired, audit = relabel_task_derived_fact_lines(
                original_task,
                attempt.answer,
            )
            if audit.get("applied"):
                if attempt.raw_answer is None:
                    attempt.raw_answer = attempt.answer
                attempt.answer = repaired
                attempt.answer_transformations.append(audit)
        normalized = super()._normalize_attempt(
            node,
            original_task,
            attempt,
            constraints,
        )
        if earliest and attempt.raw_answer != earliest:
            attempt.raw_answer = earliest
        return normalized

    @staticmethod
    def _actual_cost(response: Mapping[str, Any]) -> float:
        return extract_actual_cost(response)

    @staticmethod
    def _raise_failed_result(result: Mapping[str, Any]) -> None:
        del result

    @classmethod
    def _actual_company_audit(
        cls,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        audit = dict(super()._actual_company_audit(result))
        audit.update(
            {
                "status": "PASS",
                "policy": "audit-only-company-observability",
                "company_uniqueness_constraint": False,
                "duplicate_companies_allowed": True,
                "duplicates_invalidate_execution": False,
            }
        )
        return audit

    @staticmethod
    def _constitutional_failure_reason(
        result: Mapping[str, Any],
        company_audit: Mapping[str, Any],
        evidence_audit: Mapping[str, Any],
        constraints: TaskConstraints,
    ) -> str | None:
        del company_audit
        if evidence_audit["status"] != "PASS":
            return "unsupported-evidence-or-quantity"
        if (
            result.get("completion_mode") == "degraded"
            and not constraints.allow_degraded_success
        ):
            return "degradation-not-authorized-by-user"
        return None

    @classmethod
    def _fail_constitutional_result(
        cls,
        result: dict[str, Any],
        root: Path | None,
        reason: str,
    ) -> None:
        result.update(
            {
                "status": "failed",
                "completion_mode": "none",
                "quality_status": "failed",
                "final_answer": None,
                "stop_reason": reason,
            }
        )
        cls._write_execution_summary(root, result)
        if root is not None:
            (root / "v5-final-report.md").write_text(
                "# V5 execution failed\n\n"
                f"Constitutional final gate: {reason}.\n",
                encoding="utf-8",
            )


def install_production_expert_policy(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    runtime.prompt_policy = ProductionExpertPromptPolicy()
    runtime.execution_engine = EvidenceCompleteExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "EXPERT_DATA_COLLECTION_POLICY",
    "EXPERT_ZDR_REQUIRED",
    "EvidenceCompleteExecutionEngine",
    "ProductionExpertPromptPolicy",
    "install_production_expert_policy",
]
