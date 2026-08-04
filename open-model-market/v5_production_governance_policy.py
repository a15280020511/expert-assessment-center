"""Production governance contracts for recovery, dynamic matching and value.

GPT remains the sole author of task decomposition, expert selection, expert
combination, execution organization, parameters and recovery candidates. This
layer states and validates only constitutional contracts: finite recovery,
closed-world evidence, concrete-problem-specific dynamic matching and highest
cost effectiveness subject to the required delivery contract.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from v5_gpt_expert_selector_policy import (
    MAXIMUM_RECOVERY_CANDIDATES_PER_NODE,
    _canonical_json,
    build_proposal_request as _build_proposal_request,
    build_synthesis_request as _build_synthesis_request,
    parse_proposal,
)
from v5_proposal_materializer import (
    ProposalValidationError,
    claude_unified_review_payload as _claude_unified_review_payload,
    deterministic_violations as _deterministic_violations,
    materialize_proposal as _materialize_proposal,
)
from v5_task_constraints import normalized_quantities

_DYNAMIC_DIMENSIONS = [
    "problem_structure",
    "work_items",
    "work_granularity",
    "dependency_graph",
    "execution_stages",
    "parallel_or_serial_organization",
    "expert_count",
    "expert_roles",
    "expert_functions",
    "expert_collaboration_and_review_relationships",
    "final_nodes",
    "critical_work",
    "optional_work",
    "non_degradable_work",
    "minimum_usable_coverage",
    "model",
    "provider",
    "reasoning_effort",
    "output_capacity_advisory",
    "recovery_candidate_count",
    "recovery_candidate_distribution",
    "recovery_priority",
    "recovery_stop_condition",
]


def _amend_request(
    request: Mapping[str, Any],
    *,
    approved_recovery_calls: int,
) -> dict[str, Any]:
    amended = dict(request)
    messages = amended.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise RuntimeError("GPT governance request is missing its user contract")
    user = messages[1]
    if not isinstance(user, Mapping):
        raise RuntimeError("GPT governance user message is invalid")
    try:
        payload = json.loads(str(user.get("content") or ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("GPT governance user contract is not JSON") from exc
    if not isinstance(payload, Mapping):
        raise RuntimeError("GPT governance user contract must be an object")
    payload = dict(payload)
    constraints = payload.get("execution_constraints")
    if not isinstance(constraints, Mapping):
        raise RuntimeError("GPT governance execution constraints are missing")
    constraints = dict(constraints)
    reserve = max(0, int(approved_recovery_calls))
    constraints.update(
        {
            "concrete_problem_concrete_analysis": True,
            "concrete_expert_selection": True,
            "concrete_expert_combination": True,
            "concrete_execution_organization": True,
            "concrete_model_provider_parameter_matching": True,
            "all_dynamically_determinable_variables_must_be_dynamic": True,
            "required_dynamic_dimensions": list(_DYNAMIC_DIMENSIONS),
            "selection_objective": (
                "maximize task-specific cost effectiveness subject to complete "
                "delivery, evidence, privacy, company-diversity and reliability "
                "constraints; cost effectiveness is not lowest price alone"
            ),
            "cost_effectiveness_factors": [
                "task_fit",
                "reasoning_and_output_capability",
                "endpoint_reliability",
                "failure_probability",
                "context_compatibility",
                "provider_availability",
                "expected_effective_call_count_including_recovery",
                "actual_price",
            ],
            "avoid_low_price_high_failure_endpoints": True,
            "avoid_unnecessary_redundant_experts": True,
            "recovery_candidate_count_required": reserve,
            "maximum_recovery_candidates_per_node": (
                MAXIMUM_RECOVERY_CANDIDATES_PER_NODE
            ),
            "recovery_candidates_are_preselected_not_calls": True,
            "recovery_distribution_policy": (
                "when the reserve covers every node, assign at least one "
                "different-company recovery candidate to every node; otherwise "
                "allocate the complete reserve to nodes whose failure would most "
                "directly prevent required work or final delivery; one node may "
                "hold multiple finite candidates and GPT must not create artificial "
                "expert nodes merely to distribute recovery candidates"
            ),
            "degraded_delivery_policy": (
                "default allowed unless the task explicitly denies it; after the "
                "minimum usable coverage, at least one strict content success, all "
                "non-degradable work, evidence integrity, company uniqueness and "
                "tool prohibition are satisfied, stop rather than add paid calls "
                "only to convert degraded_success into full_success"
            ),
            "closed_world_work_contract_policy": (
                "work objectives, required_outputs, roles, and functions must not "
                "introduce any precise quantity absent from the original task; "
                "derived comparisons, preferences, policies and action rules must "
                "be labelled as inference, recommendation or constraint rather "
                "than user fact unless directly and faithfully quoted"
            ),
        }
    )
    payload["execution_constraints"] = constraints
    updated = list(messages)
    updated[1] = {**dict(user), "content": _canonical_json(payload)}
    amended["messages"] = updated
    governance = amended.get("governance_policy")
    governance = dict(governance) if isinstance(governance, Mapping) else {}
    governance.update(
        {
            "dynamic_matching_principle": "concrete-problem-concrete-matching",
            "primary_objective": "highest-cost-effectiveness-subject-to-contract",
            "all_dynamic_dimensions_declared": True,
            "degraded_success_is_not_full_success": True,
        }
    )
    amended["governance_policy"] = governance
    return amended


def build_proposal_request(**kwargs: Any) -> dict[str, Any]:
    return _amend_request(
        _build_proposal_request(**kwargs),
        approved_recovery_calls=int(kwargs.get("approved_recovery_calls") or 0),
    )


def build_synthesis_request(**kwargs: Any) -> dict[str, Any]:
    return _amend_request(
        _build_synthesis_request(**kwargs),
        approved_recovery_calls=int(kwargs.get("approved_recovery_calls") or 0),
    )


def _unsupported_quantity_rows(
    proposal: Mapping[str, Any],
    allowed: set[tuple[str, str, str]],
) -> list[str]:
    violations: list[str] = []
    for work in proposal.get("work_items", []):
        if not isinstance(work, Mapping):
            continue
        work_id = str(work.get("work_id") or "unknown")
        texts = [str(work.get("objective") or "")]
        outputs = work.get("required_outputs")
        if isinstance(outputs, list):
            texts.extend(str(value) for value in outputs)
        for text in texts:
            if normalized_quantities(text) - allowed:
                violations.append(f"{work_id}:{text[:160]}")
    for node in proposal.get("nodes", []):
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "unknown")
        texts = [str(node.get("role") or "")]
        functions = node.get("functions")
        if isinstance(functions, list):
            texts.extend(str(value) for value in functions)
        for text in texts:
            if normalized_quantities(text) - allowed:
                violations.append(f"{node_id}:{text[:160]}")
    return violations


def _proposal_policy_violations(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    *,
    approved_recovery_calls: int,
) -> list[str]:
    violations: list[str] = []
    nodes = [
        row
        for row in proposal.get("nodes", [])
        if isinstance(row, Mapping)
    ]
    reserve = max(0, int(approved_recovery_calls))
    recovery_counts = [
        len(row.get("recovery", []))
        if isinstance(row.get("recovery"), list)
        else 0
        for row in nodes
    ]
    if any(count > MAXIMUM_RECOVERY_CANDIDATES_PER_NODE for count in recovery_counts):
        violations.append(
            "node recovery candidate count exceeds finite operational maximum"
        )
    candidate_count = sum(recovery_counts)
    if candidate_count != reserve:
        violations.append(
            "recovery candidate count must equal approved recovery call reserve: "
            f"candidates={candidate_count}, reserve={reserve}"
        )
    if reserve and reserve >= len(nodes):
        uncovered = [
            str(row.get("node_id") or "unknown")
            for row, count in zip(nodes, recovery_counts, strict=True)
            if count < 1
        ]
        if uncovered:
            violations.append(
                "recovery reserve covers every node but candidates are missing: "
                + ",".join(uncovered)
            )

    constraints = task_envelope.get("task_constraints")
    precise_allowed = True
    if isinstance(constraints, Mapping):
        precise_allowed = bool(
            constraints.get("unsupported_precise_quantities_allowed", True)
        )
    if not precise_allowed:
        unsupported = _unsupported_quantity_rows(
            proposal,
            normalized_quantities(task),
        )
        violations.extend(
            "closed-world work contract introduces unsupported quantity: " + row
            for row in unsupported
        )
    return violations


def deterministic_violations(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    **limits: Any,
) -> list[str]:
    policy = _proposal_policy_violations(
        proposal,
        task,
        task_envelope,
        approved_recovery_calls=int(limits.get("approved_recovery_calls") or 0),
    )
    native = _deterministic_violations(
        proposal,
        task,
        task_envelope,
        catalog,
        **limits,
    )
    return list(dict.fromkeys([*policy, *native]))


def materialize_proposal(
    proposal: Mapping[str, Any],
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    **limits: Any,
):
    violations = _proposal_policy_violations(
        proposal,
        task,
        task_envelope,
        approved_recovery_calls=int(limits.get("approved_recovery_calls") or 0),
    )
    if violations:
        raise ProposalValidationError("; ".join(violations))
    graph, graph_limits, audit = _materialize_proposal(
        proposal,
        task,
        task_envelope,
        catalog,
        **limits,
    )
    audit = dict(audit)
    recovery_pool = graph.metadata.get("recovery_pool", {})
    pool_rows = (
        recovery_pool.values()
        if isinstance(recovery_pool, Mapping)
        else ()
    )
    audit.update(
        {
            "recovery_candidate_count": sum(
                len(rows) for rows in pool_rows if isinstance(rows, list)
            ),
            "recovery_candidate_count_required": int(
                limits.get("approved_recovery_calls") or 0
            ),
            "maximum_recovery_candidates_per_node": (
                MAXIMUM_RECOVERY_CANDIDATES_PER_NODE
            ),
            "recovery_candidates_are_preselected_not_calls": True,
            "closed_world_work_contract_checked": True,
            "dynamic_matching_principle": "concrete-problem-concrete-matching",
            "primary_objective": "highest-cost-effectiveness-subject-to-contract",
            "local_cost_scoring_used": False,
            "local_optimizer_used": False,
        }
    )
    return graph, graph_limits, audit


def claude_unified_review_payload(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Preserve the fixed Claude review schema exactly."""
    return dict(_claude_unified_review_payload(*args, **kwargs))


__all__ = [
    "build_proposal_request",
    "build_synthesis_request",
    "parse_proposal",
    "claude_unified_review_payload",
    "deterministic_violations",
    "materialize_proposal",
]
