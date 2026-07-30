"""R8 adapter over the stable V5 executor: risk preflight and fault-aware recovery."""
from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

import v5_executor as executor
import v5_resilient_executor as legacy
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode

MIN_DEGRADED_WORK_COVERAGE = legacy.MIN_DEGRADED_WORK_COVERAGE
_ACTIVE_GRAPH: ContextVar[ExecutionGraph | None] = ContextVar("v5_r8_graph", default=None)
_ACTIVE_LIMITS: ContextVar[GraphLimits | None] = ContextVar("v5_r8_limits", default=None)
_INSTALLED = False


def _provider(node: SelectedNode) -> str:
    config = node.request_config.get("provider") if isinstance(node.request_config, Mapping) else None
    if isinstance(config, Mapping):
        values = config.get("only") or config.get("order")
        if isinstance(values, list) and values:
            return str(values[0])
    return node.provider_endpoint.rsplit("@", 1)[-1]


def _candidate(row: Mapping[str, Any], selected: SelectedNode) -> SelectedNode:
    return replace(executor._node_from_candidate(row, selected), node_id=selected.node_id)


def _final_contract(graph: ExecutionGraph) -> Mapping[str, Any]:
    by_id = {node.node_id: node for node in graph.nodes}
    for node_id in graph.final_nodes:
        node = by_id.get(node_id)
        if node and node.output_contract.get("machine_readable_required"):
            return node.output_contract
    return {}


def _strict_json_valid(node: SelectedNode, answer: str | None) -> bool:
    if not node.output_contract.get("machine_readable_required"):
        return True
    try:
        parsed = json.loads(str(answer or ""))
    except json.JSONDecodeError:
        return False
    if not isinstance(parsed, Mapping):
        return False
    required = {str(value) for value in node.output_contract.get("required_fields", [])}
    return required <= set(parsed)


def _failure_class(attempt: executor.NodeAttempt | None, node: SelectedNode) -> str:
    if attempt is None:
        return "budget_denied"
    text = " ".join([
        str(attempt.error or ""),
        " ".join(str(value) for value in attempt.gate_reasons),
    ]).casefold()
    if "429" in text or "rate limit" in text or "too many requests" in text:
        return "rate_limited"
    if any(term in text for term in ("timeout", "timed out", "502", "503", "504", "upstream")):
        return "transient_provider"
    if not str(attempt.answer or "").strip():
        return "empty_output"
    if "truncated-output" in text:
        return "truncated_output"
    if not _strict_json_valid(node, attempt.answer):
        return "invalid_json"
    return "quality_failure"


def _degraded_usable(node: SelectedNode, attempt: executor.NodeAttempt | None) -> bool:
    if attempt is None or not attempt.answer:
        return False
    if node.output_contract.get("machine_readable_required"):
        return False
    text = str(attempt.answer).strip()
    return len(text) >= (260 if "synthesis" in node.functions else 120) and not any(
        value in text.casefold()
        for value in ("i cannot access", "无法访问互联网", "作为ai无法", "没有提供任何答案")
    )


class R8ExecutionBudget:
    """Graph-wide ledger that releases estimates and protects final-node budget."""

    def __init__(
        self,
        max_planned_calls: int,
        max_retries: int,
        max_replacements: int,
        max_budget_usd: float | None,
    ) -> None:
        graph = _ACTIVE_GRAPH.get()
        limits = _ACTIVE_LIMITS.get() or GraphLimits()
        multiplier = max(1.0, float(limits.cost_risk_multiplier))
        by_id = {node.node_id: node for node in graph.nodes} if graph else {}
        self.max_planned_calls = int(max_planned_calls)
        self.max_retries = int(max_retries)
        self.max_replacements = int(max_replacements)
        self.max_budget_usd = max_budget_usd
        self.risk_multiplier = multiplier
        self.calls_reserved = 0
        self.initial_calls_reserved = 0
        self.retries_reserved = 0
        self.replacements_reserved = 0
        self.actual_cost_usd = 0.0
        self.pending: list[float] = []
        self.protected_final = {
            node_id: by_id[node_id].estimated_cost * multiplier
            for node_id in (graph.final_nodes if graph else ())
            if node_id in by_id
        }
        self.denials: list[dict[str, Any]] = []
        self.endpoint_failures: dict[str, int] = {}
        self.endpoint_failure_reasons: dict[str, list[str]] = {}
        self.max_provider_failures = max(1, int(limits.max_provider_failures))
        self._lock = Lock()

    @property
    def maximum_total_calls(self) -> int:
        return self.max_planned_calls + self.max_retries + self.max_replacements

    def reserve(self, kind: str, estimated_cost_usd: float, node_id: str) -> tuple[bool, str]:
        risk = max(0.0, float(estimated_cost_usd)) * self.risk_multiplier
        with self._lock:
            reason = ""
            if kind == "initial" and self.initial_calls_reserved >= self.max_planned_calls:
                reason = "planned-call-limit-exhausted"
            elif kind == "retry" and self.retries_reserved >= self.max_retries:
                reason = "global-retry-limit-exhausted"
            elif kind == "replacement" and self.replacements_reserved >= self.max_replacements:
                reason = "global-replacement-limit-exhausted"
            elif self.calls_reserved >= self.maximum_total_calls:
                reason = "global-total-call-limit-exhausted"
            else:
                protected = sum(self.protected_final.values()) - self.protected_final.get(node_id, 0.0)
                projected = self.actual_cost_usd + sum(self.pending) + risk + protected
                if self.max_budget_usd is not None and projected > self.max_budget_usd + 1e-12:
                    reason = "global-risk-adjusted-budget-exhausted"
            if reason:
                self.denials.append({
                    "node_id": node_id,
                    "kind": kind,
                    "estimated_cost_usd": round(float(estimated_cost_usd), 8),
                    "risk_adjusted_cost_usd": round(risk, 8),
                    "reason": reason,
                })
                return False, reason
            self.calls_reserved += 1
            self.pending.append(risk)
            self.protected_final.pop(node_id, None)
            if kind == "initial":
                self.initial_calls_reserved += 1
            elif kind == "retry":
                self.retries_reserved += 1
            else:
                self.replacements_reserved += 1
            return True, ""

    def reconcile(self, actual_cost_usd: float) -> bool:
        with self._lock:
            if self.pending:
                self.pending.pop(0)
            self.actual_cost_usd += max(0.0, float(actual_cost_usd))
            return bool(
                self.max_budget_usd is not None
                and self.actual_cost_usd > self.max_budget_usd + 1e-12
            )

    def endpoint_available(self, endpoint: str) -> bool:
        with self._lock:
            return self.endpoint_failures.get(endpoint, 0) < self.max_provider_failures

    def fail_endpoint(self, endpoint: str, reason: str) -> None:
        with self._lock:
            self.endpoint_failures[endpoint] = self.endpoint_failures.get(endpoint, 0) + 1
            self.endpoint_failure_reasons.setdefault(endpoint, []).append(reason)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "max_planned_calls": self.max_planned_calls,
                "max_retries": self.max_retries,
                "max_replacements": self.max_replacements,
                "maximum_total_calls": self.maximum_total_calls,
                "max_budget_usd": self.max_budget_usd,
                "risk_multiplier": self.risk_multiplier,
                "calls_reserved": self.calls_reserved,
                "initial_calls_reserved": self.initial_calls_reserved,
                "retries_reserved": self.retries_reserved,
                "replacements_reserved": self.replacements_reserved,
                "estimated_cost_reserved_usd": round(sum(self.pending), 8),
                "protected_final_cost_usd": round(sum(self.protected_final.values()), 8),
                "actual_cost_usd": round(self.actual_cost_usd, 8),
                "denials": list(self.denials),
                "provider_circuit": {
                    "max_failures": self.max_provider_failures,
                    "failures": dict(self.endpoint_failures),
                    "reasons": {
                        key: list(value)
                        for key, value in self.endpoint_failure_reasons.items()
                    },
                },
            }


def _node_result(
    selected: SelectedNode,
    resolved: SelectedNode,
    attempts: list[executor.NodeAttempt],
    attempt: executor.NodeAttempt,
    status: str,
) -> executor.NodeExecutionResult:
    return executor.NodeExecutionResult(
        node_id=selected.node_id,
        assigned_work=selected.assigned_work,
        status=status,
        selected_model=selected.model,
        resolved_model=attempt.response_model or resolved.model,
        provider_endpoint=resolved.provider_endpoint,
        answer=attempt.answer,
        quality_score=attempt.quality_score,
        attempts=attempts,
        actual_cost_usd=round(
            sum(executor._actual_cost({"usage": row.usage}) for row in attempts), 8
        ),
    )


def fault_aware_execute_node(
    selected: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    recovery_rows: Sequence[Mapping[str, Any]],
    budget: R8ExecutionBudget,
) -> executor.NodeExecutionResult:
    attempts: list[executor.NodeAttempt] = []
    best: tuple[executor.NodeAttempt, SelectedNode] | None = None

    def call(node: SelectedNode, kind: str) -> executor.NodeAttempt | None:
        if not budget.endpoint_available(node.provider_endpoint):
            return None
        attempt = executor._reserved_attempt(
            node, selected.node_id, kind, original_task, upstream, run, call_fn,
            len(attempts) + 1, budget,
        )
        if attempt is not None:
            attempts.append(attempt)
            failure = _failure_class(attempt, node)
            if failure in {
                "rate_limited", "transient_provider", "empty_output",
                "truncated_output", "invalid_json",
            }:
                budget.fail_endpoint(node.provider_endpoint, failure)
        return attempt

    initial = call(selected, "initial")
    if initial is not None and initial.status == "passed" and _strict_json_valid(selected, initial.answer):
        return _node_result(selected, selected, attempts, initial, "success")
    if _degraded_usable(selected, initial):
        best = (initial, selected)

    failure = _failure_class(initial, selected)
    if failure == "transient_provider":
        retried = call(selected, "retry")
        if retried is not None and retried.status == "passed" and _strict_json_valid(selected, retried.answer):
            return _node_result(selected, selected, attempts, retried, "success_retried")
        if _degraded_usable(selected, retried) and (
            best is None or retried.quality_score > best[0].quality_score
        ):
            best = (retried, selected)

    alternatives = [_candidate(row, selected) for row in recovery_rows]
    alternatives.sort(key=lambda node: (
        _provider(node) == _provider(selected),
        node.failure_probability,
        node.estimated_cost,
        -node.estimated_quality,
    ))
    for replacement in alternatives:
        attempted = call(replacement, "replacement")
        if attempted is None:
            continue
        if attempted.status == "passed" and _strict_json_valid(replacement, attempted.answer):
            return _node_result(selected, replacement, attempts, attempted, "success_recovered")
        if _degraded_usable(replacement, attempted) and (
            best is None or attempted.quality_score > best[0].quality_score
        ):
            best = (attempted, replacement)

    if best is not None:
        return _node_result(selected, best[1], attempts, best[0], "success_degraded")
    active = alternatives[-1] if alternatives else selected
    return executor.NodeExecutionResult(
        node_id=selected.node_id,
        assigned_work=selected.assigned_work,
        status="failed",
        selected_model=selected.model,
        resolved_model=active.model,
        provider_endpoint=active.provider_endpoint,
        answer=None,
        quality_score=0.0,
        attempts=attempts,
        actual_cost_usd=round(
            sum(executor._actual_cost({"usage": row.usage}) for row in attempts), 8
        ),
    )


def _preflight(graph: ExecutionGraph, limits: GraphLimits) -> tuple[ExecutionGraph, dict[str, Any]]:
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    selected = {node.node_id: node for node in graph.nodes}
    substitutions: list[dict[str, Any]] = []
    blockers: list[str] = []
    for node in graph.nodes:
        if node.failure_probability <= limits.max_node_failure_probability:
            continue
        rows = recovery.get(node.node_id, []) if isinstance(recovery, Mapping) else []
        alternatives = [_candidate(row, node) for row in rows]
        alternatives = [
            item for item in alternatives
            if item.failure_probability < node.failure_probability
        ]
        alternatives.sort(key=lambda item: (
            item.failure_probability, item.estimated_cost, -item.estimated_quality
        ))
        if alternatives:
            selected[node.node_id] = alternatives[0]
            substitutions.append({
                "node_id": node.node_id,
                "reason": "failure-probability-above-production-threshold",
                "from": node.provider_endpoint,
                "to": alternatives[0].provider_endpoint,
            })
        elif node.node_id in graph.final_nodes or "synthesis" not in node.functions:
            blockers.append(f"required-node-risk-above-threshold:{node.node_id}")

    nodes = tuple(selected[node.node_id] for node in graph.nodes)
    adjusted = replace(
        graph,
        nodes=nodes,
        estimated_total_cost=round(sum(node.estimated_cost for node in nodes), 8),
    )
    risk_cost = adjusted.estimated_total_cost * max(1.0, limits.cost_risk_multiplier)
    if limits.max_budget_usd is not None and risk_cost > limits.max_budget_usd + 1e-12:
        blockers.append("preflight-risk-adjusted-cost-above-hard-budget")

    providers: dict[str, int] = {}
    for node in nodes:
        providers[_provider(node)] = providers.get(_provider(node), 0) + 1
    max_share = max(providers.values(), default=0) / max(1, len(nodes))
    if len(nodes) >= 3 and max_share > limits.max_provider_share + 1e-12:
        blockers.append("provider-concentration-above-production-limit")

    return adjusted, {
        "status": "rejected" if blockers else "pass",
        "estimated_initial_cost_usd": adjusted.estimated_total_cost,
        "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
        "max_budget_usd": limits.max_budget_usd,
        "provider_counts": providers,
        "provider_max_share": round(max_share, 6),
        "substitutions": substitutions,
        "blockers": sorted(set(blockers)),
        "policy": "R8 reasoning-inclusive risk preflight before first call",
    }


def _structured_fallback(graph: ExecutionGraph, result: Mapping[str, Any]) -> str | None:
    contract = _final_contract(graph)
    if not contract or result.get("completion_mode") != "degraded":
        return None
    fields = [str(value) for value in contract.get("required_fields", []) if str(value)]
    excerpts = [
        f"{row.get('node_id')}: {str(row.get('answer') or '')[:600]}"
        for row in result.get("node_results", [])
        if row.get("answer") and str(row.get("status", "")).startswith("success")
    ][:8]
    missing = result.get("work_coverage", {}).get("missing_work_ids", [])
    payload: dict[str, list[str]] = {}
    for field in fields:
        if field in {"conclusions", "final_recommendation", "agreements"}:
            payload[field] = excerpts or ["无可用节点结论"]
        elif field in {"uncertainties", "evidence_gaps", "verification_limits"}:
            payload[field] = ["降级合成；未覆盖工作：" + ("、".join(missing) if missing else "无")]
        else:
            payload[field] = ["降级合成结果；详见conclusions字段。"]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _write_rejection(
    root: Path | None,
    graph: ExecutionGraph,
    limits: GraphLimits,
    preflight: Mapping[str, Any],
) -> None:
    if root is None:
        return
    root.mkdir(parents=True, exist_ok=True)
    result = {
        "version": 5,
        "status": "failed",
        "completion_mode": "none",
        "quality_status": "failed",
        "execution_stages": [],
        "node_results": [],
        "final_node_ids": list(graph.final_nodes),
        "final_answer": None,
        "actual_cost_usd": 0.0,
        "recovery_used": False,
        "execution_budget": {
            "max_budget_usd": limits.max_budget_usd,
            "actual_cost_usd": 0.0,
            "calls_reserved": 0,
            "denials": [{"reason": "r8-preflight-rejected"}],
        },
        "cost_preflight": dict(preflight),
        "stop_reason": "r8-preflight-rejected",
    }
    executor._write_json(root / "v5-node-results.json", [])
    executor._write_json(
        root / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    executor._write_json(root / "v5-request-audit.json", {
        "status": "PASS",
        "request_count": 0,
        "requests": [],
        "external_tools_allowed": False,
        "preflight_blockers": list(preflight.get("blockers", [])),
    })
    (root / "v5-final-report.md").write_text("# V5 execution rejected before calls\n", encoding="utf-8")


def resilient_execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Any | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    adjusted, preflight = _preflight(graph, limits)
    root = Path(output_dir) if output_dir is not None else None
    if preflight["blockers"]:
        _write_rejection(root, adjusted, limits, preflight)
        raise executor.V5ExecutionError(
            "V5 graph rejected before model calls: " + ", ".join(preflight["blockers"])
        )

    graph_token = _ACTIVE_GRAPH.set(adjusted)
    limits_token = _ACTIVE_LIMITS.set(limits)
    try:
        result = legacy.resilient_execute_v5_graph(
            adjusted,
            run,
            original_task,
            call_fn=call_fn,
            output_dir=output_dir,
            limits=limits,
        )
    finally:
        _ACTIVE_LIMITS.reset(limits_token)
        _ACTIVE_GRAPH.reset(graph_token)

    result["executor"] = "v5-r8-fault-aware"
    result["cost_preflight"] = preflight
    structured = _structured_fallback(adjusted, result)
    if structured is not None:
        result["final_answer"] = structured
        result["degradation"]["mode"] = "deterministic-schema-preserving-synthesis"
    if root is not None:
        executor._write_json(
            root / "v5-execution-summary.json",
            {key: value for key, value in result.items() if key != "node_results"},
        )
        (root / "v5-final-report.md").write_text(
            str(result.get("final_answer") or "# V5 execution failed\n"),
            encoding="utf-8",
        )
    return result


def _patch_loaded_callers() -> None:
    for module_name in (
        "v5_pipeline",
        "v5_live_benchmark",
        "v5_live_benchmark_hardened",
        "v5_live_benchmark_economy",
    ):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "execute_v5_graph"):
            setattr(module, "execute_v5_graph", resilient_execute_v5_graph)


def install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        executor.ExecutionBudget = R8ExecutionBudget
        executor._execute_node = fault_aware_execute_node
        executor.execute_v5_graph = resilient_execute_v5_graph
        _INSTALLED = True
    _patch_loaded_callers()
