"""Production expert request policy and complete failed-result persistence.

Expert endpoint selection is restricted to the same ZDR/data-collection policy
that is placed on every expert request. Failed execution results are returned to
the pipeline after all runtime and constitutional artifacts are written, so the
pipeline can merge governance and expert ledgers before the production wrapper
raises the authoritative failure.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_runtime import ProductionRuntime
from v5_soft_resource_governance import (
    SoftResourceExecutionEngine,
    SoftResourcePromptPolicy,
)

EXPERT_DATA_COLLECTION_POLICY = "deny"
EXPERT_ZDR_REQUIRED = True


class ProductionExpertPromptPolicy(SoftResourcePromptPolicy):
    """Apply an explicit, auditable privacy contract to expert requests."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise RuntimeError("provider lock missing from production expert request")
        locked = dict(provider)
        locked["data_collection"] = EXPERT_DATA_COLLECTION_POLICY
        locked["zdr"] = EXPERT_ZDR_REQUIRED
        payload["provider"] = locked
        assert_request_has_no_tools(
            payload,
            context=f"production expert {node.node_id} request",
        )
        return payload


class EvidenceCompleteExecutionEngine(SoftResourceExecutionEngine):
    """Return failed results only after complete evidence has been persisted."""

    @staticmethod
    def _raise_failed_result(result: Mapping[str, Any]) -> None:
        """Defer authoritative failure to the production wrapper.

        The pipeline still receives a failed result, merges the governance and
        expert request ledgers, writes the final result and manifest, and then
        ``v5_production_ticket.py`` raises because the normalized result is not
        successful. No failed result can be mistaken for production success.
        """
        del result

    @classmethod
    def _fail_constitutional_result(
        cls,
        result: dict[str, Any],
        root: Path | None,
        reason: str,
    ) -> None:
        """Persist constitutional failure without aborting artifact assembly."""
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
    """Install the production-only prompt and evidence-complete engine."""
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
