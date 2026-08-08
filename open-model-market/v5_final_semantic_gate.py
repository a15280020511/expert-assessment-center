"""Scope task-explicit semantic completeness to final delivery surfaces.

Arithmetic consistency is safe to validate on every expert work product because it
only checks equalities the model explicitly emitted. Whole-task obligation coverage
belongs only on final delivery nodes / the final report; applying it to internal
analysis nodes would create unnecessary recovery calls.

The live E2E governance run #300 additionally proved that a final answer can look
substantive while still omitting an explicitly requested scenario/table/transition,
or while reversing the semantic direction of a high/low-estimate sensitivity label.
Those checks are compiled only from explicit task wording and remain final-delivery
obligations rather than internal-node requirements.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

import v5_constitutional_runtime_legacy as constitutional_legacy
import v5_task_constraints as base_constraints
from execution_graph import SelectedNode
from v5_run387_hardening import (
    HeterogeneousEvidenceExecutionEngine,
    arithmetic_consistency_violations as _legacy_arithmetic_consistency_violations,
    task_obligation_violations,
)
from v5_runtime import BudgetController, ExecutionFailure, FailureCategory, RuntimeAttempt

_PERCENT_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*%")
_SCENARIO_HINT_RE = re.compile(
    r"(?:分别|情景|场景|变化|敏感|误差|高估|低估|当前值|上调|下调|±)",
    re.I,
)
_TABLE_REQUEST_RE = re.compile(r"(?:决策表|对比表|比较表|表格|表形式)", re.I)
_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?(?:\s*:?-{3,}:?\s*\|){2,}\s*$"
)
_TABLE_CLASSIFICATION_TASK_RE = re.compile(
    r"(?:事实|题面).{0,40}(?:派生|计算).{0,40}(?:判断|推断|建议|推荐|结论)",
    re.I,
)
_ALL_OPTION_THRESHOLD_RE = re.compile(
    r"(?:(?:各|全部).{0,8}(?:方案|选项).{0,14}(?:临界|切换)|"
    r"(?:临界|切换).{0,14}(?:各|全部).{0,8}(?:方案|选项))",
    re.I,
)
_OPTION_LABEL_RE = re.compile(r"(?<![A-Za-z])([A-H])(?![A-Za-z])")
_THRESHOLD_PAIR_RE = re.compile(
    r"(?<![A-Za-z])([A-H])\s*(?:与|和|↔|/|vs\.?|VS|=|＝)\s*([A-H])(?![A-Za-z])",
    re.I,
)
_ACTUAL_RELATIVE_RE = re.compile(
    r"实际.{0,12}(?:为|是)?\s*(?:预期|估计|题面|基准).{0,8}"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>倍|%)",
    re.I,
)
_HIGH_ESTIMATE_RE = re.compile(r"高估\s*\d+(?:\.\d+)?\s*%", re.I)
_LOW_ESTIMATE_RE = re.compile(r"低估\s*\d+(?:\.\d+)?\s*%", re.I)
_LINEAR_PAIR_RE = re.compile(
    r"(?P<left>-?\d+(?:\.\d+)?\s*[+\-]\s*\d*(?:\.\d+)?\s*(?P<var>[LlXx]))"
    r"\s*[=＝]\s*"
    r"(?P<right>-?\d+(?:\.\d+)?\s*[+\-]\s*\d*(?:\.\d+)?\s*(?P=var))"
)


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _percent_values(text: str) -> set[Decimal]:
    values: set[Decimal] = set()
    for match in _PERCENT_RE.finditer(str(text or "")):
        value = _decimal(match.group(1))
        if value is not None:
            values.add(value.normalize())
    return values


def _requested_scenario_percentages(task: str) -> set[Decimal]:
    requested: set[Decimal] = set()
    for clause in re.split(r"[。；;\n]+", str(task or "")):
        if not _SCENARIO_HINT_RE.search(clause):
            continue
        requested.update(_percent_values(clause))
    return requested


def _table_lines(answer: str) -> list[str]:
    return [line for line in str(answer or "").splitlines() if line.count("|") >= 2]


def _has_markdown_table(answer: str) -> bool:
    return any(_TABLE_SEPARATOR_RE.match(line) for line in _table_lines(answer))


def _option_labels(task: str) -> list[str]:
    if "方案" not in str(task or "") and "选项" not in str(task or ""):
        return []
    return list(dict.fromkeys(match.group(1).upper() for match in _OPTION_LABEL_RE.finditer(task)))


def _threshold_pair_coverage(answer: str, labels: Sequence[str]) -> set[str]:
    graph: dict[str, set[str]] = {label: set() for label in labels}
    for match in _THRESHOLD_PAIR_RE.finditer(str(answer or "")):
        left = match.group(1).upper()
        right = match.group(2).upper()
        if left not in graph or right not in graph or left == right:
            continue
        graph[left].add(right)
        graph[right].add(left)
    if not labels:
        return set()
    visited: set[str] = set()
    stack = [labels[0]]
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        stack.extend(graph.get(current, set()) - visited)
    return visited


def scenario_direction_violations(answer: str) -> list[str]:
    """Reject high/low estimate labels whose stated actual direction is reversed."""
    violations: list[str] = []
    for line_number, line in enumerate(str(answer or "").splitlines(), 1):
        relation = _ACTUAL_RELATIVE_RE.search(line)
        if relation is None:
            continue
        value = _decimal(relation.group("value"))
        if value is None:
            continue
        ratio = value / Decimal(100) if relation.group("unit") == "%" else value
        if _HIGH_ESTIMATE_RE.search(line) and ratio >= Decimal(1):
            violations.append(
                f"scenario-direction-inconsistency:line-{line_number}:high-estimate-actual-not-lower"
            )
        if _LOW_ESTIMATE_RE.search(line) and ratio <= Decimal(1):
            violations.append(
                f"scenario-direction-inconsistency:line-{line_number}:low-estimate-actual-not-higher"
            )
    return _dedupe(violations)


def _parse_linear(expression: str, variable: str) -> tuple[Decimal, Decimal] | None:
    compact = re.sub(r"\s+", "", expression).upper()
    variable = variable.upper()
    match = re.fullmatch(
        rf"(?P<constant>-?\d+(?:\.\d+)?)(?P<sign>[+\-])(?P<coef>\d*(?:\.\d+)?)({variable})",
        compact,
    )
    if match is None:
        return None
    constant = _decimal(match.group("constant"))
    coefficient = _decimal(match.group("coef") or "1")
    if constant is None or coefficient is None:
        return None
    if match.group("sign") == "-":
        coefficient = -coefficient
    return coefficient, constant


def _robust_linear_threshold_violations(answer: str) -> list[str]:
    """Validate exposed linear thresholds without misreading chained equations."""
    violations: list[str] = []
    for line_number, line in enumerate(str(answer or "").splitlines(), 1):
        pair = _LINEAR_PAIR_RE.search(line)
        if pair is None:
            continue
        variable = pair.group("var")
        left = _parse_linear(pair.group("left"), variable)
        right = _parse_linear(pair.group("right"), variable)
        if left is None or right is None:
            continue
        token = (
            re.compile(
                r"\b[Ll](?:_[A-Za-z]{1,8})?\b\s*(?:≈|~=|=|＝)\s*(-?\d+(?:\.\d+)?)"
            )
            if variable.lower() == "l"
            else re.compile(r"\b[xX]\b\s*(?:≈|~=|=|＝)\s*(-?\d+(?:\.\d+)?)")
        )
        stated_matches = list(token.finditer(line))
        if not stated_matches:
            continue
        stated_text = stated_matches[-1].group(1)
        stated = _decimal(stated_text)
        if stated is None:
            continue
        left_coefficient, left_constant = left
        right_coefficient, right_constant = right
        denominator = left_coefficient - right_coefficient
        if denominator == 0:
            continue
        expected = (right_constant - left_constant) / denominator
        decimals = len(stated_text.partition(".")[2]) if "." in stated_text else 0
        tolerance = Decimal(2).scaleb(-decimals)
        if abs(expected - stated) > tolerance:
            violations.append(
                "linear-threshold-inconsistency:"
                f"line-{line_number}:expected={expected}:stated={stated}"
            )
    return _dedupe(violations)


def arithmetic_consistency_violations(answer: str) -> list[str]:
    """Keep constant arithmetic checks and replace the chain-prone linear parser."""
    legacy = [
        value
        for value in _legacy_arithmetic_consistency_violations(answer)
        if not value.startswith("linear-threshold-inconsistency:")
    ]
    return _dedupe([*legacy, *_robust_linear_threshold_violations(answer)])


def production_task_obligation_violations(task: str, answer: str) -> list[str]:
    """Extend run-387 obligations with explicit live-E2E delivery semantics."""
    task_text = str(task or "")
    rendered = str(answer or "")
    violations = list(task_obligation_violations(task_text, rendered))

    requested_percentages = _requested_scenario_percentages(task_text)
    observed_percentages = _percent_values(rendered)
    for percentage in sorted(requested_percentages):
        if percentage not in observed_percentages:
            violations.append(
                "missing-task-obligation:explicit-scenario-percentage:"
                f"{format(percentage, 'f')}%"
            )

    table_requested = bool(_TABLE_REQUEST_RE.search(task_text))
    table_present = _has_markdown_table(rendered)
    if table_requested and not table_present:
        violations.append("missing-task-obligation:decision-table")
    if (
        table_requested
        and table_present
        and _TABLE_CLASSIFICATION_TASK_RE.search(task_text)
    ):
        table_text = "\n".join(_table_lines(rendered))
        groups = {
            "fact": ("事实", "题面", "已知"),
            "calculated": ("计算", "派生", "结果"),
            "judgment": ("判断", "推断", "建议", "推荐", "结论"),
        }
        for name, markers in groups.items():
            if not any(marker in table_text for marker in markers):
                violations.append(
                    f"missing-task-obligation:decision-table-classification:{name}"
                )

    labels = _option_labels(task_text)
    if len(labels) >= 3 and _ALL_OPTION_THRESHOLD_RE.search(task_text):
        connected = _threshold_pair_coverage(rendered, labels)
        missing = [label for label in labels if label not in connected]
        if missing:
            violations.append(
                "missing-task-obligation:threshold-transition-coverage:"
                + ",".join(missing)
            )

    violations.extend(scenario_direction_violations(rendered))
    return _dedupe(violations)


def work_product_evidence_validator(
    task: str,
    answer: str,
    constraints: base_constraints.TaskConstraints | None = None,
) -> list[str]:
    """Evidence + explicit math consistency for every node, no whole-task coverage."""
    active = constraints or base_constraints.compile_task_constraints(task)
    violations = list(base_constraints.validate_answer_evidence(task, answer, active))
    violations.extend(arithmetic_consistency_violations(answer))
    return _dedupe(violations)


def _final_attempt_obligation_failure(
    engine: HeterogeneousEvidenceExecutionEngine,
    node: SelectedNode,
    original_task: str,
    attempt: RuntimeAttempt | None,
) -> RuntimeAttempt | None:
    if (
        attempt is None
        or not attempt.answer
        or node.output_contract.get("final_delivery_node") is not True
    ):
        return attempt
    obligations = production_task_obligation_violations(original_task, attempt.answer)
    if not obligations:
        return attempt
    attempt.gate_reasons = _dedupe([*attempt.gate_reasons, *obligations])
    if attempt.status == "passed":
        attempt.status = "quality_gate_failed"
        attempt.failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request_sent=True,
            response_received=True,
            usage_received=bool(attempt.usage),
            actual_cost_usd=engine._actual_cost({"usage": attempt.usage}),
            message=";".join(obligations),
        ).to_dict()
    return attempt


def install_final_semantic_gate() -> None:
    """Install final-only semantic obligations after run-387 base hardening."""
    constitutional_legacy.validate_answer_evidence = work_product_evidence_validator

    cls = HeterogeneousEvidenceExecutionEngine
    if getattr(cls, "_final_semantic_gate_installed", False):
        return

    original_attempt = cls._attempt
    original_evidence_audit = cls._evidence_audit

    def final_aware_attempt(
        self: HeterogeneousEvidenceExecutionEngine,
        node: SelectedNode,
        selected_node_id: str,
        kind: str,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Any,
        budget: BudgetController,
        attempt_index: int,
    ) -> RuntimeAttempt | None:
        attempt = original_attempt(
            self,
            node,
            selected_node_id,
            kind,
            original_task,
            upstream,
            run,
            call_fn,
            budget,
            attempt_index,
        )
        return _final_attempt_obligation_failure(
            self,
            node,
            original_task,
            attempt,
        )

    @staticmethod
    def final_aware_evidence_audit(
        original_task: str,
        answer: str,
        constraints: base_constraints.TaskConstraints,
        *,
        after_failure: bool = False,
    ) -> dict[str, Any]:
        payload = dict(
            original_evidence_audit(
                original_task,
                answer,
                constraints,
                after_failure=after_failure,
            )
        )
        obligations = production_task_obligation_violations(original_task, answer)
        violations = _dedupe([*payload.get("violations", []), *obligations])
        payload["violations"] = violations
        payload["status"] = "FAIL" if violations else "PASS"
        payload["task_explicit_obligation_gate"] = {
            "status": "FAIL" if obligations else "PASS",
            "scope": "final-delivery-only",
            "violations": obligations,
            "internal_nodes_required_to_answer_full_task": False,
        }
        return payload

    cls._attempt = final_aware_attempt
    cls._evidence_audit = final_aware_evidence_audit
    cls._final_semantic_gate_installed = True


__all__ = [
    "arithmetic_consistency_violations",
    "install_final_semantic_gate",
    "production_task_obligation_violations",
    "scenario_direction_violations",
    "work_product_evidence_validator",
]
