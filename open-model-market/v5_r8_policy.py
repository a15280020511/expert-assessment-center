"""R8 production policy patches for task decomposition and reasoning allocation."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

import task_semantic_compiler as compiler

_INSTALLED = False
_ORIGINAL_FINISH = compiler._finish


def production_reasoning_vector(
    operations: Mapping[str, float],
    high_stakes: bool,
    complexity: str,
) -> dict[str, Any]:
    """Allocate reasoning by cognitive operation instead of making every node high."""
    names = set(operations)
    base = max(operations.values(), default=0.58)
    high_value = bool(names & {
        "synthesis", "quantitative_modeling", "implementation",
        "adversarial_reasoning", "counterfactual_analysis",
    })
    medium_value = bool(names & {
        "evidence_validation", "decision_comparison", "causal_reasoning",
        "forecasting", "analysis",
    })
    if high_value:
        depth = max(base, 0.82 if high_stakes else 0.72)
    elif medium_value:
        depth = max(base, 0.64 if high_stakes else 0.56)
    else:
        depth = max(base, 0.48)
    if complexity == "simple" and names <= {"analysis", "creative_generation"}:
        depth = min(depth, 0.50)
    return {
        "reasoning_enabled": not (names == {"creative_generation"} and complexity == "simple"),
        "depth": round(min(1.0, depth), 6),
        "exploration": 0.82 if names & {
            "creative_generation", "counterfactual_analysis", "adversarial_reasoning"
        } else 0.42,
        "verification": (
            0.90 if names & {"quantitative_modeling", "evidence_validation", "synthesis"}
            else 0.76 if high_stakes
            else 0.60
        ),
        "counterfactual": 0.86 if "counterfactual_analysis" in names else 0.34,
        "causal_reasoning": 0.88 if "causal_reasoning" in names else 0.42,
    }


def boundary_structured_finish(
    works: list[compiler.AtomicWork],
    task: str,
    profile: Any,
    structured: bool,
    domains: list[str],
) -> list[compiler.AtomicWork]:
    """Require strict machine JSON only at the delivery boundary."""
    result = _ORIGINAL_FINISH(works, task, profile, structured, domains)
    normalized: list[compiler.AtomicWork] = []
    single = len(result) == 1
    for work in result:
        operations = set(work.operation_requirements)
        boundary = bool(structured and (single or "synthesis" in operations))
        contract = dict(work.output_contract)
        contract["machine_readable_required"] = boundary
        contract["delivery_boundary"] = boundary
        normalized.append(replace(work, output_contract=contract))
    return normalized


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    compiler._reasoning_vector = production_reasoning_vector
    compiler._finish = boundary_structured_finish
    _INSTALLED = True
