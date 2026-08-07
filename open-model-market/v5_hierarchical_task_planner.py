"""Compatibility facade for the task-derived dynamic Parameter Graph planner.

Historical versions of this module contained an independent fixed work-stage grammar,
a preloaded parameter list and an independent/review/synthesis role template.  Those
implementations are deliberately removed.  All callers now share the single active
planner in :mod:`v5_dynamic_parameter_graph`.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_dynamic_parameter_graph import (
    PARAMETER_SCHEMA_VERSION,
    SCHEMA_VERSION,
    build_dynamic_planning_context,
    decompose_task as _decompose_task,
    discover_parameter_requirements as _discover_parameter_requirements,
)

COMPATIBILITY_SCHEMA_VERSION = "v5-hierarchical-planner-facade-1"


def decompose_task(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Return the single active current-ticket work DAG with compatibility flags."""
    value = dict(_decompose_task(packet))
    value["finite_acyclic_by_construction"] = bool(
        value.get("finite_acyclic_validated_by_networkx")
    )
    value["planner_authority"] = "v5_dynamic_parameter_graph"
    return value


def discover_parameter_requirements(
    decomposition: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Discover only effective current-task parameter instances."""
    value = dict(_discover_parameter_requirements(decomposition, candidates))
    value["planner_authority"] = "v5_dynamic_parameter_graph"
    value["legacy_fixed_parameter_catalog_used"] = False
    return value


def build_hierarchical_planning_context(
    packet: Mapping[str, Any],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Expose the unified planner through the historical public function name."""
    value = dict(build_dynamic_planning_context(packet, candidates))
    # Runtime feedback is intentionally outside pre-execution planning.  Keep the
    # compatibility sequence scoped to plan construction and terminate at model
    # assignment, exactly as the production candidate optimizer does.
    planning_sequence = [
        str(stage)
        for stage in value.get("planning_sequence") or []
        if str(stage) != "runtime-feedback-replanning"
    ]
    if not planning_sequence or planning_sequence[-1] != "ortools-model-assignment":
        raise RuntimeError(
            "unified dynamic planner did not terminate at OR-Tools model assignment"
        )
    value.update(
        {
            "schema_version": COMPATIBILITY_SCHEMA_VERSION,
            "dynamic_parameter_graph_schema_version": SCHEMA_VERSION,
            "parameter_schema_version": PARAMETER_SCHEMA_VERSION,
            "planning_sequence": planning_sequence,
            "planner_authority": "v5_dynamic_parameter_graph",
            "runtime_replanning": {
                "stage": "runtime-feedback-replanning",
                "enabled": True,
                "promotion_depth_fixed": False,
                "cross_task_history_used": False,
            },
            "legacy_fixed_parameter_catalog_used": False,
            "legacy_fixed_role_grammar_used": False,
            "semantic_keyword_routing_used": False,
            "cross_task_history_used": False,
        }
    )
    decomposition = dict(value.get("decomposition") or {})
    decomposition["finite_acyclic_by_construction"] = bool(
        decomposition.get("finite_acyclic_validated_by_networkx")
    )
    value["decomposition"] = decomposition
    return value


__all__ = [
    "COMPATIBILITY_SCHEMA_VERSION",
    "PARAMETER_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_hierarchical_planning_context",
    "decompose_task",
    "discover_parameter_requirements",
]
