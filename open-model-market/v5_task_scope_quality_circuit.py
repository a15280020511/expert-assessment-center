"""Business-task scoping and repeated deterministic-quality recovery circuit.

Real production run gov-311-expert / Expert #407 showed two independent issues:

1. The ticket transport intentionally appends execution/control requirements to the
   user task for auditability. Semantic delivery gates then treated those control
   clauses as business-answer obligations (for example a model-assignment principle
   such as ``性价比优先``), contaminating obligation compilation and closed-world
   quantities.
2. Once several different models failed the same deterministic task-obligation
   reason, standby promotion kept substituting more models even though model
   replacement could not repair the contract/compiler mismatch.

This layer keeps the complete control task in the Artifact, but projects only the
business question, business-facing delivery clauses and supplied evidence into
model execution and semantic/evidence validation. It also suppresses further
standby promotion after the same deterministic task-obligation reason is observed
across multiple distinct models in the current run. No state survives the current
task and no cost/token threshold becomes an execution gate.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping, Sequence

from v5_cost_effectiveness_runtime import CostEffectiveContinuousExecutionEngine
from v5_runtime import ProductionRuntime

_EXECUTION_REQUIREMENTS_MARKER = "\n\n执行要求："
_BUSINESS_REQUIREMENTS_MARKER = "\n\n业务交付要求："
_EVIDENCE_MARKER = "\n\n已提供证据/上下文："

# These tokens are intentionally limited to unambiguous control-plane/execution
# mechanics. Generic decision principles such as 性价比优先 / 动态适配 are NOT
# control tokens by themselves because a user may legitimately make them part of
# the business objective. Domain words such as 法律/商业/数学 are also absent.
_CONTROL_CLAUSE_TOKENS = (
    "专家不得调用外部工具",
    "外部工具、浏览器",
    "工具、浏览器、搜索",
    "专家数量",
    "role dag",
    "reasoning effort",
    "模型组合",
    "恢复与standby",
    "恢复专家",
    "standby",
    "提示词必须",
    "固定宪法",
    "模型分配必须",
    "费用/token",
    "token是软优化",
    "最终完整payload",
    "payload组装",
    "max_tokens",
    "effective timeout",
    "current-run",
    "跨任务历史",
    "固定3人",
    "固定4人",
    "裁判模板",
    "openrouter",
    "provider",
    "or-tools",
    "artifact",
    "full/full_success",
    "quality-gates-passed",
    "all-quality-gates-passed",
)

_DETERMINISTIC_OBLIGATION_PREFIXES = (
    "missing-task-obligation:",
    "empty-task-obligation:",
)


def _sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _split_requirement_clauses(line: str) -> list[str]:
    rendered = re.sub(r"^\s*[-*+]\s*", "", str(line or "")).strip()
    if not rendered:
        return []
    return [value.strip() for value in re.split(r"[；;]+", rendered) if value.strip()]


def _is_control_clause(clause: str) -> bool:
    folded = str(clause or "").casefold()
    return any(token.casefold() in folded for token in _CONTROL_CLAUSE_TOKENS)


def project_business_task(task: str) -> tuple[str, dict[str, Any]]:
    """Project a transport/control task into the business-answer surface.

    The historical ticket writer uses ``执行要求：`` as the deterministic boundary
    between the user question and appended execution requirements. We retain any
    business-facing clauses from that section (for example no-fabrication or final
    report content requirements) while removing control-plane mechanics. Evidence
    remains authoritative and is reattached unchanged.
    """
    full = str(task or "").strip()
    if not full:
        return "", {
            "schema_version": "v5-business-task-scope-1",
            "projection_applied": False,
            "reason": "empty-task",
            "control_task_sha256": _sha(""),
            "business_task_sha256": _sha(""),
            "business_clause_count": 0,
            "control_clause_count": 0,
            "cross_task_history_used": False,
        }

    if _EXECUTION_REQUIREMENTS_MARKER not in full:
        return full, {
            "schema_version": "v5-business-task-scope-1",
            "projection_applied": False,
            "reason": "no-execution-requirements-marker",
            "control_task_sha256": _sha(full),
            "business_task_sha256": _sha(full),
            "business_clause_count": 0,
            "control_clause_count": 0,
            "cross_task_history_used": False,
        }

    question, requirements_and_evidence = full.split(
        _EXECUTION_REQUIREMENTS_MARKER, 1
    )
    if _EVIDENCE_MARKER in requirements_and_evidence:
        requirements_text, evidence_text = requirements_and_evidence.split(
            _EVIDENCE_MARKER, 1
        )
        evidence_suffix = _EVIDENCE_MARKER + evidence_text
    else:
        requirements_text = requirements_and_evidence
        evidence_suffix = ""

    business_clauses: list[str] = []
    control_clauses: list[str] = []
    for line in requirements_text.splitlines():
        for clause in _split_requirement_clauses(line):
            if _is_control_clause(clause):
                control_clauses.append(clause)
            else:
                business_clauses.append(clause)

    parts = [question.strip()]
    if business_clauses:
        parts.append(
            _BUSINESS_REQUIREMENTS_MARKER.strip()
            + "\n"
            + "\n".join(f"- {value}" for value in business_clauses)
        )
    projected = "\n\n".join(value for value in parts if value).strip()
    if evidence_suffix:
        projected += evidence_suffix

    audit = {
        "schema_version": "v5-business-task-scope-1",
        "projection_applied": projected != full,
        "reason": "typed-business-versus-execution-requirement-projection",
        "control_task_sha256": _sha(full),
        "business_task_sha256": _sha(projected),
        "business_clause_count": len(business_clauses),
        "control_clause_count": len(control_clauses),
        "business_clauses": business_clauses,
        "dropped_control_clauses": control_clauses,
        "evidence_preserved": bool(evidence_suffix),
        "control_task_preserved_in_ticket_artifact": True,
        "model_and_semantic_gate_receive_business_projection": True,
        "domain_keyword_routing_used": False,
        "cross_task_history_used": False,
    }
    return projected, audit


def _deterministic_gate_reasons(attempt: Any) -> list[str]:
    reasons = getattr(attempt, "gate_reasons", [])
    if not isinstance(reasons, list):
        return []
    values: list[str] = []
    for raw in reasons:
        value = str(raw or "").strip()
        if value.startswith(_DETERMINISTIC_OBLIGATION_PREFIXES) and value not in values:
            values.append(value)
    return values


def repeated_deterministic_quality_signal(
    attempts: Sequence[Any],
) -> dict[str, Any] | None:
    """Return a current-run repeated contract signal, if one is established.

    A replacement-worthy model failure should not be inferred from one or two
    samples. The evidence threshold scales with the number of distinct models
    observed so far and is never below three. The circuit considers only exact
    deterministic task-obligation reasons, not arithmetic/content-quality errors.
    """
    reason_models: dict[str, set[str]] = {}
    quality_models: set[str] = set()
    for attempt in attempts:
        reasons = _deterministic_gate_reasons(attempt)
        if not reasons:
            continue
        model = str(getattr(attempt, "model", "") or "").strip() or "unknown"
        quality_models.add(model)
        for reason in reasons:
            reason_models.setdefault(reason, set()).add(model)

    if not quality_models:
        return None
    distinct_quality_models = len(quality_models)
    threshold = max(3, math.ceil(math.sqrt(distinct_quality_models)) + 1)
    ranked = sorted(
        reason_models.items(),
        key=lambda item: (-len(item[1]), item[0]),
    )
    if not ranked:
        return None
    reason, models = ranked[0]
    if len(models) < threshold:
        return None
    return {
        "schema_version": "v5-repeated-deterministic-quality-signal-1",
        "reason": reason,
        "distinct_model_count": len(models),
        "distinct_models": sorted(models),
        "dynamic_evidence_threshold": threshold,
        "model_substitution_expected_marginal_return": "low",
        "failure_scope": "task-contract-or-prompt-systemic-suspected",
        "cross_task_history_used": False,
    }


class TaskScopedCostEffectiveExecutionEngine(
    CostEffectiveContinuousExecutionEngine
):
    """Execute only the business projection and stop wasteful standby fan-out."""

    def _ensure_quality_circuit_state(self) -> None:
        self._ensure_feedback_state()
        if not hasattr(self, "_systemic_quality_circuit_open"):
            self._systemic_quality_circuit_open = False
            self._systemic_quality_signal: dict[str, Any] | None = None
            self._systemic_quality_circuit_events: list[dict[str, Any]] = []
            self._systemic_quality_attempts: list[Any] = []
            self._task_scope_audit: dict[str, Any] = {}

    def _initialize_feedback(self, graph: Any) -> None:
        super()._initialize_feedback(graph)
        self._ensure_quality_circuit_state()
        with self._feedback_lock:
            self._systemic_quality_circuit_open = False
            self._systemic_quality_signal = None
            self._systemic_quality_circuit_events = []
            self._systemic_quality_attempts = []

    def _open_quality_circuit(self, signal: Mapping[str, Any]) -> None:
        if self._systemic_quality_circuit_open:
            return
        self._systemic_quality_circuit_open = True
        self._systemic_quality_signal = dict(signal)
        self._systemic_quality_circuit_events.append(
            {
                "event_type": "systemic-quality-contract-circuit-opened",
                **dict(signal),
                "standby_promotion_suppressed": True,
                "initial_recovery_evidence_preserved": True,
                "cost_or_token_threshold_used": False,
            }
        )

    def _record_feedback(self, attempt: Any | None) -> None:
        super()._record_feedback(attempt)
        if attempt is None:
            return
        self._ensure_quality_circuit_state()
        with self._feedback_lock:
            self._systemic_quality_attempts.append(attempt)
            signal = repeated_deterministic_quality_signal(
                self._systemic_quality_attempts
            )
            if signal is not None:
                self._open_quality_circuit(signal)

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_quality_circuit_state()
        with self._feedback_lock:
            if self._systemic_quality_circuit_open:
                return None
        return super()._claim_next_standby()

    def _dynamic_promotion_depth(self, node_attempts: Sequence[Any]) -> int:
        self._ensure_quality_circuit_state()
        signal = repeated_deterministic_quality_signal(node_attempts)
        if signal is not None:
            with self._feedback_lock:
                self._open_quality_circuit(signal)
            return 0
        with self._feedback_lock:
            if self._systemic_quality_circuit_open:
                return 0
        return int(super()._dynamic_promotion_depth(node_attempts))

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        self._ensure_quality_circuit_state()
        with self._feedback_lock:
            value["systemic_quality_contract_circuit"] = {
                "enabled": True,
                "open": bool(self._systemic_quality_circuit_open),
                "signal": (
                    dict(self._systemic_quality_signal)
                    if isinstance(self._systemic_quality_signal, Mapping)
                    else None
                ),
                "events": [
                    dict(row) for row in self._systemic_quality_circuit_events
                ],
                "standby_promotion_suppressed_when_open": True,
                "cost_or_token_threshold_used": False,
                "cross_task_history_used": False,
            }
        return value

    def execute_graph(
        self,
        graph: Any,
        run: Any,
        original_task: str,
        *,
        call_fn: Any = None,
        output_dir: str | Any | None = None,
        limits: Any = None,
    ) -> dict[str, Any]:
        business_task, audit = project_business_task(original_task)
        self._ensure_quality_circuit_state()
        self._task_scope_audit = dict(audit)
        result = super().execute_graph(
            graph,
            run,
            business_task,
            call_fn=call_fn,
            output_dir=output_dir,
            limits=limits,
        )
        result = dict(result)
        result["business_task_scope"] = dict(audit)
        return result


def install_task_scope_quality_circuit(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    """Install the final execution layer before any model call occurs."""
    runtime.execution_engine = TaskScopedCostEffectiveExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "TaskScopedCostEffectiveExecutionEngine",
    "install_task_scope_quality_circuit",
    "project_business_task",
    "repeated_deterministic_quality_signal",
]
