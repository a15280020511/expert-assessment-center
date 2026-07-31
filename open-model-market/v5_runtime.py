"""Explicit V5 production runtime with no global monkey patching.

The runtime owns the immutable configuration, per-run catalog snapshot, call
budget, provider circuit, retry/replacement state machine, prompt/output
contracts, quality gates, and evidence writes.  Importing this module does not
modify any module-level function or class.
"""
from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping, Sequence

import v5_cost_reliability_hardening as cost_hardening
import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_executor as legacy_executor
import v5_output_contract_delivery as output_contract
import v5_quality_status_integrity as quality_integrity
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from execution_graph_validator import validate_execution_graph
from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json
from v5_planning_runtime import PlannerPolicy

RUNTIME_VERSION = "v5-native-runtime-1"
MIN_DEGRADED_WORK_COVERAGE = 2.0 / 3.0
STRICT_SUCCESS_STATUSES = {"success", "success_retried", "success_recovered"}


class FailureCategory(str, Enum):
    CATALOG_UNAVAILABLE = "CATALOG_UNAVAILABLE"
    CATALOG_SCHEMA_CHANGED = "CATALOG_SCHEMA_CHANGED"
    PLANNER_INFEASIBLE = "PLANNER_INFEASIBLE"
    BUDGET_INSUFFICIENT = "BUDGET_INSUFFICIENT"
    CALL_LIMIT_EXHAUSTED = "CALL_LIMIT_EXHAUSTED"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_EMPTY_RESPONSE = "PROVIDER_EMPTY_RESPONSE"
    PROVIDER_INVALID_RESPONSE = "PROVIDER_INVALID_RESPONSE"
    UNSUPPORTED_PARAMETER = "UNSUPPORTED_PARAMETER"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    ARTIFACT_FAILURE = "ARTIFACT_FAILURE"
    INTERNAL_CONTRACT_VIOLATION = "INTERNAL_CONTRACT_VIOLATION"


@dataclass(frozen=True)
class ExecutionFailure:
    category: FailureCategory
    retryable: bool
    http_status: int | None = None
    retry_after_seconds: float | None = None
    model: str | None = None
    provider_endpoint: str | None = None
    request_sent: bool = False
    response_received: bool = False
    usage_received: bool = False
    actual_cost_usd: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["category"] = self.category.value
        return value


@dataclass(frozen=True)
class RuntimeConfig:
    total_call_limit: int
    recovery_call_limit: int
    cost_anomaly_usd: float | None
    quality_tier: str
    tools_allowed: bool = False
    live_catalog_required: bool = False
    provider_lock_required: bool = True
    maximum_candidates_per_work: int = 12
    solver_timeout_seconds: float = 20.0
    cost_risk_multiplier: float = 1.18
    max_provider_failures: int = 2

    def __post_init__(self) -> None:
        if not 1 <= int(self.total_call_limit) <= 16:
            raise ValueError("total_call_limit must be between 1 and 16")
        if not 0 <= int(self.recovery_call_limit) < int(self.total_call_limit):
            raise ValueError("recovery_call_limit must be non-negative and below total_call_limit")
        if self.cost_anomaly_usd is not None and (
            not math.isfinite(float(self.cost_anomaly_usd))
            or float(self.cost_anomaly_usd) <= 0
        ):
            raise ValueError("cost_anomaly_usd must be finite and positive")
        if self.quality_tier not in {"budget", "value", "quality"}:
            raise ValueError("quality_tier must be budget, value, or quality")
        if self.tools_allowed:
            raise ValueError("V5 expert runtime forbids external tools")
        if not self.provider_lock_required:
            raise ValueError("V5 production runtime requires explicit provider lock")
        if int(self.maximum_candidates_per_work) < 2:
            raise ValueError("maximum_candidates_per_work must be at least 2")
        if float(self.solver_timeout_seconds) < 1.0:
            raise ValueError("solver_timeout_seconds must be at least 1")

    @property
    def initial_call_limit(self) -> int:
        return int(self.total_call_limit) - int(self.recovery_call_limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "initial_call_limit": self.initial_call_limit,
            "runtime_version": RUNTIME_VERSION,
        }


@dataclass(frozen=True)
class TicketPolicy:
    fail_closed: bool = True
    alternate_runtime_allowed: bool = False


@dataclass(frozen=True)
class CatalogPolicy:
    read_once_per_run: bool = True
    reject_router_models: bool = True
    reject_missing_price: bool = True
    reject_missing_context: bool = True


@dataclass(frozen=True)
class BudgetPolicy:
    shared_recovery_pool: bool = True


@dataclass(frozen=True)
class CostPolicy:
    billed_usage_not_allowance: bool = True


@dataclass(frozen=True)
class ProviderPolicy:
    explicit_lock_required: bool = True
    per_run_circuit_only: bool = True


@dataclass(frozen=True)
class RetryPolicy:
    retry_same_endpoint_categories: tuple[FailureCategory, ...] = (
        FailureCategory.PROVIDER_RATE_LIMITED,
        FailureCategory.PROVIDER_TIMEOUT,
        FailureCategory.PROVIDER_EMPTY_RESPONSE,
    )
    maximum_same_endpoint_retries_per_node: int = 1


@dataclass(frozen=True)
class RecoveryPolicy:
    replace_categories: tuple[FailureCategory, ...] = (
        FailureCategory.UNSUPPORTED_PARAMETER,
        FailureCategory.CONTEXT_OVERFLOW,
        FailureCategory.PROVIDER_INVALID_RESPONSE,
        FailureCategory.OUTPUT_TRUNCATED,
        FailureCategory.PROVIDER_RATE_LIMITED,
        FailureCategory.PROVIDER_TIMEOUT,
        FailureCategory.PROVIDER_EMPTY_RESPONSE,
    )


@dataclass(frozen=True)
class TokenPolicy:
    dynamic_allowance: bool = True
    allowance_is_cost_assumption: bool = False


@dataclass(frozen=True)
class OutputPolicy:
    schema_version: str = "v5-node-result-1"
    field_aware_compaction_only: bool = True


@dataclass(frozen=True)
class AuditPolicy:
    preserve_failed_attempts: bool = True
    write_catalog_snapshot: bool = True


@dataclass(frozen=True)
class CatalogSnapshot:
    snapshot_id: str
    catalog_source: str
    endpoint_source: str
    models: tuple[Mapping[str, Any], ...]
    endpoint_payloads: Mapping[str, Mapping[str, Any]]

    @classmethod
    def build(
        cls,
        models: Sequence[Any],
        endpoint_payloads: Mapping[str, Mapping[str, Any]],
        *,
        catalog_source: str,
        endpoint_source: str,
    ) -> "CatalogSnapshot":
        rows = []
        for model in models:
            rows.append(
                {
                    "id": str(getattr(model, "id", "")),
                    "context_length": int(getattr(model, "context_length", 0) or 0),
                    "max_completion_tokens": int(
                        getattr(model, "max_completion_tokens", 0) or 0
                    ),
                    "prompt_price_per_million": getattr(
                        model, "prompt_price_per_million", None
                    ),
                    "completion_price_per_million": getattr(
                        model, "completion_price_per_million", None
                    ),
                    "supported_parameters": sorted(
                        str(value)
                        for value in (getattr(model, "supported_parameters", []) or [])
                    ),
                    "ranks": dict(getattr(model, "ranks", {}) or {}),
                }
            )
        rows.sort(key=lambda row: row["id"])
        canonical = json.dumps(
            {
                "catalog_source": catalog_source,
                "endpoint_source": endpoint_source,
                "models": rows,
                "endpoint_payloads": endpoint_payloads,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return cls(
            snapshot_id="catalog-" + sha256(canonical.encode("utf-8")).hexdigest()[:20],
            catalog_source=catalog_source,
            endpoint_source=endpoint_source,
            models=tuple(rows),
            endpoint_payloads=dict(endpoint_payloads),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "v5-catalog-snapshot-1",
            "catalog_snapshot_id": self.snapshot_id,
            "catalog_source": self.catalog_source,
            "endpoint_source": self.endpoint_source,
            "models": list(self.models),
            "endpoint_payloads": dict(self.endpoint_payloads),
            "cross_task_history_used": False,
        }


@dataclass
class RuntimeAttempt:
    attempt_index: int
    attempt_kind: str
    candidate_id: str
    model: str
    provider_endpoint: str
    request: Mapping[str, Any]
    status: str
    answer: str | None
    quality_score: float
    gate_reasons: list[str]
    latency_seconds: float
    usage: Mapping[str, Any]
    response_id: str | None
    response_model: str | None
    response_provider: str | None
    failure: Mapping[str, Any] | None = None


@dataclass
class RuntimeNodeResult:
    node_id: str
    assigned_work: tuple[str, ...]
    status: str
    selected_model: str
    resolved_model: str | None
    provider_endpoint: str
    answer: str | None
    quality_score: float
    attempts: list[RuntimeAttempt]
    actual_cost_usd: float
    contract: Mapping[str, Any]


class BudgetController:
    """One immutable-config-backed ledger for all calls in one run."""

    def __init__(self, config: RuntimeConfig, graph: ExecutionGraph) -> None:
        self.config = config
        self.calls_reserved = 0
        self.initial_calls_reserved = 0
        self.recovery_calls_reserved = 0
        self.retry_calls_reserved = 0
        self.replacement_calls_reserved = 0
        self.actual_cost_usd = 0.0
        self.pending_risk_costs: list[float] = []
        self.denials: list[dict[str, Any]] = []
        self.endpoint_failures: dict[str, int] = {}
        self.endpoint_failure_reasons: dict[str, list[str]] = {}
        self._lock = Lock()
        by_id = {node.node_id: node for node in graph.nodes}
        self.protected_final_cost = {
            node_id: by_id[node_id].estimated_cost * config.cost_risk_multiplier
            for node_id in graph.final_nodes
            if node_id in by_id
        }

    def reserve(self, kind: str, estimated_cost_usd: float, node_id: str) -> tuple[bool, str]:
        estimated = max(0.0, float(estimated_cost_usd))
        risk = estimated * float(self.config.cost_risk_multiplier)
        with self._lock:
            reason = ""
            if self.calls_reserved >= self.config.total_call_limit:
                reason = "total-call-limit-exhausted"
            elif kind == "initial" and self.initial_calls_reserved >= self.config.initial_call_limit:
                reason = "initial-call-cap-reserved-for-recovery"
            elif kind != "initial" and self.recovery_calls_reserved >= self.config.recovery_call_limit:
                reason = "shared-recovery-pool-exhausted"
            else:
                protected = sum(self.protected_final_cost.values()) - self.protected_final_cost.get(node_id, 0.0)
                projected = self.actual_cost_usd + sum(self.pending_risk_costs) + risk + protected
                if (
                    self.config.cost_anomaly_usd is not None
                    and projected > self.config.cost_anomaly_usd + 1e-12
                ):
                    reason = "risk-adjusted-cost-anomaly-limit-exhausted"
            if reason:
                self.denials.append(
                    {
                        "node_id": node_id,
                        "kind": kind,
                        "estimated_cost_usd": round(estimated, 8),
                        "reason": reason,
                    }
                )
                return False, reason
            self.calls_reserved += 1
            self.pending_risk_costs.append(risk)
            self.protected_final_cost.pop(node_id, None)
            if kind == "initial":
                self.initial_calls_reserved += 1
            else:
                self.recovery_calls_reserved += 1
                if kind == "retry":
                    self.retry_calls_reserved += 1
                elif kind == "replacement":
                    self.replacement_calls_reserved += 1
            return True, ""

    def reconcile(self, actual_cost_usd: float) -> bool:
        with self._lock:
            if self.pending_risk_costs:
                self.pending_risk_costs.pop(0)
            self.actual_cost_usd += max(0.0, float(actual_cost_usd))
            return bool(
                self.config.cost_anomaly_usd is not None
                and self.actual_cost_usd > self.config.cost_anomaly_usd + 1e-12
            )

    def endpoint_available(self, endpoint: str) -> bool:
        with self._lock:
            return self.endpoint_failures.get(endpoint, 0) < self.config.max_provider_failures

    def fail_endpoint(self, endpoint: str, category: FailureCategory) -> None:
        with self._lock:
            self.endpoint_failures[endpoint] = self.endpoint_failures.get(endpoint, 0) + 1
            self.endpoint_failure_reasons.setdefault(endpoint, []).append(category.value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "maximum_total_calls": self.config.total_call_limit,
                "maximum_initial_calls": self.config.initial_call_limit,
                "maximum_recovery_calls": self.config.recovery_call_limit,
                "calls_reserved": self.calls_reserved,
                "initial_calls_reserved": self.initial_calls_reserved,
                "recovery_calls_reserved": self.recovery_calls_reserved,
                "retries_reserved": self.retry_calls_reserved,
                "replacements_reserved": self.replacement_calls_reserved,
                "actual_cost_usd": round(self.actual_cost_usd, 8),
                "estimated_cost_reserved_usd": round(sum(self.pending_risk_costs), 8),
                "protected_final_cost_usd": round(sum(self.protected_final_cost.values()), 8),
                "denials": list(self.denials),
                "provider_circuit": {
                    "scope": "current-run-only",
                    "max_failures": self.config.max_provider_failures,
                    "failures": dict(self.endpoint_failures),
                    "reasons": {
                        key: list(value)
                        for key, value in self.endpoint_failure_reasons.items()
                    },
                },
            }


class PromptPolicy:
    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        structured = []
        for row in upstream:
            contract = row.get("contract") if isinstance(row, Mapping) else None
            if isinstance(contract, Mapping):
                structured.append(
                    {
                        "node_id": row.get("node_id"),
                        "answer": json.dumps(
                            contract,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            else:
                structured.append(dict(row))
        payload = cost_hardening.hardened_build_node_payload(
            node, original_task, structured
        )
        messages = payload.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
            messages[0] = {
                **dict(messages[0]),
                "content": dynamic_prompt.dynamic_system_prompt(node),
            }
            payload["messages"] = messages
        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise RuntimeError("provider lock missing from node request")
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            raise RuntimeError("provider.only must contain exactly one endpoint provider")
        if provider.get("allow_fallbacks") is not False:
            raise RuntimeError("provider fallbacks must be disabled")
        forbidden = sorted(legacy_executor.FORBIDDEN_FIELDS.intersection(payload))
        if forbidden:
            raise RuntimeError(f"forbidden request fields: {forbidden}")
        return payload


class QualityGatePolicy:
    def evaluate(
        self,
        node: SelectedNode,
        response: Mapping[str, Any],
        answer: str,
    ) -> tuple[bool, float, list[str]]:
        return output_contract.contract_aware_quality_gate(node, response, answer)


class ExecutionEngine:
    def __init__(
        self,
        config: RuntimeConfig,
        *,
        prompt_policy: PromptPolicy,
        retry_policy: RetryPolicy,
        recovery_policy: RecoveryPolicy,
        quality_policy: QualityGatePolicy,
        output_policy: OutputPolicy,
    ) -> None:
        self.config = config
        self.prompt_policy = prompt_policy
        self.retry_policy = retry_policy
        self.recovery_policy = recovery_policy
        self.quality_policy = quality_policy
        self.output_policy = output_policy

    @staticmethod
    def _provider(node: SelectedNode) -> str:
        provider = node.request_config.get("provider") if isinstance(node.request_config, Mapping) else None
        if isinstance(provider, Mapping):
            values = provider.get("only") or provider.get("order")
            if isinstance(values, list) and values:
                return str(values[0])
        return node.provider_endpoint.rsplit("@", 1)[-1]

    @staticmethod
    def _candidate(row: Mapping[str, Any], selected: SelectedNode) -> SelectedNode:
        return SelectedNode(
            node_id=selected.node_id,
            assigned_work=tuple(str(value) for value in row.get("assigned_work", selected.assigned_work)),
            professional_capabilities=dict(row.get("professional_capabilities", selected.professional_capabilities)),
            functions=tuple(str(value) for value in row.get("functions", selected.functions)),
            prompt_profile=dict(row.get("prompt_profile", selected.prompt_profile)),
            reasoning_profile=dict(row.get("reasoning_profile", selected.reasoning_profile)),
            parameter_profile=dict(row.get("parameter_profile", selected.parameter_profile)),
            model=str(row.get("model") or selected.model),
            provider_endpoint=str(row.get("provider_endpoint") or selected.provider_endpoint),
            output_contract=dict(row.get("output_contract", selected.output_contract)),
            estimated_quality=float(row.get("estimated_quality", selected.estimated_quality)),
            quality_uncertainty=float(row.get("quality_uncertainty", selected.quality_uncertainty)),
            estimated_cost=float(row.get("estimated_cost", selected.estimated_cost)),
            failure_probability=float(row.get("failure_probability", selected.failure_probability)),
            request_config=dict(row.get("request_config", selected.request_config)),
            independence_group=selected.independence_group,
        )

    @staticmethod
    def _actual_cost(response: Mapping[str, Any]) -> float:
        usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
        for key in ("cost", "total_cost"):
            try:
                if usage.get(key) is not None:
                    return max(0.0, float(usage[key]))
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _finish_reason(response: Mapping[str, Any]) -> str:
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
            return str(choices[0].get("finish_reason") or "")
        return ""

    @staticmethod
    def _failure_from_exception(
        exc: BaseException,
        node: SelectedNode,
    ) -> ExecutionFailure:
        status = getattr(exc, "http_status", None)
        retry_after = getattr(exc, "retry_after_seconds", None)
        retryable = bool(getattr(exc, "retryable", False))
        category_name = str(getattr(exc, "category", "") or "")
        if category_name in {"rate_limited", FailureCategory.PROVIDER_RATE_LIMITED.value} or status == 429:
            category = FailureCategory.PROVIDER_RATE_LIMITED
            retryable = True
        elif category_name in {"timeout", FailureCategory.PROVIDER_TIMEOUT.value}:
            category = FailureCategory.PROVIDER_TIMEOUT
            retryable = True
        elif category_name in {"unsupported_parameter", FailureCategory.UNSUPPORTED_PARAMETER.value}:
            category = FailureCategory.UNSUPPORTED_PARAMETER
        elif category_name in {"context_overflow", FailureCategory.CONTEXT_OVERFLOW.value}:
            category = FailureCategory.CONTEXT_OVERFLOW
        else:
            category = FailureCategory.PROVIDER_INVALID_RESPONSE
        return ExecutionFailure(
            category=category,
            retryable=retryable,
            http_status=int(status) if status is not None else None,
            retry_after_seconds=float(retry_after) if retry_after is not None else None,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request_sent=bool(getattr(exc, "request_sent", True)),
            response_received=bool(getattr(exc, "response_received", status is not None)),
            message=str(exc),
        )

    def _contract(self, node: SelectedNode, answer: str | None) -> dict[str, Any]:
        parsed: Any = None
        if answer:
            try:
                parsed = json.loads(answer)
            except json.JSONDecodeError:
                parsed = None
        standard = {
            "conclusions": [],
            "calculations": [],
            "assumptions": [],
            "unknowns": [],
            "risks": [],
            "decision_thresholds": [],
            "counterarguments": [],
            "unresolved_items": [],
        }
        if isinstance(parsed, Mapping):
            for key in standard:
                value = parsed.get(key)
                if isinstance(value, list):
                    standard[key] = [str(item) for item in value]
                elif value not in {None, ""}:
                    standard[key] = [str(value)]
            standard["raw_fields"] = dict(parsed)
        elif answer:
            standard["conclusions"] = [answer]
        required = [str(value) for value in node.output_contract.get("required_fields", [])]
        complete = True
        if required:
            complete = isinstance(parsed, Mapping) and all(key in parsed for key in required)
        canonical = json.dumps(standard, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return {
            "schema_version": self.output_policy.schema_version,
            "node_id": node.node_id,
            "required_fields_complete": bool(complete),
            "content_sha256": sha256(canonical.encode("utf-8")).hexdigest(),
            "compression_used": False,
            **standard,
        }

    def _attempt(
        self,
        node: SelectedNode,
        selected_node_id: str,
        kind: str,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
        budget: BudgetController,
        attempt_index: int,
    ) -> RuntimeAttempt | None:
        if not budget.endpoint_available(node.provider_endpoint):
            return None
        allowed, _ = budget.reserve(kind, node.estimated_cost, selected_node_id)
        if not allowed:
            return None
        payload: Mapping[str, Any] = {}
        response: Mapping[str, Any] = {}
        latency = 0.0
        try:
            payload = self.prompt_policy.build_payload(node, original_task, upstream)
            response, latency = call_fn(run, payload)
            answer = cost_hardening.robust_extract_answer(response)
            usage = dict(response.get("usage") or {}) if isinstance(response.get("usage"), Mapping) else {}
            actual_cost = self._actual_cost(response)
            budget_exceeded = budget.reconcile(actual_cost)
            if not answer:
                failure = ExecutionFailure(
                    category=FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    retryable=True,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message="provider returned no usable answer",
                )
                return RuntimeAttempt(
                    attempt_index, kind, node.node_id, node.model, node.provider_endpoint,
                    payload, "call_failed", None, 0.0, ["empty-output"],
                    round(float(latency), 6), usage,
                    str(response.get("id") or "") or None,
                    str(response.get("model") or node.model) or None,
                    str(response.get("provider") or "") or None,
                    failure.to_dict(),
                )
            passed, quality, reasons = self.quality_policy.evaluate(node, response, answer)
            finish = self._finish_reason(response).casefold()
            if finish in {"length", "max_tokens"}:
                failure = ExecutionFailure(
                    category=FailureCategory.OUTPUT_TRUNCATED,
                    retryable=False,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message="provider stopped because output allowance was exhausted",
                )
            elif not passed:
                failure = ExecutionFailure(
                    category=FailureCategory.QUALITY_GATE_FAILED,
                    retryable=False,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message=";".join(reasons),
                )
            else:
                failure = None
            status = "passed" if passed and not budget_exceeded else "quality_gate_failed"
            if budget_exceeded:
                reasons = list(reasons) + ["actual-budget-exceeded"]
                failure = ExecutionFailure(
                    category=FailureCategory.BUDGET_INSUFFICIENT,
                    retryable=False,
                    model=node.model,
                    provider_endpoint=node.provider_endpoint,
                    request_sent=True,
                    response_received=True,
                    usage_received=bool(usage),
                    actual_cost_usd=actual_cost,
                    message="actual cost exceeded the approved anomaly guard",
                )
            return RuntimeAttempt(
                attempt_index, kind, node.node_id, node.model, node.provider_endpoint,
                payload, status, answer, quality, list(reasons),
                round(float(latency), 6), usage,
                str(response.get("id") or "") or None,
                str(response.get("model") or node.model) or None,
                str(response.get("provider") or "") or None,
                failure.to_dict() if failure else None,
            )
        except Exception as exc:  # noqa: BLE001 - converted into structured evidence
            budget.reconcile(0.0)
            failure = self._failure_from_exception(exc, node)
            return RuntimeAttempt(
                attempt_index, kind, node.node_id, node.model, node.provider_endpoint,
                payload, "call_failed", None, 0.0, [failure.category.value],
                round(float(latency), 6), {}, None, None, None,
                failure.to_dict(),
            )

    @staticmethod
    def _category(attempt: RuntimeAttempt | None) -> FailureCategory:
        if attempt is None:
            return FailureCategory.CALL_LIMIT_EXHAUSTED
        failure = attempt.failure
        if isinstance(failure, Mapping):
            try:
                return FailureCategory(str(failure.get("category")))
            except ValueError:
                pass
        return FailureCategory.QUALITY_GATE_FAILED

    @staticmethod
    def _degraded_usable(node: SelectedNode, attempt: RuntimeAttempt | None) -> bool:
        if attempt is None or not attempt.answer:
            return False
        if node.output_contract.get("machine_readable_required"):
            return False
        text = str(attempt.answer).strip()
        minimum = 260 if "synthesis" in node.functions else 120
        return len(text) >= minimum and not any(
            value in text.casefold()
            for value in ("i cannot access", "无法访问互联网", "作为ai无法", "没有提供任何答案")
        )

    def _node_result(
        self,
        selected: SelectedNode,
        resolved: SelectedNode,
        attempts: list[RuntimeAttempt],
        attempt: RuntimeAttempt,
        status: str,
    ) -> RuntimeNodeResult:
        return RuntimeNodeResult(
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
                sum(
                    self._actual_cost({"usage": row.usage})
                    for row in attempts
                ),
                8,
            ),
            contract=self._contract(selected, attempt.answer),
        )

    def execute_node(
        self,
        selected: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]],
        recovery_rows: Sequence[Mapping[str, Any]],
        budget: BudgetController,
    ) -> RuntimeNodeResult:
        attempts: list[RuntimeAttempt] = []
        best: tuple[RuntimeAttempt, SelectedNode] | None = None

        def call(node: SelectedNode, kind: str) -> RuntimeAttempt | None:
            attempt = self._attempt(
                node, selected.node_id, kind, original_task, upstream,
                run, call_fn, budget, len(attempts) + 1,
            )
            if attempt is not None:
                attempts.append(attempt)
                category = self._category(attempt)
                if category in {
                    FailureCategory.PROVIDER_RATE_LIMITED,
                    FailureCategory.PROVIDER_TIMEOUT,
                    FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    FailureCategory.UNSUPPORTED_PARAMETER,
                    FailureCategory.CONTEXT_OVERFLOW,
                }:
                    budget.fail_endpoint(node.provider_endpoint, category)
            return attempt

        initial = call(selected, "initial")
        if initial is not None and initial.status == "passed":
            return self._node_result(selected, selected, attempts, initial, "success")
        if self._degraded_usable(selected, initial):
            best = (initial, selected)

        category = self._category(initial)
        if category in self.retry_policy.retry_same_endpoint_categories:
            retry_after = 0.0
            if initial and isinstance(initial.failure, Mapping):
                try:
                    retry_after = max(0.0, min(60.0, float(initial.failure.get("retry_after_seconds") or 0.0)))
                except (TypeError, ValueError):
                    retry_after = 0.0
            if retry_after:
                time.sleep(retry_after)
            retried = call(selected, "retry")
            if retried is not None and retried.status == "passed":
                return self._node_result(selected, selected, attempts, retried, "success_retried")
            if self._degraded_usable(selected, retried) and (
                best is None or retried.quality_score > best[0].quality_score
            ):
                best = (retried, selected)
            category = self._category(retried)

        alternatives = [self._candidate(row, selected) for row in recovery_rows]
        alternatives.sort(
            key=lambda node: (
                self._provider(node) == self._provider(selected),
                node.failure_probability,
                node.estimated_cost,
                -node.estimated_quality,
            )
        )
        if category in self.recovery_policy.replace_categories:
            for replacement in alternatives:
                attempted = call(replacement, "replacement")
                if attempted is None:
                    continue
                if attempted.status == "passed":
                    return self._node_result(
                        selected, replacement, attempts, attempted, "success_recovered"
                    )
                if self._degraded_usable(replacement, attempted) and (
                    best is None or attempted.quality_score > best[0].quality_score
                ):
                    best = (attempted, replacement)

        if best is not None:
            return self._node_result(selected, best[1], attempts, best[0], "success_degraded")
        active = alternatives[-1] if alternatives else selected
        return RuntimeNodeResult(
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
                sum(self._actual_cost({"usage": row.usage}) for row in attempts), 8
            ),
            contract=self._contract(selected, None),
        )

    def _default_call(
        self,
        run: Any,
        payload: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], float]:
        api_key = getattr(run, "api_key", None)
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        started = time.monotonic()
        response = request_json(
            CHAT_URL,
            api_key,
            int(getattr(run, "model_timeout_seconds", 240)),
            0,
            dict(payload),
        )
        return response, time.monotonic() - started

    @staticmethod
    def _content_work_ids(graph: ExecutionGraph) -> set[str]:
        synthesis = {
            work_id
            for node in graph.nodes
            if "synthesis" in node.functions
            for work_id in node.assigned_work
        }
        return set(graph.required_work) - synthesis or set(graph.required_work)

    @staticmethod
    def _best_outputs_by_work(
        graph: ExecutionGraph,
        outputs: Mapping[str, RuntimeNodeResult],
    ) -> dict[str, RuntimeNodeResult]:
        best: dict[str, RuntimeNodeResult] = {}
        content = ExecutionEngine._content_work_ids(graph)
        for result in outputs.values():
            if not result.status.startswith("success") or not result.answer:
                continue
            for work_id in result.assigned_work:
                if work_id not in content:
                    continue
                previous = best.get(work_id)
                if previous is None or result.quality_score > previous.quality_score:
                    best[work_id] = result
        return best

    @staticmethod
    def _degraded_synthesis(
        best_by_work: Mapping[str, RuntimeNodeResult],
        missing_work: Sequence[str],
    ) -> str:
        sections = [
            "# V5降级合成结果",
            "",
            "本结果由已通过可用性门的节点确定性合成；未调用额外模型。",
        ]
        if missing_work:
            sections.extend(["", "## 未覆盖工作", "、".join(sorted(missing_work))])
        for index, (work_id, result) in enumerate(sorted(best_by_work.items()), 1):
            sections.extend(["", f"## {index}. {work_id}", result.answer or ""])
        return "\n".join(sections).strip()

    def _preflight(self, graph: ExecutionGraph) -> dict[str, Any]:
        risk_cost = graph.estimated_total_cost * self.config.cost_risk_multiplier
        providers: dict[str, int] = {}
        for node in graph.nodes:
            providers[self._provider(node)] = providers.get(self._provider(node), 0) + 1
        blockers = []
        if (
            self.config.cost_anomaly_usd is not None
            and risk_cost > self.config.cost_anomaly_usd + 1e-12
        ):
            blockers.append("preflight-risk-adjusted-cost-above-anomaly-limit")
        return {
            "status": "rejected" if blockers else "pass",
            "estimated_initial_cost_usd": graph.estimated_total_cost,
            "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
            "cost_anomaly_usd": self.config.cost_anomaly_usd,
            "provider_counts": providers,
            "blockers": blockers,
            "policy": "native-runtime-preflight-before-first-call",
        }

    def _write_artifacts(
        self,
        root: Path,
        result: Mapping[str, Any],
        outputs: Mapping[str, RuntimeNodeResult],
    ) -> None:
        root.mkdir(parents=True, exist_ok=True)
        node_rows = [asdict(outputs[node_id]) for node_id in sorted(outputs)]
        self._write_json(root / "v5-node-results.json", node_rows)
        self._write_json(
            root / "v5-execution-summary.json",
            {key: value for key, value in result.items() if key != "node_results"},
        )
        attempts = [attempt for row in outputs.values() for attempt in row.attempts]
        requests = [attempt.request for attempt in attempts]
        self._write_json(
            root / "v5-request-audit.json",
            {
                "status": "PASS" if all(
                    not legacy_executor.FORBIDDEN_FIELDS.intersection(request)
                    for request in requests
                ) else "FAIL",
                "request_count": len(requests),
                "requests": requests,
                "external_tools_allowed": False,
                "bounded_output_allowance_sent": any(
                    "max_tokens" in request or "max_completion_tokens" in request
                    for request in requests
                ),
                "dynamic_output_allowance_sent": any(
                    "max_tokens" in request or "max_completion_tokens" in request
                    for request in requests
                ),
                "artificial_token_ceiling_sent": False,
                "global_limits": result["execution_budget"],
                "quality_integrity_status": result.get("quality_integrity", {}).get("status"),
                "degraded_synthesis_is_deterministic": bool(
                    result.get("degradation", {}).get("used")
                ),
            },
        )
        (root / "v5-final-report.md").write_text(
            str(result.get("final_answer") or "# V5 execution failed\n"),
            encoding="utf-8",
        )

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def execute_graph(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        run: Any,
        original_task: str,
        *,
        call_fn: Callable[[Any, Mapping[str, Any]], tuple[Mapping[str, Any], float]] | None = None,
        output_dir: str | Path | None = None,
        limits: GraphLimits | None = None,
    ) -> dict[str, Any]:
        graph = graph if isinstance(graph, ExecutionGraph) else ExecutionGraph.from_mapping(graph)
        limits = limits or GraphLimits()
        issues = validate_execution_graph(graph, limits)
        structural = [issue for issue in issues if issue.code != "budget_limit"]
        if structural:
            raise RuntimeError(
                "Invalid execution graph: "
                + "; ".join(f"{issue.code}:{issue.message}" for issue in structural)
            )
        if len(graph.nodes) > self.config.initial_call_limit:
            raise RuntimeError("planned nodes exceed RuntimeConfig.initial_call_limit")

        preflight = self._preflight(graph)
        root = Path(output_dir) if output_dir is not None else None
        if preflight["blockers"]:
            if root is not None:
                self._write_json(root / "v5-node-results.json", [])
                self._write_json(
                    root / "v5-execution-summary.json",
                    {
                        "version": 5,
                        "status": "failed",
                        "completion_mode": "none",
                        "quality_status": "failed",
                        "final_answer": None,
                        "actual_cost_usd": 0.0,
                        "execution_budget": {
                            "maximum_total_calls": self.config.total_call_limit,
                            "maximum_initial_calls": self.config.initial_call_limit,
                            "maximum_recovery_calls": self.config.recovery_call_limit,
                            "calls_reserved": 0,
                        },
                        "cost_preflight": preflight,
                        "stop_reason": "native-runtime-preflight-rejected",
                    },
                )
                self._write_json(root / "v5-request-audit.json", {"status": "PASS", "request_count": 0, "requests": []})
            raise RuntimeError("V5 graph rejected before model calls")

        call = call_fn or self._default_call
        node_by_id = {node.node_id: node for node in graph.nodes}
        incoming: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
        for edge in graph.edges:
            incoming[edge.target].append(edge.source)
        budget = BudgetController(self.config, graph)
        outputs: dict[str, RuntimeNodeResult] = {}
        recovery = graph.metadata.get("recovery_pool", {}) if isinstance(graph.metadata, Mapping) else {}
        stage_records: list[dict[str, Any]] = []

        for stage_index, stage in enumerate(graph.execution_stages):
            configured = max(1, int(getattr(run, "parallel_workers", len(stage) or 1)))
            workers = 1 if self.config.cost_anomaly_usd is not None else min(configured, len(stage))
            futures = {}
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                for node_id in stage:
                    upstream = [
                        {
                            "node_id": source,
                            "answer": outputs[source].answer,
                            "quality_score": outputs[source].quality_score,
                            "contract": outputs[source].contract,
                        }
                        for source in incoming[node_id]
                        if source in outputs and outputs[source].answer
                    ]
                    futures[
                        pool.submit(
                            self.execute_node,
                            node_by_id[node_id],
                            original_task,
                            upstream,
                            run,
                            call,
                            list(recovery.get(node_id, [])) if isinstance(recovery, Mapping) else [],
                            budget,
                        )
                    ] = node_id
                stage_results = [future.result() for future in as_completed(futures)]
            stage_results.sort(key=lambda row: row.node_id)
            for row in stage_results:
                outputs[row.node_id] = row
            failed = [row.node_id for row in stage_results if not row.status.startswith("success")]
            stage_records.append(
                {
                    "stage_index": stage_index,
                    "node_ids": list(stage),
                    "failed_node_ids": failed,
                    "status": "degraded" if failed else "success",
                    "continued_after_failure": bool(failed),
                }
            )
            if budget.calls_reserved >= self.config.total_call_limit:
                break

        successful_finals = [
            outputs[node_id]
            for node_id in graph.final_nodes
            if node_id in outputs
            and outputs[node_id].status.startswith("success")
            and outputs[node_id].answer
        ]
        preferred_final = "\n\n".join(row.answer or "" for row in successful_finals).strip()
        optional_work = {
            str(value)
            for value in graph.metadata.get("optional_work_ids", [])
        } if isinstance(graph.metadata, Mapping) else set()
        non_degradable_work = {
            str(value)
            for value in graph.metadata.get("non_degradable_work_ids", [])
        } if isinstance(graph.metadata, Mapping) else set()
        content_work = self._content_work_ids(graph) - optional_work
        best_by_work = {
            work_id: result
            for work_id, result in self._best_outputs_by_work(graph, outputs).items()
            if work_id in content_work
        }
        covered = set(best_by_work)
        missing = sorted(content_work - covered)
        coverage = len(covered) / max(1, len(content_work))
        successful_content_nodes = len({
            result.node_id for result in best_by_work.values()
        })
        complete_nodes = (
            len(outputs) == len(graph.nodes)
            and all(row.status.startswith("success") for row in outputs.values())
        )
        minimum_coverage = max(0.0, min(1.0, float(limits.min_required_work_coverage)))
        degradation_used = False
        final_answer = preferred_final
        if not final_answer and coverage >= minimum_coverage:
            final_answer = self._degraded_synthesis(best_by_work, missing)
            degradation_used = True
        elif preferred_final and (missing or not complete_nodes):
            degradation_used = True

        delivery_blockers: list[str] = []
        missing_non_degradable = sorted(non_degradable_work.intersection(missing))
        if missing_non_degradable:
            delivery_blockers.append("missing-non-degradable-work")
        if coverage + 1e-12 < minimum_coverage:
            delivery_blockers.append("insufficient-required-work-coverage")
        if successful_content_nodes < int(limits.min_successful_content_nodes):
            delivery_blockers.append("insufficient-successful-content-nodes")
        if degradation_used and not limits.allow_degraded_success:
            delivery_blockers.append("degraded-success-disabled")

        if final_answer and not degradation_used and complete_nodes and not missing:
            status = "success"
            completion_mode = "full"
            quality_status = "full_success"
            stop_reason = "all-quality-gates-passed"
        elif final_answer and not delivery_blockers:
            status = "success"
            completion_mode = "degraded"
            quality_status = "degraded_success"
            stop_reason = "partial-success-deterministic-synthesis"
        else:
            status = "failed"
            completion_mode = "none"
            quality_status = "failed"
            stop_reason = delivery_blockers[0] if delivery_blockers else "insufficient-work-coverage-after-recovery"

        result = {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "executor": "v5-native-execution-engine",
            "status": status,
            "completion_mode": completion_mode,
            "quality_status": quality_status,
            "execution_stages": stage_records,
            "node_results": [asdict(outputs[node_id]) for node_id in sorted(outputs)],
            "final_node_ids": list(graph.final_nodes),
            "final_answer": final_answer or None,
            "actual_cost_usd": round(sum(row.actual_cost_usd for row in outputs.values()), 8),
            "recovery_used": any(
                attempt.attempt_kind in {"retry", "replacement"}
                for row in outputs.values()
                for attempt in row.attempts
            ),
            "execution_budget": budget.snapshot(),
            "cost_preflight": preflight,
            "work_coverage": {
                "required_content_work_ids": sorted(content_work),
                "covered_work_ids": sorted(covered),
                "missing_work_ids": missing,
                "coverage_ratio": round(coverage, 6),
                "minimum_degraded_coverage": minimum_coverage,
                "successful_content_nodes": successful_content_nodes,
            },
            "delivery_policy": {
                "optional_work_ids": sorted(optional_work),
                "non_degradable_work_ids": sorted(non_degradable_work),
                "missing_non_degradable_work_ids": missing_non_degradable,
                "minimum_required_work_coverage": minimum_coverage,
                "minimum_successful_content_nodes": int(limits.min_successful_content_nodes),
                "allow_degraded_success": bool(limits.allow_degraded_success),
                "blockers": delivery_blockers,
            },
            "degradation": {
                "used": degradation_used,
                "mode": "deterministic-successful-node-synthesis" if degradation_used else None,
                "extra_model_calls": 0,
            },
            "stop_reason": stop_reason,
        }
        result = quality_integrity.enforce_result_integrity(result)
        if root is not None:
            self._write_artifacts(root, result, outputs)
        if status == "failed":
            if stop_reason == "insufficient-successful-content-nodes":
                raise RuntimeError("insufficient-successful-content-nodes")
            if stop_reason in {"missing-non-degradable-work", "degraded-success-disabled"}:
                raise RuntimeError("V5 execution failed production delivery policy")
            raise RuntimeError("V5 execution could not reach the minimum audited work-coverage gate")
        return result


@dataclass
class ProductionRuntime:
    config: RuntimeConfig
    ticket_policy: TicketPolicy = field(default_factory=TicketPolicy)
    catalog_policy: CatalogPolicy = field(default_factory=CatalogPolicy)
    budget_policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    cost_policy: CostPolicy = field(default_factory=CostPolicy)
    provider_policy: ProviderPolicy = field(default_factory=ProviderPolicy)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    recovery_policy: RecoveryPolicy = field(default_factory=RecoveryPolicy)
    prompt_policy: PromptPolicy = field(default_factory=PromptPolicy)
    token_policy: TokenPolicy = field(default_factory=TokenPolicy)
    output_policy: OutputPolicy = field(default_factory=OutputPolicy)
    quality_policy: QualityGatePolicy = field(default_factory=QualityGatePolicy)
    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)
    planner_policy: Any | None = None

    def __post_init__(self) -> None:
        if self.planner_policy is None:
            self.planner_policy = PlannerPolicy(self.config)
        self.execution_engine = ExecutionEngine(
            self.config,
            prompt_policy=self.prompt_policy,
            retry_policy=self.retry_policy,
            recovery_policy=self.recovery_policy,
            quality_policy=self.quality_policy,
            output_policy=self.output_policy,
        )

    def build_catalog_snapshot(
        self,
        models: Sequence[Any],
        endpoint_payloads: Mapping[str, Mapping[str, Any]],
        *,
        catalog_source: str,
        endpoint_source: str,
    ) -> CatalogSnapshot:
        return CatalogSnapshot.build(
            models,
            endpoint_payloads,
            catalog_source=catalog_source,
            endpoint_source=endpoint_source,
        )

    def build_node_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        return self.prompt_policy.build_payload(node, original_task, upstream)

    def execute_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return self.execution_engine.execute_graph(*args, **kwargs)

    def describe(self) -> dict[str, Any]:
        return {
            "runtime_version": RUNTIME_VERSION,
            "configuration": self.config.to_dict(),
            "policies": {
                "ticket": asdict(self.ticket_policy),
                "catalog": asdict(self.catalog_policy),
                "budget": asdict(self.budget_policy),
                "cost": asdict(self.cost_policy),
                "provider": asdict(self.provider_policy),
                "retry": {
                    "retry_same_endpoint_categories": [
                        value.value for value in self.retry_policy.retry_same_endpoint_categories
                    ],
                    "maximum_same_endpoint_retries_per_node": self.retry_policy.maximum_same_endpoint_retries_per_node,
                },
                "recovery": {
                    "replace_categories": [
                        value.value for value in self.recovery_policy.replace_categories
                    ]
                },
                "token": asdict(self.token_policy),
                "output": asdict(self.output_policy),
                "audit": asdict(self.audit_policy),
                "planner": {
                    "implementation": type(self.planner_policy).__name__,
                    "composition": "explicit-direct-call",
                },
            },
            "global_monkey_patching": False,
            "cross_task_history_used": False,
        }
