"""Production-resilient V5 execution path.

This module deliberately leaves the original V5 executor intact as a rollback
surface. The production/benchmark entry can install this executor explicitly.
It converts the R6 all-or-nothing DAG into a coverage-qualified DAG while
preserving strict tool prohibition, explicit provider routing and hard budgets.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph
from v5_executor import (
    FORBIDDEN_FIELDS,
    V5ExecutionError,
    _actual_cost,
    _default_call,
    _extract_answer,
    _finish_reason,
    _node_from_candidate,
    build_node_payload,
)

_OPTIONAL_FUNCTIONS = {"review", "adversarial", "red_team", "supplement", "correction", "comparison"}
_FINAL_FUNCTIONS = {"synthesis", "adjudication", "delivery", "final", "formatting"}
_TRANSIENT = (
    "429", "rate limit", "too many requests", "timeout", "timed out",
    "502", "503", "504", "upstream", "temporarily unavailable", "connection reset",
)


@dataclass
class _Budget:
    max_initial: int
    max_retries: int
    max_replacements: int
    max_usd: float | None
    multiplier: float
    initial: int = 0
    retries: int = 0
    replacements: int = 0
    calls: int = 0
    reserved_usd: float = 0.0
    actual_usd: float = 0.0
    denials: list[dict[str, Any]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock, repr=False)

    def reserve(self, kind: str, node_id: str, estimate: float) -> tuple[bool, float, str]:
        reserve = max(0.0, float(estimate)) * max(1.0, self.multiplier)
        with self.lock:
            reason = ""
            if kind == "initial" and self.initial >= self.max_initial:
                reason = "planned-call-limit-exhausted"
            elif kind == "retry" and self.retries >= self.max_retries:
                reason = "global-retry-limit-exhausted"
            elif kind == "replacement" and self.replacements >= self.max_replacements:
                reason = "global-replacement-limit-exhausted"
            elif self.max_usd is not None and self.actual_usd + self.reserved_usd + reserve > self.max_usd + 1e-12:
                reason = "global-risk-adjusted-budget-exhausted"
            if reason:
                self.denials.append({
                    "node_id": node_id,
                    "kind": kind,
                    "estimated_cost_usd": round(float(estimate), 8),
                    "risk_adjusted_cost_usd": round(reserve, 8),
                    "reason": reason,
                })
                return False, 0.0, reason
            self.calls += 1
            self.reserved_usd += reserve
            if kind == "initial":
                self.initial += 1
            elif kind == "retry":
                self.retries += 1
            else:
                self.replacements += 1
            return True, reserve, ""

    def reconcile(self, reserved: float, actual: float) -> bool:
        with self.lock:
            self.reserved_usd = max(0.0, self.reserved_usd - max(0.0, reserved))
            self.actual_usd += max(0.0, actual)
            return self.max_usd is not None and self.actual_usd > self.max_usd + 1e-12

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "max_planned_calls": self.max_initial,
                "max_retries": self.max_retries,
                "max_replacements": self.max_replacements,
                "max_budget_usd": self.max_usd,
                "risk_multiplier": self.multiplier,
                "calls_reserved": self.calls,
                "initial_calls_reserved": self.initial,
                "retries_reserved": self.retries,
                "replacements_reserved": self.replacements,
                "estimated_cost_reserved_usd": round(self.reserved_usd, 8),
                "actual_cost_usd": round(self.actual_usd, 8),
                "denials": list(self.denials),
            }


@dataclass
class _Circuit:
    maximum: int
    failures: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    lock: Lock = field(default_factory=Lock, repr=False)

    def available(self, endpoint: str) -> bool:
        with self.lock:
            return self.failures.get(endpoint, 0) < max(1, self.maximum)

    def fail(self, endpoint: str, reason: str) -> None:
        with self.lock:
            self.failures[endpoint] = self.failures.get(endpoint, 0) + 1
            self.reasons.setdefault(endpoint, []).append(reason)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "max_failures": self.maximum,
                "failures": dict(self.failures),
                "reasons": {key: list(value) for key, value in self.reasons.items()},
                "blocked_endpoints": sorted(
                    endpoint for endpoint, count in self.failures.items()
                    if count >= max(1, self.maximum)
                ),
            }


def _limit(limits: GraphLimits, name: str, default: Any) -> Any:
    return getattr(limits, name, default)


def _criticality(graph: ExecutionGraph, node: SelectedNode) -> str:
    configured = graph.metadata.get("node_criticality", {}) if isinstance(graph.metadata, Mapping) else {}
    if isinstance(configured, Mapping) and configured.get(node.node_id) in {"required", "optional", "final"}:
        return str(configured[node.node_id])
    functions = {str(value).casefold() for value in node.functions}
    if node.node_id in graph.final_nodes or functions & _FINAL_FUNCTIONS:
        return "final"
    if functions and functions <= _OPTIONAL_FUNCTIONS:
        return "optional"
    return "required"


def _provider(node: SelectedNode) -> str:
    provider = node.request_config.get("provider") if isinstance(node.request_config, Mapping) else None
    if isinstance(provider, Mapping):
        values = provider.get("order") or provider.get("only")
        if isinstance(values, list) and values:
            return str(values[0])
    return node.provider_endpoint.rsplit("@", 1)[-1]


def _json_object(text: str) -> Mapping[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, Mapping) else None
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(candidate[start:], start):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(candidate[start:index + 1])
                    return parsed if isinstance(parsed, Mapping) else None
                except json.JSONDecodeError:
                    return None
    return None


def _assess(node: SelectedNode, response: Mapping[str, Any], answer: str) -> dict[str, Any]:
    finish = _finish_reason(response).casefold()
    reasons: list[str] = []
    minimum = 280 if {str(x).casefold() for x in node.functions} & _FINAL_FUNCTIONS else 100
    if not answer.strip():
        reasons.append("empty-output")
    elif len(answer) < minimum:
        reasons.append(f"answer-too-short<{minimum}")
    folded = answer.casefold()
    if any(term in folded for term in ("i cannot access", "无法访问互联网", "作为ai无法", "没有提供任何答案")):
        reasons.append("non-delivery-or-tool-dependency")
    truncated = finish in {"length", "max_tokens"}
    if truncated:
        reasons.append("truncated-output")
    required_fields = [str(value) for value in node.output_contract.get("required_fields", [])]
    field_hits = sum(field.casefold() in folded or field.replace("_", " ").casefold() in folded for field in required_fields)
    machine = bool(node.output_contract.get("machine_readable_required"))
    parsed = _json_object(answer) if machine else None
    if machine and parsed is None:
        reasons.append("invalid-required-json")
    completeness = min(1.0, len(answer) / max(1, minimum * 3))
    contract = field_hits / max(1, len(required_fields))
    score = max(0.0, min(1.0, 0.55 * completeness + 0.25 * contract + (0.0 if truncated else 0.20)))
    threshold = max(0.45, min(0.78, 0.48 + 0.18 * node.estimated_quality - 0.08 * node.quality_uncertainty))
    passed = not reasons and score + 1e-12 >= threshold
    usable = bool(
        answer.strip()
        and len(answer) >= 80
        and "non-delivery-or-tool-dependency" not in reasons
        and (not machine or parsed is not None or len(answer) >= 180)
    )
    failure_class = None
    if not usable:
        if not answer.strip():
            failure_class = "empty_output"
        elif truncated:
            failure_class = "truncated_output"
        elif machine and parsed is None:
            failure_class = "invalid_json"
        else:
            failure_class = "quality_failure"
    elif not passed:
        failure_class = "degraded_output"
    normalized = json.dumps(parsed, ensure_ascii=False) if parsed is not None else answer.strip()
    return {
        "passed": passed,
        "usable": usable,
        "score": round(score, 6),
        "reasons": reasons + ([] if score + 1e-12 >= threshold else [f"quality-score<{threshold:.3f}"]),
        "answer": normalized,
        "failure_class": failure_class,
    }


def _transient(error: str) -> bool:
    folded = error.casefold()
    return any(term in folded for term in _TRANSIENT)


def _clip_upstream(rows: Sequence[Mapping[str, Any]], limits: GraphLimits) -> list[dict[str, Any]]:
    per_node = max(500, int(_limit(limits, "max_upstream_chars_per_node", 6000)))
    total = max(per_node, int(_limit(limits, "max_total_upstream_chars", 24000)))
    result: list[dict[str, Any]] = []
    consumed = 0
    for row in sorted(rows, key=lambda value: (-float(value.get("quality_score", 0.0)), str(value.get("node_id")))):
        remaining = total - consumed
        if remaining <= 0:
            break
        answer = str(row.get("answer") or "")[: min(per_node, remaining)]
        if not answer:
            continue
        result.append({"node_id": row.get("node_id"), "answer": answer, "quality_score": row.get("quality_score")})
        consumed += len(answer)
    return result


def _call_attempt(
    node: SelectedNode,
    selected_id: str,
    kind: str,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    budget: _Budget,
    circuit: _Circuit,
    attempt_index: int,
) -> dict[str, Any] | None:
    if not circuit.available(node.provider_endpoint):
        return None
    allowed, reserved, reason = budget.reserve(kind, selected_id, node.estimated_cost)
    if not allowed:
        return {"status": "denied", "usable": False, "reason": reason, "attempt_index": attempt_index}
    payload = build_node_payload(node, original_task, upstream)
    started = time.monotonic()
    try:
        response, latency = call_fn(run, payload)
        answer = _extract_answer(response)
        assessment = _assess(node, response, answer)
        actual = _actual_cost(response)
        exceeded = budget.reconcile(reserved, actual)
        if exceeded:
            assessment["passed"] = False
            assessment["usable"] = False
            assessment["reasons"].append("actual-budget-exceeded")
            assessment["failure_class"] = "budget_exceeded"
        if assessment["failure_class"] in {"empty_output", "truncated_output", "invalid_json"}:
            circuit.fail(node.provider_endpoint, str(assessment["failure_class"]))
        return {
            "attempt_index": attempt_index,
            "candidate_id": node.node_id,
            "model": node.model,
            "provider_endpoint": node.provider_endpoint,
            "request": payload,
            "status": "passed" if assessment["passed"] else "degraded" if assessment["usable"] else "quality_gate_failed",
            "answer": assessment["answer"] or None,
            "quality_score": assessment["score"],
            "gate_reasons": assessment["reasons"],
            "latency_seconds": round(float(latency), 6),
            "usage": dict(response.get("usage") or {}) if isinstance(response.get("usage"), Mapping) else {},
            "response_id": str(response.get("id") or "") or None,
            "response_model": str(response.get("model") or node.model) or None,
            "response_provider": str(response.get("provider") or "") or None,
            "error": None,
            "replacement": kind == "replacement",
            "retry": kind == "retry",
            "usable": assessment["usable"],
            "failure_class": assessment["failure_class"],
            "actual_cost_usd": round(actual, 8),
        }
    except Exception as exc:  # noqa: BLE001
        budget.reconcile(reserved, 0.0)
        error = str(exc)
        failure_class = "transient_provider" if _transient(error) else "call_failure"
        if failure_class == "transient_provider":
            circuit.fail(node.provider_endpoint, error)
        return {
            "attempt_index": attempt_index,
            "candidate_id": node.node_id,
            "model": node.model,
            "provider_endpoint": node.provider_endpoint,
            "request": payload,
            "status": "call_failed",
            "answer": None,
            "quality_score": 0.0,
            "gate_reasons": ["call-failed"],
            "latency_seconds": round(time.monotonic() - started, 6),
            "usage": {},
            "response_id": None,
            "response_model": None,
            "response_provider": None,
            "error": error,
            "replacement": kind == "replacement",
            "retry": kind == "retry",
            "usable": False,
            "failure_class": failure_class,
            "actual_cost_usd": 0.0,
        }


def _execute_node(
    selected: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
    run: Any,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
    recovery_rows: Sequence[Mapping[str, Any]],
    budget: _Budget,
    circuit: _Circuit,
    limits: GraphLimits,
    criticality: str,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    candidates = [selected]
    alternatives = [_node_from_candidate(row, selected) for row in recovery_rows]
    alternatives.sort(key=lambda node: (
        _provider(node) == _provider(selected),
        node.failure_probability,
        -node.estimated_quality,
        node.estimated_cost,
    ))
    candidates.extend(alternatives)
    maximum = 1 + max(0, int(limits.max_replacements))
    for index, candidate in enumerate(candidates[:maximum], 1):
        kind = "initial" if index == 1 else "replacement"
        attempt = _call_attempt(
            candidate, selected.node_id, kind, original_task, upstream,
            run, call_fn, budget, circuit, index,
        )
        if attempt is None:
            continue
        attempts.append(attempt)
        if attempt.get("usable"):
            status = "success" if attempt["status"] == "passed" else "degraded"
            if index > 1 and status == "success":
                status = "success_recovered"
            return {
                "node_id": selected.node_id,
                "assigned_work": list(selected.assigned_work),
                "status": status,
                "selected_model": selected.model,
                "resolved_model": attempt.get("response_model") or candidate.model,
                "provider_endpoint": candidate.provider_endpoint,
                "answer": attempt.get("answer"),
                "quality_score": attempt.get("quality_score", 0.0),
                "attempts": attempts,
                "actual_cost_usd": round(sum(float(row.get("actual_cost_usd", 0.0)) for row in attempts), 8),
                "criticality": criticality,
                "usable": True,
                "failure_class": attempt.get("failure_class"),
            }
    last = attempts[-1] if attempts else {}
    if attempts and int(limits.max_retries) > 0 and circuit.available(selected.provider_endpoint):
        attempt = _call_attempt(
            selected, selected.node_id, "retry", original_task, upstream,
            run, call_fn, budget, circuit, len(attempts) + 1,
        )
        if attempt is not None:
            attempts.append(attempt)
            if attempt.get("usable"):
                return {
                    "node_id": selected.node_id,
                    "assigned_work": list(selected.assigned_work),
                    "status": "success_retried" if attempt["status"] == "passed" else "degraded",
                    "selected_model": selected.model,
                    "resolved_model": attempt.get("response_model") or selected.model,
                    "provider_endpoint": selected.provider_endpoint,
                    "answer": attempt.get("answer"),
                    "quality_score": attempt.get("quality_score", 0.0),
                    "attempts": attempts,
                    "actual_cost_usd": round(sum(float(row.get("actual_cost_usd", 0.0)) for row in attempts), 8),
                    "criticality": criticality,
                    "usable": True,
                    "failure_class": attempt.get("failure_class"),
                }
            last = attempt
    return {
        "node_id": selected.node_id,
        "assigned_work": list(selected.assigned_work),
        "status": "failed",
        "selected_model": selected.model,
        "resolved_model": last.get("response_model") or selected.model,
        "provider_endpoint": last.get("provider_endpoint") or selected.provider_endpoint,
        "answer": None,
        "quality_score": 0.0,
        "attempts": attempts,
        "actual_cost_usd": round(sum(float(row.get("actual_cost_usd", 0.0)) for row in attempts), 8),
        "criticality": criticality,
        "usable": False,
        "failure_class": last.get("failure_class") or "unrecovered_failure",
    }


def _replacement(row: Mapping[str, Any], selected: SelectedNode) -> SelectedNode:
    return replace(_node_from_candidate(row, selected), node_id=selected.node_id)


def _preflight(graph: ExecutionGraph, limits: GraphLimits) -> dict[str, Any]:
    recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
    selected = {node.node_id: node for node in graph.nodes}
    substitutions: list[dict[str, Any]] = []
    threshold = float(_limit(limits, "max_node_failure_probability", 0.18))
    for node in graph.nodes:
        if node.failure_probability <= threshold:
            continue
        rows = recovery.get(node.node_id, []) if isinstance(recovery, Mapping) else []
        candidates = [_replacement(row, node) for row in rows]
        candidates = [row for row in candidates if row.failure_probability < node.failure_probability]
        candidates.sort(key=lambda row: (row.failure_probability, -row.estimated_quality, row.estimated_cost))
        if candidates:
            selected[node.node_id] = candidates[0]
            substitutions.append({
                "node_id": node.node_id,
                "reason": "failure-probability-above-production-threshold",
                "from": node.provider_endpoint,
                "to": candidates[0].provider_endpoint,
            })

    max_share = float(_limit(limits, "max_provider_share", 0.50))
    if len(selected) > 1 and 0.0 < max_share < 1.0:
        for _ in range(len(selected)):
            counts: dict[str, int] = {}
            for node in selected.values():
                counts[_provider(node)] = counts.get(_provider(node), 0) + 1
            provider, count = max(counts.items(), key=lambda item: item[1])
            if count / len(selected) <= max_share + 1e-12:
                break
            changed = False
            for node_id, node in sorted(selected.items(), key=lambda item: item[1].estimated_cost):
                if _provider(node) != provider:
                    continue
                rows = recovery.get(node_id, []) if isinstance(recovery, Mapping) else []
                alternatives = [_replacement(row, node) for row in rows]
                alternatives = [row for row in alternatives if _provider(row) != provider]
                alternatives.sort(key=lambda row: (row.failure_probability, row.estimated_cost, -row.estimated_quality))
                if alternatives:
                    selected[node_id] = alternatives[0]
                    substitutions.append({
                        "node_id": node_id,
                        "reason": "provider-concentration-rebalance",
                        "from": node.provider_endpoint,
                        "to": alternatives[0].provider_endpoint,
                    })
                    changed = True
                    break
            if not changed:
                break

    multiplier = max(1.0, float(_limit(limits, "cost_risk_multiplier", 4.0)))
    active = set(selected)
    pruned: list[dict[str, Any]] = []

    def upper() -> float:
        return sum(selected[node_id].estimated_cost for node_id in active) * multiplier

    if limits.max_budget_usd is not None and upper() > limits.max_budget_usd + 1e-12:
        optional = [node for node in selected.values() if _criticality(graph, node) == "optional"]
        optional.sort(key=lambda node: (node.estimated_quality / max(node.estimated_cost, 1e-9), node.estimated_quality))
        for node in optional:
            if upper() <= limits.max_budget_usd + 1e-12:
                break
            active.discard(node.node_id)
            pruned.append({"node_id": node.node_id, "reason": "risk-adjusted-budget-prune-optional"})

    blockers: list[str] = []
    if limits.max_budget_usd is not None and upper() > limits.max_budget_usd + 1e-12:
        blockers.append("preflight-risk-adjusted-cost-above-hard-budget")
    required_nodes = [node_id for node_id in active if _criticality(graph, selected[node_id]) != "optional"]
    if not required_nodes:
        blockers.append("no-required-or-final-node-remains")
    return {
        "selected_nodes": selected,
        "active_node_ids": active,
        "substitutions": substitutions,
        "pruned_nodes": pruned,
        "risk_adjusted_cost_upper_usd": round(upper(), 8),
        "estimated_cost_usd": round(sum(selected[node_id].estimated_cost for node_id in active), 8),
        "blockers": blockers,
    }


def _serializable_preflight(preflight: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in preflight.items() if key not in {"selected_nodes", "active_node_ids"}},
        "selected_nodes": {node_id: node.to_dict() for node_id, node in preflight["selected_nodes"].items()},
        "active_node_ids": sorted(preflight["active_node_ids"]),
    }


def _fallback(outputs: Mapping[str, Mapping[str, Any]], graph: ExecutionGraph, missing: Sequence[str]) -> str:
    usable = [row for row in outputs.values() if row.get("usable") and row.get("answer")]
    usable.sort(key=lambda row: (
        0 if row.get("criticality") == "final" else 1,
        -float(row.get("quality_score", 0.0)),
        str(row.get("node_id")),
    ))
    if not usable:
        return ""
    sections = [
        "# V5降级合成结果",
        "本结果由已成功节点确定性合成；未覆盖部分已显式标注，不代表全图完整成功。",
    ]
    if missing:
        sections.append("\n## 未覆盖工作\n" + "、".join(missing))
    for row in usable:
        sections.append(
            f"\n## 节点 {row['node_id']}（工作：{', '.join(row.get('assigned_work', []))}）\n{row['answer']}"
        )
    return "\n".join(sections)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def execute_v5_graph(
    graph: ExecutionGraph | Mapping[str, Any],
    run: Any,
    original_task: str,
    *,
    call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]] | None = None,
    output_dir: str | Path | None = None,
    limits: GraphLimits | None = None,
) -> dict[str, Any]:
    """Execute a V5 DAG with pre-call budget safety and degraded delivery."""
    graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
    limits = limits or GraphLimits()
    issues = validate_execution_graph(graph, limits)
    if issues:
        raise V5ExecutionError("Invalid execution graph: " + "; ".join(f"{x.code}:{x.message}" for x in issues))
    root = Path(output_dir) if output_dir is not None else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)

    preflight = _preflight(graph, limits)
    if root is not None:
        _write_json(root / "v5-preflight.json", _serializable_preflight(preflight))
    selected: dict[str, SelectedNode] = dict(preflight["selected_nodes"])
    active: set[str] = set(preflight["active_node_ids"])
    budget = _Budget(
        min(limits.max_model_calls, len(active)), limits.max_retries, limits.max_replacements,
        limits.max_budget_usd, float(_limit(limits, "cost_risk_multiplier", 4.0)),
    )
    circuit = _Circuit(int(_limit(limits, "max_provider_failures", 1)))
    outputs: dict[str, dict[str, Any]] = {}
    stages: list[dict[str, Any]] = []
    for node in graph.nodes:
        if node.node_id not in active:
            outputs[node.node_id] = {
                "node_id": node.node_id,
                "assigned_work": list(node.assigned_work),
                "status": "pruned_preflight",
                "selected_model": node.model,
                "resolved_model": None,
                "provider_endpoint": node.provider_endpoint,
                "answer": None,
                "quality_score": 0.0,
                "attempts": [],
                "actual_cost_usd": 0.0,
                "criticality": _criticality(graph, node),
                "usable": False,
                "failure_class": "preflight_pruned",
            }

    blocked = bool(preflight["blockers"])
    if not blocked:
        call = call_fn or _default_call
        incoming: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
        for edge in graph.edges:
            incoming[edge.target].append(edge.source)
        recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
        for stage_index, stage in enumerate(graph.execution_stages):
            node_ids = [node_id for node_id in stage if node_id in active]
            if not node_ids:
                stages.append({"stage_index": stage_index, "node_ids": list(stage), "status": "skipped"})
                continue
            tight = bool(
                limits.max_budget_usd is not None
                and limits.max_budget_usd <= max(float(preflight["risk_adjusted_cost_upper_usd"]), 1e-9) * 1.15
            )
            configured = int(getattr(run, "parallel_workers", len(node_ids) or 1))
            workers = 1 if tight else min(max(1, configured), len(node_ids))
            futures = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for node_id in node_ids:
                    upstream = _clip_upstream([
                        outputs[source]
                        for source in incoming[node_id]
                        if source in outputs and outputs[source].get("usable")
                    ], limits)
                    rows = list(recovery.get(node_id, [])) if isinstance(recovery, Mapping) else []
                    node = selected[node_id]
                    futures[pool.submit(
                        _execute_node, node, original_task, upstream, run, call, rows,
                        budget, circuit, limits, _criticality(graph, node),
                    )] = node_id
                rows = [future.result() for future in as_completed(futures)]
            rows.sort(key=lambda row: str(row["node_id"]))
            for row in rows:
                outputs[str(row["node_id"])] = row
            failed = [row["node_id"] for row in rows if not row.get("usable")]
            degraded_nodes = [row["node_id"] for row in rows if row.get("status") == "degraded"]
            stages.append({
                "stage_index": stage_index,
                "node_ids": list(stage),
                "active_node_ids": node_ids,
                "failed_node_ids": failed,
                "degraded_node_ids": degraded_nodes,
                "parallel_workers": workers,
                "status": "failed" if len(failed) == len(node_ids) else "degraded" if failed or degraded_nodes else "success",
            })

    required = set(graph.required_work)
    covered = {
        work for row in outputs.values() if row.get("usable")
        for work in row.get("assigned_work", [])
    }
    coverage = 1.0 if not required else len(required & covered) / len(required)
    missing = sorted(required - covered)
    final_rows = [outputs[node_id] for node_id in graph.final_nodes if outputs.get(node_id, {}).get("usable")]
    final_rows.sort(key=lambda row: (-float(row.get("quality_score", 0.0)), str(row.get("node_id"))))
    final_answer = "\n\n".join(str(row.get("answer") or "") for row in final_rows).strip()
    if len(final_answer) < 160:
        final_answer = _fallback(outputs, graph, missing).strip()
    usable_count = sum(bool(row.get("usable")) for row in outputs.values())
    all_active_passed = bool(active) and all(str(outputs.get(node_id, {}).get("status", "")).startswith("success") for node_id in active)
    deliverable = bool(
        not blocked
        and len(final_answer) >= 160
        and usable_count >= int(_limit(limits, "min_successful_nodes", 1))
        and coverage + 1e-12 >= float(_limit(limits, "min_required_work_coverage", 0.66))
    )
    degraded = bool(deliverable and (not all_active_passed or missing or preflight["pruned_nodes"] or not final_rows))
    accepted = deliverable and (bool(_limit(limits, "allow_degraded_success", True)) or not degraded)
    result = {
        "version": 5,
        "executor": "v5-resilient-coverage-qualified",
        "status": "success" if accepted else "failed",
        "completion_class": "degraded_success" if accepted and degraded else "full_success" if accepted else "failed",
        "degraded": degraded,
        "execution_stages": stages,
        "node_results": [outputs[node_id] for node_id in sorted(outputs)],
        "final_node_ids": list(graph.final_nodes),
        "final_answer": final_answer or None,
        "actual_cost_usd": round(sum(float(row.get("actual_cost_usd", 0.0)) for row in outputs.values()), 8),
        "required_work_coverage": round(coverage, 6),
        "covered_work": sorted(covered),
        "missing_work": missing,
        "successful_node_count": usable_count,
        "active_node_count": len(active),
        "recovery_used": any(
            attempt.get("replacement") or attempt.get("retry")
            for row in outputs.values() for attempt in row.get("attempts", [])
        ),
        "preflight": _serializable_preflight(preflight),
        "provider_circuit": circuit.snapshot(),
        "execution_budget": budget.snapshot(),
        "stop_reason": (
            "full-quality-gates-passed" if accepted and not degraded
            else "coverage-qualified-degraded-delivery" if accepted
            else "preflight-blocked" if blocked
            else "insufficient-coverage-or-no-deliverable-answer"
        ),
    }
    if root is not None:
        _write_json(root / "v5-node-results.json", result["node_results"])
        _write_json(root / "v5-execution-summary.json", {key: value for key, value in result.items() if key != "node_results"})
        requests = [attempt.get("request", {}) for row in outputs.values() for attempt in row.get("attempts", [])]
        _write_json(root / "v5-request-audit.json", {
            "status": "PASS" if all(not FORBIDDEN_FIELDS.intersection(request) for request in requests) else "FAIL",
            "request_count": len(requests),
            "requests": requests,
            "artificial_token_ceiling_sent": any(
                "max_tokens" in request or "max_completion_tokens" in request for request in requests
            ),
            "external_tools_allowed": False,
            "global_limits": result["execution_budget"],
            "preflight_blockers": list(preflight["blockers"]),
        })
        (root / "v5-final-report.md").write_text(final_answer or "# V5 execution failed\n", encoding="utf-8")
    if not accepted:
        raise V5ExecutionError(
            "V5 execution did not meet the production delivery gate: "
            + ", ".join(preflight["blockers"] or [result["stop_reason"]])
        )
    return result
