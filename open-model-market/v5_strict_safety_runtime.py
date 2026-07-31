"""Production runtime that forbids degraded delivery for strict contracts."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from execution_graph import ExecutionGraph, GraphLimits
from v5_runtime import (
    ExecutionEngine,
    ProductionRuntime,
    RuntimeAttempt,
)


class StrictSafetyExecutionEngine(ExecutionEngine):
    """Disable usable-but-failed output for high-stakes or exact contracts."""

    @staticmethod
    def _strict_node(node: Any) -> bool:
        contract = getattr(node, "output_contract", {})
        if not isinstance(contract, Mapping):
            return False
        return bool(
            contract.get("fail_closed_on_quality_gate")
            or contract.get("explicit_user_contract")
            or contract.get("explicit_markdown_contract")
        )

    @staticmethod
    def _degraded_usable(node: Any, attempt: RuntimeAttempt | None) -> bool:
        if StrictSafetyExecutionEngine._strict_node(node):
            return False
        return ExecutionEngine._degraded_usable(node, attempt)

    def execute_graph(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        run: Any,
        original_task: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        graph_value = (
            graph
            if isinstance(graph, ExecutionGraph)
            else ExecutionGraph.from_mapping(graph)
        )
        strict_nodes = [
            node for node in graph_value.nodes if self._strict_node(node)
        ]
        limits = kwargs.get("limits") or GraphLimits()
        if strict_nodes:
            content_nodes = [
                node
                for node in graph_value.nodes
                if "synthesis" not in node.functions
            ]
            limits = replace(
                limits,
                min_required_work_coverage=1.0,
                min_successful_content_nodes=max(1, len(content_nodes)),
                allow_degraded_success=False,
            )
            kwargs["limits"] = limits
        return super().execute_graph(
            graph_value,
            run,
            original_task,
            **kwargs,
        )


@dataclass
class StrictSafetyProductionRuntime(ProductionRuntime):
    """Use strict execution while preserving the explicit planner composition."""

    def __post_init__(self) -> None:
        super().__post_init__()
        self.execution_engine = StrictSafetyExecutionEngine(
            self.config,
            prompt_policy=self.prompt_policy,
            retry_policy=self.retry_policy,
            recovery_policy=self.recovery_policy,
            quality_policy=self.quality_policy,
            output_policy=self.output_policy,
        )
