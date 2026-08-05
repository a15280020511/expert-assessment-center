"""Validate a governance-signed roster and materialize a NetworkX expert DAG.

V6 contains no GPT planner, Claude red team, model panel, local model ranking, or
recursive agent loop. Model identities and work assignments arrive in the
immutable governance roster. This module may only resolve exact ZDR provider
endpoints for those fixed model identities and construct the declared DAG.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any, Mapping, Sequence

import networkx as nx

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode
from execution_graph_validator import validate_execution_graph
from v5_catalog_view import compact_endpoint_catalog
from v5_endpoint_catalog import fetch_live_endpoint_payloads
from v5_task_envelope import work_output_contract

RUNTIME_VERSION = "v6-governed-roster-networkx-1"
ROSTER_SCHEMA = "governed-expert-roster-v1"
PLAN_SCHEMA = "expert-team-plan-v1"
MAX_TEAM_SIZE = 8
MAX_RECOVERY_SIZE = 4


class GovernedRosterError(RuntimeError):
    """Fail-closed governed-roster or graph error."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GovernedRosterError(f"{field} must be an object")
    return value


def _rows(value: Any, field: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        raise GovernedRosterError(f"{field} must be a list")
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise GovernedRosterError(f"{field} contains a non-object row")
    return rows


def _positive_int(value: Any, field: str, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GovernedRosterError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise GovernedRosterError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return value


def _work_plan(ticket: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str, nx.DiGraph]:
    plan = _mapping(ticket.get("team_plan"), "team_plan")
    if plan.get("schema_version") != PLAN_SCHEMA:
        raise GovernedRosterError(f"team_plan.schema_version must be {PLAN_SCHEMA}")
    work_items = _rows(plan.get("work_items"), "team_plan.work_items")
    if not 2 <= len(work_items) <= MAX_TEAM_SIZE:
        raise GovernedRosterError("team_plan must contain 2-8 work items")
    work_ids = [str(row.get("work_id") or "") for row in work_items]
    if any(not value for value in work_ids) or len(work_ids) != len(set(work_ids)):
        raise GovernedRosterError("work ids must be non-empty and unique")
    final_work_id = str(plan.get("final_work_id") or "")
    if final_work_id not in work_ids:
        raise GovernedRosterError("team_plan.final_work_id is unknown")

    graph = nx.DiGraph()
    graph.add_nodes_from(work_ids)
    known = set(work_ids)
    for row in work_items:
        work_id = str(row["work_id"])
        dependencies = row.get("dependencies")
        if not isinstance(dependencies, list):
            raise GovernedRosterError(f"work {work_id} dependencies must be a list")
        normalized = [str(value) for value in dependencies]
        if len(normalized) != len(set(normalized)):
            raise GovernedRosterError(f"work {work_id} dependencies contain duplicates")
        for source in normalized:
            if source not in known or source == work_id:
                raise GovernedRosterError(
                    f"work {work_id} has an invalid dependency: {source}"
                )
            graph.add_edge(source, work_id)
    if not nx.is_directed_acyclic_graph(graph):
        raise GovernedRosterError("team_plan dependency graph is cyclic")
    if graph.out_degree(final_work_id) != 0:
        raise GovernedRosterError("final work item must be a terminal node")
    ancestors = nx.ancestors(graph, final_work_id)
    missing = sorted(set(work_ids) - ancestors - {final_work_id})
    if missing:
        raise GovernedRosterError(
            f"all work must feed the final work item; disconnected: {missing}"
        )
    return work_items, final_work_id, graph


def _member_identity(row: Mapping[str, Any], field: str) -> tuple[str, str]:
    model_id = str(row.get("model_id") or "").strip()
    company = str(row.get("company") or "").strip().casefold()
    if not model_id or "/" not in model_id or not company:
        raise GovernedRosterError(f"{field} has an invalid model/company identity")
    return model_id, company


def validate_governed_ticket(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ticket, roster digests, assignments, budget, and uniqueness."""
    if ticket.get("route") != "expert-team":
        raise GovernedRosterError("ticket.route must be expert-team")
    work_items, final_work_id, graph = _work_plan(ticket)
    plan = _mapping(ticket.get("team_plan"), "team_plan")
    roster = dict(_mapping(ticket.get("governance_roster"), "governance_roster"))
    if roster.get("schema_version") != ROSTER_SCHEMA:
        raise GovernedRosterError(f"governance_roster.schema_version must be {ROSTER_SCHEMA}")
    if roster.get("status") != "GOVERNED_EXPERT_ROSTER_READY":
        raise GovernedRosterError("governance roster is not ready")
    if roster.get("governance_repository") != "a15280020511/decision-system-governance":
        raise GovernedRosterError("governance roster authority is invalid")
    if not str(roster.get("governance_commit_sha") or "").strip():
        raise GovernedRosterError("governance commit SHA is missing")

    expected_plan_sha = _sha256(plan)
    if roster.get("team_plan_sha256") != expected_plan_sha:
        raise GovernedRosterError("team plan digest mismatch")
    supplied_roster_sha = str(roster.pop("roster_sha256", ""))
    if not supplied_roster_sha or supplied_roster_sha != _sha256(roster):
        raise GovernedRosterError("governance roster digest mismatch")
    roster["roster_sha256"] = supplied_roster_sha

    primary = _rows(roster.get("primary_members"), "governance_roster.primary_members")
    recovery = _rows(roster.get("recovery_members"), "governance_roster.recovery_members")
    team_size = _positive_int(
        roster.get("team_size"), "governance_roster.team_size", minimum=2, maximum=MAX_TEAM_SIZE
    )
    recovery_size = _positive_int(
        roster.get("recovery_size"),
        "governance_roster.recovery_size",
        minimum=0,
        maximum=MAX_RECOVERY_SIZE,
    )
    if len(primary) != team_size or len(recovery) != recovery_size:
        raise GovernedRosterError("roster member counts do not match declared sizes")
    if team_size != len(work_items):
        raise GovernedRosterError("one primary model is required per work item")
    if roster.get("final_work_id") != final_work_id:
        raise GovernedRosterError("roster final work id does not match team plan")
    if roster.get("all_companies_unique") is not True:
        raise GovernedRosterError("roster does not assert global company uniqueness")
    if int(roster.get("model_calls_for_selection") or 0) != 0:
        raise GovernedRosterError("governance roster selection must not call a model")
    if float(roster.get("selection_cost_usd") or 0.0) != 0.0:
        raise GovernedRosterError("governance roster selection cost must be zero")
    if roster.get("secret_values_exposed") is not False:
        raise GovernedRosterError("governance roster secret boundary is invalid")

    budget = _mapping(ticket.get("approved_budget"), "approved_budget")
    calls = _positive_int(
        budget.get("calls"), "approved_budget.calls", minimum=2, maximum=12
    )
    recovery_calls = _positive_int(
        budget.get("maximum_recovery_calls"),
        "approved_budget.maximum_recovery_calls",
        minimum=0,
        maximum=MAX_RECOVERY_SIZE,
    )
    if calls != team_size + recovery_size or recovery_calls != recovery_size:
        raise GovernedRosterError(
            "approved budget must equal primary plus preapproved recovery roster"
        )
    if int(roster.get("approved_total_calls") or -1) != calls:
        raise GovernedRosterError("roster approved call count does not match ticket")

    assigned = [str(row.get("assigned_work_id") or "") for row in primary]
    work_ids = [str(row["work_id"]) for row in work_items]
    if sorted(assigned) != sorted(work_ids):
        raise GovernedRosterError("primary roster does not cover every work item exactly once")
    final_members = [row for row in primary if row.get("assigned_work_id") == final_work_id]
    if len(final_members) != 1:
        raise GovernedRosterError("exactly one final synthesis member is required")

    identities = [_member_identity(row, "roster member") for row in primary + recovery]
    models = [model for model, _ in identities]
    companies = [company for _, company in identities]
    if len(models) != len(set(models)):
        raise GovernedRosterError("roster model identities are not unique")
    if len(companies) != len(set(companies)):
        raise GovernedRosterError("roster model companies are not globally unique")

    return {
        "schema_version": "v6-governed-ticket-validation-1",
        "status": "PASS",
        "runtime_version": RUNTIME_VERSION,
        "team_plan": dict(plan),
        "work_items": [dict(row) for row in work_items],
        "final_work_id": final_work_id,
        "work_graph": graph,
        "governance_roster": roster,
        "primary_members": [dict(row) for row in primary],
        "recovery_members": [dict(row) for row in recovery],
        "approved_total_calls": calls,
        "approved_recovery_calls": recovery_calls,
        "claude_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
    }


def _profile(validation: Mapping[str, Any]) -> tuple[int, int]:
    roster = _mapping(validation.get("governance_roster"), "governance_roster")
    profile = _mapping(roster.get("task_cost_profile"), "task_cost_profile")
    prompt = _positive_int(
        profile.get("expected_prompt_tokens_per_call"),
        "expected_prompt_tokens_per_call",
        minimum=1,
        maximum=2_000_000,
    )
    completion = _positive_int(
        profile.get("expected_completion_tokens_per_call"),
        "expected_completion_tokens_per_call",
        minimum=1,
        maximum=500_000,
    )
    return prompt, completion


def _required_models(validation: Mapping[str, Any]) -> list[str]:
    rows = list(validation["primary_members"]) + list(validation["recovery_members"])
    return [str(row["model_id"]) for row in rows]


def _model_objects(models: Mapping[str, Any], required: Sequence[str]) -> list[Any]:
    missing = [model_id for model_id in required if model_id not in models]
    if missing:
        raise GovernedRosterError(f"governed models missing from live catalog: {missing}")
    selected = [models[model_id] for model_id in required]
    for model in selected:
        model_id = str(getattr(model, "id", ""))
        inputs = list(getattr(model, "input_modalities", []) or [])
        outputs = list(getattr(model, "output_modalities", []) or [])
        if inputs and "text" not in inputs:
            raise GovernedRosterError(f"governed model lacks text input: {model_id}")
        if outputs and outputs != ["text"]:
            raise GovernedRosterError(f"governed model is not pure text output: {model_id}")
    return selected


def _endpoint_cost(row: Mapping[str, Any], prompt: int, completion: int) -> float:
    return (
        float(row.get("prompt_price_per_million") or 0.0) * prompt
        + float(row.get("completion_price_per_million") or 0.0) * completion
    ) / 1_000_000


def resolve_exact_endpoints(
    validation: Mapping[str, Any],
    models: Mapping[str, Any],
    run: Any,
    task_envelope: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any], Mapping[str, Mapping[str, Any]]]:
    """Resolve one cheapest ZDR endpoint for each fixed roster model identity."""
    prompt_tokens, completion_tokens = _profile(validation)
    required = _required_models(validation)
    selected_models = _model_objects(models, required)
    payloads = fetch_live_endpoint_payloads(
        selected_models,
        run,
        maximum_models=len(selected_models),
        enforce_zdr_for_all=True,
    )
    compact = compact_endpoint_catalog(
        selected_models,
        payloads,
        required_context_tokens=int(task_envelope.get("required_context_tokens") or 1),
        minimum_completion_tokens=completion_tokens,
    )
    compact = dict(compact)
    compact.update(
        {
            "schema_version": "v6-governed-exact-endpoint-catalog-1",
            "selection_authority": "governance-signed-model-roster",
            "model_identity_substitution_allowed": False,
            "provider_resolution_policy": "lowest-estimated-cost-zdr-endpoint-per-fixed-model",
            "zdr_required_for_all_models": True,
            "governance_companies_excluded": [],
        }
    )
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in compact.get("endpoints", []):
        if isinstance(row, Mapping):
            grouped[str(row.get("model") or "")].append(row)

    chosen: dict[str, Mapping[str, Any]] = {}
    for model_id in required:
        rows = grouped.get(model_id, [])
        if not rows:
            raise GovernedRosterError(
                f"governed model has no eligible ZDR endpoint: {model_id}"
            )
        rows = sorted(
            rows,
            key=lambda row: (
                _endpoint_cost(row, prompt_tokens, completion_tokens),
                str(row.get("provider") or ""),
            ),
        )
        winner = dict(rows[0])
        winner["estimated_task_cost_usd"] = round(
            _endpoint_cost(winner, prompt_tokens, completion_tokens), 10
        )
        chosen[model_id] = winner
    compact["chosen_endpoints"] = chosen
    compact["chosen_endpoint_count"] = len(chosen)
    compact["roster_model_count"] = len(required)
    return chosen, compact, payloads


def _request_config(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    supported = {
        str(value).casefold() for value in endpoint.get("supported_parameters", [])
    }
    config: dict[str, Any] = {
        "provider": {
            "only": [str(endpoint["provider"])],
            "order": [str(endpoint["provider"])],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    }
    if "reasoning" in supported:
        config["reasoning"] = {"effort": "medium", "exclude": True}
    return config


def _selected_node(
    member: Mapping[str, Any],
    work: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    task: str,
    final_work_id: str,
    completion_tokens: int,
) -> SelectedNode:
    work_id = str(work["work_id"])
    required_outputs = [str(value) for value in work.get("required_outputs", [])]
    role = str(work.get("role") or member.get("assigned_role") or "expert")
    supported = [str(value) for value in endpoint.get("supported_parameters", [])]
    return SelectedNode(
        node_id=work_id,
        assigned_work=(work_id,),
        professional_capabilities={role: 1.0},
        functions=(role,),
        prompt_profile={
            "modules": [role],
            "role": role,
            "objective": str(work.get("objective") or ""),
            "source": "governance-signed-team-plan",
        },
        reasoning_profile={
            "reasoning_enabled": "reasoning" in {value.casefold() for value in supported},
            "effort": "medium",
        },
        parameter_profile={
            "supported_parameters": supported,
            "recommended_output_allowance_tokens": completion_tokens,
            "selection_source": "governance-signed-lowest-task-cost-roster",
        },
        model=str(member["model_id"]),
        provider_endpoint=str(endpoint["provider_endpoint"]),
        output_contract=work_output_contract(
            task,
            required_outputs,
            final_node=work_id == final_work_id,
        ),
        estimated_quality=float(member.get("balanced_score") or 0.0),
        quality_uncertainty=0.0,
        estimated_cost=float(endpoint["estimated_task_cost_usd"]),
        failure_probability=0.0,
        request_config=_request_config(endpoint),
        independence_group=str(member.get("company") or ""),
    )


def _recovery_row(
    member: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    selected: SelectedNode,
) -> dict[str, Any]:
    return {
        "candidate_id": f"recovery:{selected.node_id}:{member['model_id']}@{endpoint['provider']}",
        "assigned_work": list(selected.assigned_work),
        "professional_capabilities": dict(selected.professional_capabilities),
        "functions": list(selected.functions),
        "prompt_profile": dict(selected.prompt_profile),
        "reasoning_profile": dict(selected.reasoning_profile),
        "parameter_profile": {
            **dict(selected.parameter_profile),
            "supported_parameters": list(endpoint.get("supported_parameters", [])),
        },
        "model": str(member["model_id"]),
        "provider_endpoint": str(endpoint["provider_endpoint"]),
        "provider_slug": str(endpoint["provider"]),
        "output_contract": dict(selected.output_contract),
        "estimated_quality": float(member.get("balanced_score") or 0.0),
        "quality_uncertainty": 0.0,
        "estimated_cost": float(endpoint["estimated_task_cost_usd"]),
        "failure_probability": 0.0,
        "request_config": _request_config(endpoint),
        "independence_group": str(member.get("company") or ""),
    }


def materialize_execution_graph(
    ticket: Mapping[str, Any],
    task: str,
    validation: Mapping[str, Any],
    endpoints: Mapping[str, Mapping[str, Any]],
) -> tuple[ExecutionGraph, GraphLimits, dict[str, Any]]:
    """Construct the exact NetworkX DAG and preassigned recovery pool."""
    prompt_tokens, completion_tokens = _profile(validation)
    del prompt_tokens
    work_items = {str(row["work_id"]): row for row in validation["work_items"]}
    final_work_id = str(validation["final_work_id"])
    dag: nx.DiGraph = validation["work_graph"]
    primary = list(validation["primary_members"])
    recovery = list(validation["recovery_members"])

    selected_nodes: dict[str, SelectedNode] = {}
    for member in primary:
        work_id = str(member["assigned_work_id"])
        model_id = str(member["model_id"])
        selected_nodes[work_id] = _selected_node(
            member,
            work_items[work_id],
            endpoints[model_id],
            task,
            final_work_id,
            completion_tokens,
        )

    critical_order = [final_work_id]
    for generation in reversed(list(nx.topological_generations(dag))):
        for work_id in sorted(str(value) for value in generation):
            if work_id not in critical_order:
                critical_order.append(work_id)
    recovery_pool: dict[str, list[dict[str, Any]]] = {
        work_id: [] for work_id in selected_nodes
    }
    recovery_assignment: list[dict[str, str]] = []
    for index, member in enumerate(recovery):
        target = critical_order[index % len(critical_order)]
        model_id = str(member["model_id"])
        recovery_pool[target].append(
            _recovery_row(member, endpoints[model_id], selected_nodes[target])
        )
        recovery_assignment.append(
            {
                "member_id": str(member.get("member_id") or ""),
                "model_id": model_id,
                "company": str(member.get("company") or ""),
                "assigned_node_id": target,
            }
        )

    edges = tuple(
        SelectedEdge(
            source=str(source),
            target=str(target),
            relation_type="declared-work-dependency",
            payload_type="structured-node-result",
            visibility_policy="declared-edge-only",
        )
        for source, target in sorted(dag.edges())
    )
    stages = tuple(
        tuple(sorted(str(value) for value in generation))
        for generation in nx.topological_generations(dag)
    )
    entry_nodes = tuple(sorted(str(value) for value in dag.nodes if dag.in_degree(value) == 0))
    nodes = tuple(selected_nodes[work_id] for work_id in sorted(selected_nodes))
    graph = ExecutionGraph(
        nodes=nodes,
        edges=edges,
        execution_stages=stages,
        entry_nodes=entry_nodes,
        final_nodes=(final_work_id,),
        required_work=tuple(str(row["work_id"]) for row in validation["work_items"]),
        estimated_quality=0.0,
        quality_floor=0.0,
        estimated_total_cost=round(sum(node.estimated_cost for node in nodes), 10),
        metadata={
            "runtime_version": RUNTIME_VERSION,
            "work_items": [dict(row) for row in validation["work_items"]],
            "recovery_pool": recovery_pool,
            "recovery_assignment": recovery_assignment,
            "governance_roster_sha256": validation["governance_roster"]["roster_sha256"],
            "governance_commit_sha": validation["governance_roster"]["governance_commit_sha"],
            "selection_authority": "governance-signed-roster",
            "orchestration_library": "networkx",
            "orchestration_algorithm": "topological_generations",
            "gpt_planning_calls": 0,
            "claude_red_team_calls": 0,
            "gpt_synthesis_calls": 0,
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_scoring_used": False,
            "optimizer_used": False,
            "cp_sat_used": False,
            "pareto_pruning_used": False,
            "heuristic_ranking_used": False,
            "model_loop_allowed": False,
            "cross_task_history_used": False,
        },
    )
    limits = GraphLimits(
        max_nodes=len(nodes),
        max_edges=max(1, len(edges)),
        max_stages=max(1, len(stages)),
        max_model_calls=len(nodes),
        max_retries=0,
        max_replacements=int(validation["approved_recovery_calls"]),
        max_budget_usd=None,
        min_required_work_coverage=1.0,
        min_successful_content_nodes=max(1, len(nodes) - 1),
        allow_degraded_success=False,
        max_provider_share=1.0,
    )
    issues = [
        issue
        for issue in validate_execution_graph(graph, limits)
        if issue.code != "budget_limit"
    ]
    if issues:
        raise GovernedRosterError(
            "materialized graph is invalid: "
            + "; ".join(f"{issue.code}:{issue.message}" for issue in issues)
        )
    audit = {
        "schema_version": "v6-governed-networkx-materialization-1",
        "status": "PASS",
        "runtime_version": RUNTIME_VERSION,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "stage_count": len(stages),
        "primary_model_count": len(primary),
        "recovery_model_count": len(recovery),
        "all_model_companies_unique": True,
        "recovery_models_assigned_once": len(recovery_assignment) == len(recovery),
        "governance_roster_sha256": validation["governance_roster"]["roster_sha256"],
        "networkx_version_required": "3.6.1",
        "model_calls_before_execution": 0,
        "claude_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
    }
    return graph, limits, audit
