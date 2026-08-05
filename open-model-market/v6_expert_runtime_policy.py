"""V6 production expert request policy and complete failed-result persistence."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_production_answer_normalization import relabel_task_derived_fact_lines
from v5_runtime import ProductionRuntime, RuntimeAttempt, extract_actual_cost
from v5_task_constraints import TaskConstraints
from v6_resource_runtime import V6ResourceExecutionEngine, V6ResourcePromptPolicy

EXPERT_DATA_COLLECTION_POLICY = "deny"
EXPERT_ZDR_REQUIRED = True


class V6ExpertPromptPolicy(V6ResourcePromptPolicy):
    """Apply explicit no-tool, no-retention and exact-provider request controls."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise RuntimeError("provider lock missing from V6 expert request")
        locked = dict(provider)
        only = locked.get("only")
        order = locked.get("order")
        if (
            not isinstance(only, list)
            or not isinstance(order, list)
            or len(only) != 1
            or only != order
        ):
            raise RuntimeError("V6 expert request must use one exact provider lock")
        if locked.get("allow_fallbacks") is not False:
            raise RuntimeError("V6 expert provider fallback must be disabled")
        locked["data_collection"] = EXPERT_DATA_COLLECTION_POLICY
        locked["zdr"] = EXPERT_ZDR_REQUIRED
        payload["provider"] = locked
        assert_request_has_no_tools(
            payload,
            context=f"V6 production expert {node.node_id} request",
        )
        return payload


class V6EvidenceCompleteExecutionEngine(V6ResourceExecutionEngine):
    """Persist failed evidence completely before the production wrapper fails."""

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
                "# V6 execution failed\n\n"
                f"Constitutional final gate: {reason}.\n",
                encoding="utf-8",
            )


def install_v6_expert_policy(runtime: ProductionRuntime) -> ProductionRuntime:
    runtime.prompt_policy = V6ExpertPromptPolicy()
    runtime.execution_engine = V6EvidenceCompleteExecutionEngine(
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
    "V6EvidenceCompleteExecutionEngine",
    "V6ExpertPromptPolicy",
    "install_v6_expert_policy",
]
