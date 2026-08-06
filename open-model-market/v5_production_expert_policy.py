"""Production expert request policy and complete failed-result persistence.

Provider routing is completely open. The production prompt policy does not add
ZDR, data-collection, provider allowlists/orders, provider price constraints or
other routing filters. Model identity remains fixed by the expert plan.
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
    """Preserve unrestricted provider routing on production expert requests."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        payload.pop("provider", None)
        assert_request_has_no_tools(
            payload,
            context=f"production expert {node.node_id} request",
        )
        return payload


class EvidenceCompleteExecutionEngine(SoftResourceExecutionEngine):
    """Return failed results only after complete evidence has been persisted."""

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
