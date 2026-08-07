"""Task-dynamic production runtime facade.

The legacy executor is retained for I/O and state-machine compatibility, while
this facade removes historical business ceilings. Call/recovery capacity and
runtime resilience telemetry are derived from the current finite execution
graph. Provider routing stays open and company identity is audit telemetry only.
Structural safety, no-tools, evidence contracts and finite DAG execution remain
enforced.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence

import v5_runtime_legacy as _legacy
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph
from v5_no_tools_policy import (
    assert_request_has_no_tools,
    assert_response_has_no_tools,
)

_LegacyExecutionEngine = _legacy.ExecutionEngine

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


@dataclass(frozen=True)
class RuntimeConfig:
    """Compatibility telemetry; fixed call counts are not execution gates."""

    total_call_limit: int
    recovery_call_limit: int
    cost_anomaly_usd: float | None
    tools_allowed: bool = False
    live_catalog_required: bool = False
    provider_lock_required: bool = False
    cost_risk_multiplier: float = 1.0
    max_provider_failures: int = 1

    def __post_init__(self) -> None:
        if int(self.total_call_limit) < 1:
            raise ValueError("total_call_limit telemetry must be positive")
        if int(self.recovery_call_limit) < 0:
            raise ValueError("recovery_call_limit telemetry must be non-negative")
        if self.cost_anomaly_usd is not None and not math.isfinite(
            float(self.cost_anomaly_usd)
        ):
            raise ValueError("cost_anomaly_usd must be finite when supplied")
        if self.tools_allowed:
            raise ValueError("expert runtime external tools remain disabled")
        if self.provider_lock_required:
            raise ValueError(
                "active runtime uses unrestricted OpenRouter Provider routing"
            )
        if not math.isfinite(float(self.cost_risk_multiplier)) or float(
            self.cost_risk_multiplier
        ) <= 0:
            raise ValueError("cost_risk_multiplier must be finite and positive")
        if int(self.max_provider_failures) < 0:
            raise ValueError("max_provider_failures must be non-negative")

    @property
    def initial_call_limit(self) -> int:
        # Compatibility only. The BudgetController below derives real capacity
        # from the current graph and never uses this property as admission.
        return max(1, int(self.total_call_limit) - int(self.recovery_call_limit))

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "initial_call_limit": self.initial_call_limit,
            "runtime_version": _legacy.RUNTIME_VERSION,
            "provider_routing_mode": "unrestricted-openrouter",
            "fixed_call_ceiling_applied": False,
            "team_size_source": "current-execution-graph",
            "recovery_capacity_source": "unique-current-run-recovery-identities",
            "runtime_resilience_parameters_dynamic": True,
            "failed_model_circuit_scope": "current-run-only",
            "tool_use_forbidden": True,
        }


def _recovery_capacity(graph: ExecutionGraph) -> int:
    """Count unique recovery identities, not per-node copies of the shared pool."""
    metadata = graph.metadata if isinstance(graph.metadata, Mapping) else {}
    pool = metadata.get("recovery_pool")
    if not isinstance(pool, Mapping):
        return 0
    identities: set[tuple[str, str]] = set()
    for rows in pool.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            model = str(row.get("model") or "").strip()
            endpoint = str(row.get("provider_endpoint") or "").strip()
            if model:
                identities.add((model, endpoint))
    return len(identities)


def _dynamic_resilience_parameters(
    graph: ExecutionGraph,
) -> tuple[float, int]:
    """Derive runtime resilience telemetry from this execution graph only."""
    initial = max(1, len(graph.nodes))
    recovery = max(0, _recovery_capacity(graph))
    graph_width = max(1, initial + recovery)
    cost_risk_multiplier = 1.0 + min(
        1.0,
        math.log2(graph_width + 1) / 10.0,
    )
    max_provider_failures = max(
        1,
        math.ceil(recovery / max(1, initial)),
    )
    return float(cost_risk_multiplier), int(max_provider_failures)


def _dynamic_config(config: RuntimeConfig, graph: ExecutionGraph) -> RuntimeConfig:
    initial = max(1, len(graph.nodes))
    recovery = max(0, _recovery_capacity(graph))
    cost_risk_multiplier, max_provider_failures = _dynamic_resilience_parameters(
        graph
    )
    return RuntimeConfig(
        total_call_limit=initial + recovery,
        recovery_call_limit=recovery,
        cost_anomaly_usd=config.cost_anomaly_usd,
        tools_allowed=False,
        live_catalog_required=config.live_catalog_required,
        provider_lock_required=False,
        cost_risk_multiplier=cost_risk_multiplier,
        # Current-run circuit depth scales with the graph's recovery breadth.
        # It carries no state across tasks and is not a Provider allowlist.
        max_provider_failures=max_provider_failures,
    )


class BudgetController(_legacy.BudgetController):
    """Use graph-derived finite capacity instead of CLI/config call ceilings."""

    def __init__(self, config: RuntimeConfig, graph: ExecutionGraph) -> None:
        self.requested_config = config
        super().__init__(_dynamic_config(config, graph), graph)

    def snapshot(self) -> dict[str, Any]:
        value = dict(super().snapshot())
        value.update(
            {
                "fixed_call_ceiling_applied": False,
                "call_capacity_source": "current-execution-graph",
                "recovery_capacity_source": "unique-current-run-recovery-identities",
                "runtime_resilience_parameters_dynamic": True,
                "failed_model_circuit_scope": "current-run-only",
                "failed_model_circuit_threshold": int(
                    self.config.max_provider_failures
                ),
                "requested_total_call_telemetry": int(
                    self.requested_config.total_call_limit
                ),
                "requested_recovery_call_telemetry": int(
                    self.requested_config.recovery_call_limit
                ),
            }
        )
        return value


class ExecutionEngine(_LegacyExecutionEngine):
    """Validate intrinsic graph safety without historical business gates."""

    _IGNORED_BUSINESS_LIMIT_CODES = {
        "budget_limit",
        "node_limit",
        "edge_limit",
        "call_limit",
        "stage_limit",
        "model_company_reuse",
    }

    def _validated_graph(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        limits: GraphLimits | None,
    ) -> tuple[ExecutionGraph, GraphLimits]:
        parsed = (
            graph
            if isinstance(graph, ExecutionGraph)
            else ExecutionGraph.from_mapping(graph)
        )
        active_limits = limits or GraphLimits()
        structural = [
            issue
            for issue in validate_execution_graph(parsed, active_limits)
            if issue.code not in self._IGNORED_BUSINESS_LIMIT_CODES
        ]
        if structural:
            raise RuntimeError(
                "Invalid execution graph: "
                + "; ".join(
                    f"{issue.code}:{issue.message}" for issue in structural
                )
            )
        return parsed, active_limits

    def _recorded_call(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
        budget: BudgetController,
        node: SelectedNode,
        kind: str,
    ) -> Any:
        # Explicit top-level no-tools enforcement wraps the actual model request
        # and raw response.  openrouter_api enforces the same boundary again at
        # transport level, giving production a two-layer fail-closed guarantee.
        def guarded_call_fn(
            run_config: Any,
            payload: Mapping[str, Any],
        ) -> tuple[Mapping[str, Any], float]:
            assert_request_has_no_tools(payload)
            response, cost = call_fn(run_config, payload)
            assert_response_has_no_tools(response)
            return response, cost

        attempt = super()._recorded_call(
            selected,
            attempts,
            original_task,
            upstream,
            run,
            guarded_call_fn,
            budget,
            node,
            kind,
        )
        invalid = _legacy.FailureCategory.PROVIDER_INVALID_RESPONSE
        if attempt is not None and self._category(attempt) == invalid:
            budget.fail_endpoint(node.provider_endpoint, invalid)
        return attempt


# Patch legacy module globals because legacy classes resolve these names at
# runtime. This is an explicit compatibility bridge, not a production gate.
_legacy.RuntimeConfig = RuntimeConfig
_legacy.BudgetController = BudgetController
_legacy.ExecutionEngine = ExecutionEngine

__all__ = [name for name in globals() if not name.startswith("_")]
