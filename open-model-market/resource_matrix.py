"""Compile V5 task interpretations into auditable resource-demand matrices."""
from __future__ import annotations

import re
from dataclasses import is_dataclass, replace
from types import SimpleNamespace
from typing import Any, Mapping

import numpy as np

from atomic_work_graph import compile_atomic_work_graphs
from task_semantic_compiler import compile_task_semantics

BASE_CAPABILITIES = (
    "general_analysis",
    "complex_reasoning",
    "quantitative_reasoning",
    "statistics",
    "causal_reasoning",
    "forecasting",
    "evidence_validation",
    "counterfactual_analysis",
    "decision_comparison",
    "adversarial_reasoning",
    "risk_discovery",
    "implementation",
    "creative_generation",
    "long_context",
    "structured_output",
    "synthesis",
    "delivery",
)

OPERATION_CAPABILITIES: Mapping[str, Mapping[str, float]] = {
    "analysis": {"general_analysis": 0.92, "complex_reasoning": 0.74, "delivery": 0.62},
    "causal_reasoning": {"causal_reasoning": 0.96, "complex_reasoning": 0.86, "evidence_validation": 0.62},
    "quantitative_modeling": {"quantitative_reasoning": 0.98, "statistics": 0.84, "complex_reasoning": 0.82, "delivery": 0.70},
    "forecasting": {"forecasting": 0.98, "statistics": 0.70, "complex_reasoning": 0.78, "risk_discovery": 0.60},
    "counterfactual_analysis": {"counterfactual_analysis": 0.96, "causal_reasoning": 0.74, "complex_reasoning": 0.82},
    "evidence_validation": {"evidence_validation": 0.99, "risk_discovery": 0.72, "complex_reasoning": 0.74, "delivery": 0.66},
    "decision_comparison": {"decision_comparison": 0.98, "complex_reasoning": 0.86, "risk_discovery": 0.68, "delivery": 0.78},
    "adversarial_reasoning": {"adversarial_reasoning": 0.99, "risk_discovery": 0.94, "evidence_validation": 0.72},
    "implementation": {"implementation": 0.99, "complex_reasoning": 0.70, "delivery": 0.82},
    "creative_generation": {"creative_generation": 0.98, "delivery": 0.58},
    "synthesis": {"synthesis": 0.99, "decision_comparison": 0.82, "evidence_validation": 0.72, "delivery": 0.92},
}

HARD_BY_OPERATION: Mapping[str, set[str]] = {
    "quantitative_modeling": {"quantitative_reasoning"},
    "forecasting": {"forecasting"},
    "evidence_validation": {"evidence_validation"},
    "adversarial_reasoning": {"adversarial_reasoning", "risk_discovery"},
    "implementation": {"implementation"},
    "creative_generation": {"creative_generation"},
    "synthesis": {"synthesis"},
}

# A reversible field trial is not the same thing as validating external evidence.
# Normalize only phrases where “validation” directly modifies a trial step/period.
_TRIAL_VALIDATION_ZH = re.compile(r"验证(?=(步骤|计划|周期|期|流程|方案|试用))")
_TRIAL_VALIDATION_EN = re.compile(r"\bvalidation(?=\s+(step|plan|period|workflow|trial))", re.I)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _semantic_task_text(task: str) -> str:
    text = _TRIAL_VALIDATION_ZH.sub("试用", str(task or ""))
    return _TRIAL_VALIDATION_EN.sub("trial", text)


def _semantic_run(run: Any) -> tuple[Any, bool]:
    original = str(getattr(run, "task", "") or "")
    normalized = _semantic_task_text(original)
    if normalized == original:
        return run, False
    if is_dataclass(run) and not isinstance(run, type):
        return replace(run, task=normalized), True
    values = dict(vars(run)) if hasattr(run, "__dict__") else {}
    values["task"] = normalized
    return SimpleNamespace(**values), True


def _capability_labels(interpretation: Mapping[str, Any]) -> list[str]:
    domains = sorted({str(domain) for work in interpretation["atomic_work"] for domain in work["domain_requirements"]})
    return [*BASE_CAPABILITIES, *(f"domain:{domain}" for domain in domains)]


def _work_capability_demands(work: Mapping[str, Any], labels: list[str]) -> tuple[dict[str, float], dict[str, bool], dict[str, float]]:
    demand = {label: 0.0 for label in labels}
    hard = {label: False for label in labels}
    confidence = {label: 0.0 for label in labels}
    operation_requirements = work.get("operation_requirements", {})
    for operation, operation_weight_raw in operation_requirements.items():
        operation_weight = _clamp(operation_weight_raw)
        for capability, base_weight in OPERATION_CAPABILITIES.get(str(operation), {}).items():
            value = _clamp(base_weight * (0.62 + 0.38 * operation_weight))
            demand[capability] = max(demand.get(capability, 0.0), value)
            confidence[capability] = max(confidence.get(capability, 0.0), 0.68 + 0.26 * operation_weight)
        for capability in HARD_BY_OPERATION.get(str(operation), set()):
            hard[capability] = True

    independence = work.get("independence_requirements", {})
    high_assurance_domain = bool(
        int(independence.get("minimum_independent_copies", 1) or 1) >= 2
        or independence.get("different_model_required")
        or independence.get("different_model_family_preferred")
        or independence.get("different_provider_preferred")
    )
    for domain, domain_weight_raw in work.get("domain_requirements", {}).items():
        label = f"domain:{domain}"
        value = _clamp(domain_weight_raw)
        demand[label] = max(demand.get(label, 0.0), value)
        # Domain fit remains an optimization signal for ordinary work. It becomes
        # a hard gate only when the task compiler explicitly requests independent
        # high-assurance specialist coverage.
        hard[label] = bool(high_assurance_domain and value >= 0.62)
        confidence[label] = max(confidence.get(label, 0.0), 0.72 + 0.22 * value)

    context = work.get("context_requirements", {})
    if int(context.get("required_context_tokens", 0)) >= 32768:
        demand["long_context"] = max(demand["long_context"], 0.84)
        hard["long_context"] = True
        confidence["long_context"] = 0.96
    if bool(work.get("output_contract", {}).get("machine_readable_required")):
        demand["structured_output"] = 1.0
        hard["structured_output"] = True
        confidence["structured_output"] = 0.99
    demand["delivery"] = max(demand["delivery"], 0.62)
    confidence["delivery"] = max(confidence["delivery"], 0.78)
    for label in labels:
        if demand[label] > 0 and confidence[label] == 0:
            confidence[label] = 0.70
    return demand, hard, confidence


def _matrix_rows(interpretation: Mapping[str, Any]) -> dict[str, Any]:
    labels = _capability_labels(interpretation)
    works = interpretation["atomic_work"]
    demand_rows: list[list[float]] = []
    hard_rows: list[list[int]] = []
    confidence_rows: list[list[float]] = []
    prompt_labels = sorted({name for work in works for name in work.get("prompt_requirements", {})})
    reasoning_labels = sorted({name for work in works for name in work.get("reasoning_requirements", {}) if name != "reasoning_enabled"})
    prompt_rows: list[list[float]] = []
    reasoning_rows: list[list[float]] = []
    work_index: list[dict[str, Any]] = []
    hard_requirements: list[dict[str, Any]] = []

    for work in works:
        demand, hard, confidence = _work_capability_demands(work, labels)
        demand_rows.append([round(demand[label], 6) for label in labels])
        hard_rows.append([1 if hard[label] else 0 for label in labels])
        confidence_rows.append([round(confidence[label], 6) for label in labels])
        prompt_rows.append([round(_clamp(work.get("prompt_requirements", {}).get(label, 0.0)), 6) for label in prompt_labels])
        reasoning_rows.append([round(_clamp(work.get("reasoning_requirements", {}).get(label, 0.0)), 6) for label in reasoning_labels])
        work_index.append(
            {
                "work_id": work["work_id"],
                "objective": work["objective"],
                "importance": work["importance"],
                "error_cost": work["error_cost"],
                "verifiability": work["verifiability"],
                "minimum_independent_copies": work.get("independence_requirements", {}).get("minimum_independent_copies", 1),
                "required_context_tokens": work.get("context_requirements", {}).get("required_context_tokens", 0),
                "expected_output_tokens": work.get("context_requirements", {}).get("expected_output_tokens", 0),
            }
        )
        for label in labels:
            if hard[label]:
                hard_requirements.append(
                    {
                        "work_id": work["work_id"],
                        "capability": label,
                        "minimum_demand": round(demand[label], 6),
                        "confidence": round(confidence[label], 6),
                        "source": "v5-task-semantic-compiler",
                    }
                )

    demand_array = np.asarray(demand_rows, dtype=float)
    hard_array = np.asarray(hard_rows, dtype=np.int8)
    confidence_array = np.asarray(confidence_rows, dtype=float)
    if demand_array.shape != hard_array.shape or demand_array.shape != confidence_array.shape:
        raise ValueError("Capability demand, hard-requirement, and confidence matrices must have identical shapes.")

    return {
        "interpretation_id": interpretation["interpretation_id"],
        "strategy": interpretation["strategy"],
        "work_index": work_index,
        "capability_labels": labels,
        "task_resource_matrix": demand_array.round(6).tolist(),
        "hard_requirement_matrix": hard_array.tolist(),
        "confidence_matrix": confidence_array.round(6).tolist(),
        "prompt_labels": prompt_labels,
        "prompt_requirement_matrix": np.asarray(prompt_rows, dtype=float).round(6).tolist(),
        "reasoning_labels": reasoning_labels,
        "reasoning_requirement_matrix": np.asarray(reasoning_rows, dtype=float).round(6).tolist(),
        "hard_requirements": hard_requirements,
        "shape": {"work_count": int(demand_array.shape[0]), "capability_count": int(demand_array.shape[1])},
    }


def compile_resource_matrices(compilation: Mapping[str, Any]) -> dict[str, Any]:
    interpretations = compilation.get("interpretations", [])
    if not isinstance(interpretations, list) or not interpretations:
        raise ValueError("Semantic compilation contains no interpretations.")
    matrices = [_matrix_rows(row) for row in interpretations]
    return {
        "version": 5,
        "task_digest": compilation.get("task_digest"),
        "matrices": matrices,
        "model_market_fields_present": False,
        "source": "v5-task-semantic-compiler",
    }


def compile_v5_task_resources(profile: Any, run: Any, max_interpretations: int = 3) -> dict[str, Any]:
    semantic_run, trial_validation_disambiguated = _semantic_run(run)
    semantics = compile_task_semantics(profile, semantic_run, max_interpretations=max_interpretations)
    graphs = compile_atomic_work_graphs(semantics)
    matrices = compile_resource_matrices(semantics)
    return {
        "version": 5,
        "architecture": "task-interpretations-to-atomic-work-graphs-to-resource-matrices",
        "task_semantics": semantics,
        "atomic_work_graphs": graphs,
        "resource_matrices": matrices,
        "semantic_input_policy": {
            "trial_validation_disambiguated": trial_validation_disambiguated,
            "delegation_notice_must_be_excluded_upstream": True,
            "domain_fit_hard_only_for_high_assurance_specialist_work": True,
        },
        "phase_a_complete": True,
        "model_market_accessed": False,
    }
