"""V5 execution-graph contracts.

The contracts remain backward compatible with earlier V5 graph JSON. Production
resilience limits are conservative defaults and are never optimized away.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class SelectedNode:
    """One fully resolved model invocation in a V5 execution graph."""

    node_id: str
    assigned_work: tuple[str, ...]
    professional_capabilities: Mapping[str, float]
    functions: tuple[str, ...]
    prompt_profile: Mapping[str, Any]
    reasoning_profile: Mapping[str, Any]
    parameter_profile: Mapping[str, Any]
    model: str
    provider_endpoint: str
    output_contract: Mapping[str, Any]
    estimated_quality: float
    quality_uncertainty: float
    estimated_cost: float
    failure_probability: float = 0.0
    request_config: Mapping[str, Any] = field(default_factory=dict)
    independence_group: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelectedNode":
        return cls(
            node_id=str(value.get("node_id") or ""),
            assigned_work=tuple(str(x) for x in value.get("assigned_work", ())),
            professional_capabilities={
                str(k): float(v)
                for k, v in dict(value.get("professional_capabilities", {})).items()
            },
            functions=tuple(str(x) for x in value.get("functions", ())),
            prompt_profile=dict(value.get("prompt_profile", {})),
            reasoning_profile=dict(value.get("reasoning_profile", {})),
            parameter_profile=dict(value.get("parameter_profile", {})),
            model=str(value.get("model") or ""),
            provider_endpoint=str(value.get("provider_endpoint") or ""),
            output_contract=dict(value.get("output_contract", {})),
            estimated_quality=float(value.get("estimated_quality", 0.0)),
            quality_uncertainty=float(value.get("quality_uncertainty", 0.0)),
            estimated_cost=float(value.get("estimated_cost", 0.0)),
            failure_probability=float(value.get("failure_probability", 0.0)),
            request_config=dict(value.get("request_config", {})),
            independence_group=(
                str(value["independence_group"])
                if value.get("independence_group") not in {None, ""}
                else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectedEdge:
    """One explicit information or control relationship between two nodes."""

    source: str
    target: str
    relation_type: str
    payload_type: str
    visibility_policy: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SelectedEdge":
        return cls(
            source=str(value.get("source") or ""),
            target=str(value.get("target") or ""),
            relation_type=str(value.get("relation_type") or ""),
            payload_type=str(value.get("payload_type") or ""),
            visibility_policy=str(value.get("visibility_policy") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionGraph:
    """The complete auditable V5 execution plan selected by the optimizer."""

    nodes: tuple[SelectedNode, ...]
    edges: tuple[SelectedEdge, ...]
    execution_stages: tuple[tuple[str, ...], ...]
    entry_nodes: tuple[str, ...]
    final_nodes: tuple[str, ...]
    required_work: tuple[str, ...]
    estimated_quality: float
    quality_floor: float
    estimated_total_cost: float
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExecutionGraph":
        raw_stages: Sequence[Sequence[Any]] = value.get("execution_stages", ())
        return cls(
            nodes=tuple(SelectedNode.from_mapping(x) for x in value.get("nodes", ())),
            edges=tuple(SelectedEdge.from_mapping(x) for x in value.get("edges", ())),
            execution_stages=tuple(tuple(str(x) for x in stage) for stage in raw_stages),
            entry_nodes=tuple(str(x) for x in value.get("entry_nodes", ())),
            final_nodes=tuple(str(x) for x in value.get("final_nodes", ())),
            required_work=tuple(str(x) for x in value.get("required_work", ())),
            estimated_quality=float(value.get("estimated_quality", 0.0)),
            quality_floor=float(value.get("quality_floor", 0.0)),
            estimated_total_cost=float(value.get("estimated_total_cost", 0.0)),
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "execution_stages": [list(stage) for stage in self.execution_stages],
            "entry_nodes": list(self.entry_nodes),
            "final_nodes": list(self.final_nodes),
            "required_work": list(self.required_work),
            "estimated_quality": self.estimated_quality,
            "quality_floor": self.quality_floor,
            "estimated_total_cost": self.estimated_total_cost,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GraphLimits:
    """Non-optimizable safety and production-resilience ceilings."""

    max_nodes: int = 16
    max_edges: int = 64
    max_stages: int = 8
    max_model_calls: int = 16
    max_retries: int = 2
    max_replacements: int = 2
    max_budget_usd: float | None = None

    # Delivery gates.
    min_required_work_coverage: float = 2.0 / 3.0
    min_successful_content_nodes: int = 1
    allow_degraded_success: bool = True

    # Reliability and cost-risk policy.
    max_node_failure_probability: float = 0.18
    cost_risk_multiplier: float = 1.35
    max_provider_share: float = 0.60
    max_provider_failures: int = 1
    tight_budget_headroom_ratio: float = 1.25

    # Context/output controls. 32,768 is a maximum permission, not a target.
    max_output_allowance_tokens: int = 32_768
    max_upstream_chars_per_node: int = 6_000
    max_total_upstream_chars: int = 24_000


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    path: str = ""
