"""Production hardening derived from real run #387.

This module closes four gaps observed in the 2026-08-08 production run:
1. task-explicit semantic obligations could be empty while headings existed;
2. deterministic normalization removed legitimate task-derived quantities;
3. truncation immediately switched companies instead of first repairing request shape;
4. recovery ordering did not prefer cross-company heterogeneity when quality/risk tied.

Company diversity remains a soft execution objective, never a model eligibility gate.
"""
from __future__ import annotations

import ast
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

import v5_constitutional_runtime_legacy as constitutional_legacy
import v5_deterministic_answer_normalization as base_normalization
import v5_task_constraints as base_constraints
from execution_graph import SelectedNode
from v5_model_company import canonical_model_company
from v5_production_expert_policy import EvidenceCompleteExecutionEngine
from v5_runtime import ExecutionEngine as DynamicExecutionEngine
from v5_runtime import FailureCategory, ProductionRuntime, RuntimeAttempt


_CALC_REQUEST_RE = re.compile(r"(?:计算|测算|算出|总成本|期望成本|预期成本|成本计算)", re.I)
_AUDITABLE_CALC_RE = re.compile(r"(?:展示|给出|列出).{0,12}(?:计算|算式|公式)|可复核.{0,8}计算", re.I)
_THRESHOLD_RE = re.compile(r"(?:临界值|阈值|切换条件|转换条件|拐点)", re.I)
_SENSITIVITY_RE = re.compile(r"(?:敏感性|敏感度|敏感)", re.I)
_ERROR_SCENARIO_RE = re.compile(r"(?:±\s*\d+(?:\.\d+)?\s*%|误差|高估|低估)", re.I)
_DISTINGUISH_RE = re.compile(r"(?:区分|分别标明|明确标明).{0,30}(?:事实|确定数据).{0,30}(?:计算|判断|建议)", re.I)
_GOAL_RE = re.compile(
    r"(?:^|[，,、；;：:\s])([\u4e00-\u9fff]{2,8}(?:优先|均衡))(?=[，,、；;\s三四五六七八九十])"
)
_NUMERIC_CALC_RE = re.compile(
    r"\d+(?:\.\d+)?[^\n]{0,50}[+\-*/×÷][^\n]{0,50}\d+(?:\.\d+)?[^\n]{0,50}[=＝][^\n]{0,20}-?\d+(?:\.\d+)?"
)
_CONSTANT_EQUATION_RE = re.compile(
    r"(?P<expr>-?\d+(?:\.\d+)?(?:\s*[+\-*/×÷]\s*-?\d+(?:\.\d+)?)+)\s*[=＝]\s*(?P<result>-?\d+(?:\.\d+)?)"
)
_LINEAR_THRESHOLD_RE = re.compile(
    r"(?P<left>[+\-\d.Ll\s]+)[=＝](?P<right>[+\-\d.Ll\s]+?)(?:→|->|⇒|，|,|；|;).{0,40}?[Ll]\s*(?:≈|~=|=|＝)\s*(?P<stated>-?\d+(?:\.\d+)?)"
)


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def _introduced_quantities(task: str, text: str) -> set[str]:
    return set(base_constraints.normalized_quantities(text)) - set(
        base_constraints.normalized_quantities(task)
    )


def _context_windows(answer: str, pattern: re.Pattern[str], radius: int = 3) -> list[str]:
    lines = answer.splitlines()
    result: list[str] = []
    for index, line in enumerate(lines):
        if pattern.search(line):
            lo = max(0, index - 1)
            hi = min(len(lines), index + radius + 1)
            result.append("\n".join(lines[lo:hi]))
    return result


def _explicit_goal_labels(task: str) -> list[str]:
    labels = [match.group(1).strip() for match in _GOAL_RE.finditer(task)]
    return list(dict.fromkeys(label for label in labels if len(label) <= 8))


def task_obligation_violations(task: str, answer: str) -> list[str]:
    """Validate only obligations explicitly requested by the current task.

    No domain keyword selects a model or fixed role. The checks compile observable
    delivery obligations from the task itself and verify that the final answer has
    substantive payload, not merely the requested heading/marker text.
    """
    task_text = str(task or "")
    rendered = str(answer or "").strip()
    violations: list[str] = []
    if not rendered:
        return ["missing-task-obligation:nonempty-delivery"]

    introduced = _introduced_quantities(task_text, rendered)
    if _CALC_REQUEST_RE.search(task_text):
        if not introduced:
            violations.append("missing-task-obligation:derived-calculation-result")
        if _AUDITABLE_CALC_RE.search(task_text) and not _NUMERIC_CALC_RE.search(rendered):
            violations.append("missing-task-obligation:auditable-numeric-calculation")

    if _THRESHOLD_RE.search(task_text):
        threshold_windows = _context_windows(rendered, _THRESHOLD_RE)
        threshold_derived = set().union(
            *(_introduced_quantities(task_text, window) for window in threshold_windows)
        ) if threshold_windows else set()
        if not threshold_windows or not threshold_derived:
            violations.append("missing-task-obligation:derived-threshold-or-switch-condition")

    if _SENSITIVITY_RE.search(task_text):
        if not _SENSITIVITY_RE.search(rendered):
            violations.append("missing-task-obligation:sensitivity-analysis")
        if _ERROR_SCENARIO_RE.search(task_text):
            scenario_windows = _context_windows(
                rendered,
                re.compile(r"(?:±|误差|高估|低估|情景|上限|下限|敏感)", re.I),
                radius=5,
            )
            scenario_derived = set().union(
                *(_introduced_quantities(task_text, window) for window in scenario_windows)
            ) if scenario_windows else set()
            # A two-sided error request needs observable lower/upper derived states.
            if len(scenario_derived) < 2:
                violations.append("missing-task-obligation:two-sided-error-scenarios")

    for label in _explicit_goal_labels(task_text):
        match = re.search(re.escape(label), rendered)
        if not match:
            violations.append(f"missing-task-obligation:goal-recommendation:{label}")
            continue
        window = rendered[match.start() : match.start() + 180]
        if not re.search(r"(?:选|推荐|建议|采用|方案)", window):
            violations.append(f"empty-task-obligation:goal-recommendation:{label}")

    if _DISTINGUISH_RE.search(task_text):
        groups = {
            "fact": ("事实", "确定数据", "题面数据"),
            "calculated": ("计算结果", "派生计算", "计算"),
            "judgment": ("判断", "建议", "推荐"),
        }
        for name, markers in groups.items():
            if not any(marker in rendered for marker in markers):
                violations.append(f"missing-task-obligation:classification:{name}")
    return _dedupe(violations)


def _decimal_from_ast(node: ast.AST) -> Decimal:
    if isinstance(node, ast.Expression):
        return _decimal_from_ast(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _decimal_from_ast(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp) and isinstance(
        node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
    ):
        left = _decimal_from_ast(node.left)
        right = _decimal_from_ast(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if right == 0:
            raise InvalidOperation("division by zero")
        return left / right
    raise InvalidOperation("unsupported arithmetic syntax")


def _evaluate_constant_expression(expression: str) -> Decimal:
    normalized = expression.replace("×", "*").replace("÷", "/")
    tree = ast.parse(normalized, mode="eval")
    return _decimal_from_ast(tree)


def _rounding_tolerance(stated_text: str, approximate: bool = False) -> Decimal:
    decimals = len(stated_text.partition(".")[2]) if "." in stated_text else 0
    unit = Decimal(1).scaleb(-decimals)
    return unit if approximate else unit / Decimal(2)


def _linear_terms(expression: str) -> tuple[Decimal, Decimal] | None:
    compact = expression.upper().replace(" ", "")
    if not compact or not re.fullmatch(r"[+\-\d.L]+", compact):
        return None
    if compact[0] not in "+-":
        compact = "+" + compact
    coefficient = Decimal(0)
    constant = Decimal(0)
    for term in re.findall(r"[+\-][^+\-]+", compact):
        sign = Decimal(-1) if term[0] == "-" else Decimal(1)
        body = term[1:]
        try:
            if body.endswith("L"):
                raw = body[:-1]
                coefficient += sign * (Decimal(raw) if raw else Decimal(1))
            else:
                constant += sign * Decimal(body)
        except InvalidOperation:
            return None
    return coefficient, constant


def arithmetic_consistency_violations(answer: str) -> list[str]:
    """Check explicit arithmetic the model chose to expose.

    This is mathematical consistency, not a quality-score heuristic. Expressions
    without an observable equality are ignored; no hidden chain of thought is used.
    """
    violations: list[str] = []
    for line_number, line in enumerate(str(answer or "").splitlines(), 1):
        for match in _CONSTANT_EQUATION_RE.finditer(line):
            try:
                expected = _evaluate_constant_expression(match.group("expr"))
                stated = Decimal(match.group("result"))
            except (InvalidOperation, SyntaxError, ValueError):
                continue
            tolerance = _rounding_tolerance(match.group("result"), approximate=False)
            if abs(expected - stated) > tolerance:
                violations.append(
                    f"arithmetic-inconsistency:line-{line_number}:{match.group('expr')}={match.group('result')}"
                )

        linear = _LINEAR_THRESHOLD_RE.search(line)
        if linear:
            left = _linear_terms(linear.group("left"))
            right = _linear_terms(linear.group("right"))
            if left is None or right is None:
                continue
            a1, b1 = left
            a2, b2 = right
            denominator = a1 - a2
            if denominator == 0:
                continue
            expected = (b2 - b1) / denominator
            try:
                stated = Decimal(linear.group("stated"))
            except InvalidOperation:
                continue
            tolerance = _rounding_tolerance(linear.group("stated"), approximate=True)
            if abs(expected - stated) > tolerance:
                violations.append(
                    f"linear-threshold-inconsistency:line-{line_number}:expected={expected}:stated={stated}"
                )
    return _dedupe(violations)


def hardened_validate_answer_evidence(
    task: str,
    answer: str,
    constraints: base_constraints.TaskConstraints | None = None,
) -> list[str]:
    active = constraints or base_constraints.compile_task_constraints(task)
    violations = list(base_constraints.validate_answer_evidence(task, answer, active))
    violations.extend(arithmetic_consistency_violations(answer))
    violations.extend(task_obligation_violations(task, answer))
    return _dedupe(violations)


def _quantity_violations_for_line(
    task: str,
    line: str,
    constraints: base_constraints.TaskConstraints,
) -> list[str]:
    return [
        value
        for value in base_constraints.validate_answer_evidence(task, line, constraints)
        if value.startswith("closed-world-unproven-derived-quantity:")
        or value.startswith("closed-world-unsupported-quantity:")
    ]


def hardened_normalize_answer(
    task: str,
    answer: str,
    output_contract: Mapping[str, Any],
    constraints: base_constraints.TaskConstraints,
) -> tuple[str, dict[str, Any]]:
    """Preserve provenance-backed derived quantities while deleting inventions."""
    original = str(answer or "")
    working, mixed_label_actions = base_normalization._split_mixed_fact_labels(original)
    working, inferential_relabels = base_normalization._relabel_inferential_facts(
        task, working
    )
    removed: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []

    if not constraints.unsupported_precise_quantities_allowed:
        allowed = base_normalization._normalized_quantities(task)
        kept: list[str] = []
        for line_number, line in enumerate(working.splitlines(), 1):
            line_quantities = base_normalization._normalized_quantities(line)
            unsupported = sorted(line_quantities - allowed)
            if unsupported:
                quantity_violations = _quantity_violations_for_line(task, line, constraints)
                if quantity_violations:
                    removed.append(
                        {
                            "line": line_number,
                            "unsupported_quantities": unsupported,
                            "quantity_violations": quantity_violations,
                            "sha256": base_normalization._sha(line),
                        }
                    )
                    continue
                preserved.append(
                    {
                        "line": line_number,
                        "derived_quantities": unsupported,
                        "provenance": "local-arithmetic-or-derived-context",
                        "sha256": base_normalization._sha(line),
                    }
                )
            kept.append(line)
        working = "\n".join(kept)

    working, h2_audit = base_normalization._canonicalize_h2(working, output_contract)
    normalized = working.strip()
    audit = {
        "schema_version": "deterministic-answer-normalization-3",
        "applied": bool(
            normalized != original.strip()
            or removed
            or preserved
            or h2_audit.get("applied")
            or mixed_label_actions
            or inferential_relabels
        ),
        "source_sha256": base_normalization._sha(original),
        "normalized_sha256": base_normalization._sha(normalized),
        "closed_world_quantity_filter_applied": (
            not constraints.unsupported_precise_quantities_allowed
        ),
        "removed_line_count": len(removed),
        "removed_lines": removed,
        "preserved_derived_quantity_line_count": len(preserved),
        "preserved_derived_quantity_lines": preserved,
        "derived_quantities_preserved_when_proven": True,
        "mixed_fact_label_split_count": len(mixed_label_actions),
        "mixed_fact_label_splits": mixed_label_actions,
        "inferential_fact_relabel_count": len(inferential_relabels),
        "inferential_fact_relabels": inferential_relabels,
        "h2_normalization": h2_audit,
        "new_external_facts_added": False,
        "new_quantities_added": False,
        "external_tools_used": False,
        "model_calls_added": 0,
    }
    return normalized, audit


class HeterogeneousEvidenceExecutionEngine(EvidenceCompleteExecutionEngine):
    """Prefer heterogeneity after task quality/risk, and repair truncation first."""

    def _ensure_production_failure_state(self) -> None:
        super()._ensure_production_failure_state()
        if not hasattr(self, "_attempted_company_sequence"):
            self._attempted_company_sequence: list[str] = []
        if not hasattr(self, "_same_model_truncation_retries"):
            self._same_model_truncation_retries: list[dict[str, Any]] = []

    def _record_feedback(self, attempt: Any | None) -> None:
        super()._record_feedback(attempt)
        if attempt is None:
            return
        self._ensure_production_failure_state()
        model = str(getattr(attempt, "model", "") or "").strip()
        company = canonical_model_company(model)
        if company and company != "unknown":
            self._attempted_company_sequence.append(company)

    @classmethod
    def _failure_rank_key(
        cls,
        row: Mapping[str, Any],
        category: Any,
        tried_companies: set[str],
    ) -> tuple[Any, ...]:
        category_value = str(getattr(category, "value", category))
        quality = cls._number(row, "estimated_quality", 0.0)
        failure = cls._number(row, "failure_probability", 1.0)
        cost = cls._number(row, "estimated_cost", 0.0)
        model = str(row.get("model") or "")
        company = canonical_model_company(model)
        repeated_company = int(company in tried_companies and company != "unknown")
        # Capability / observed failure-risk remain ahead of diversity. Diversity
        # breaks the next soft dimension before cost/model identity.
        if category_value == FailureCategory.QUALITY_GATE_FAILED.value:
            return (-quality, failure, repeated_company, cost, model)
        return (failure, -quality, repeated_company, cost, model)

    def _diversify_rows(
        self,
        rows: Sequence[Mapping[str, Any]],
        category: Any,
    ) -> list[dict[str, Any]]:
        self._ensure_production_failure_state()
        tried = set(self._attempted_company_sequence)
        return sorted(
            (dict(row) for row in rows),
            key=lambda row: self._failure_rank_key(row, category, tried),
        )

    def _rerank_standby_for_failure(self, category: Any) -> None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            before = [
                str(row.get("model") or "")
                for row in self._standby_inventory
                if str(row.get("model") or "") not in self._standby_claimed
            ]
            tried = set(self._attempted_company_sequence)
            ranked = sorted(
                (dict(row) for row in self._standby_inventory),
                key=lambda row: self._failure_rank_key(row, category, tried),
            )
            self._standby_inventory = ranked
            after = [
                str(row.get("model") or "")
                for row in self._standby_inventory
                if str(row.get("model") or "") not in self._standby_claimed
            ]
            self._standby_rerank_events.append(
                {
                    "trigger_category": str(getattr(category, "value", category)),
                    "candidate_count": len(after),
                    "order_changed": before != after,
                    "top_before": before[:8],
                    "top_after": after[:8],
                    "policy": "task-quality-risk-first-then-company-heterogeneity-before-cost",
                    "company_diversity_is_execution_gate": False,
                    "cross_task_history_used": False,
                }
            )

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            eligible: list[Mapping[str, Any]] = []
            for row in self._standby_inventory:
                model = str(row.get("model") or "").strip()
                if (
                    not model
                    or model in self._standby_claimed
                    or model in self._hard_failed_model_ids
                ):
                    continue
                eligible.append(row)
            if not eligible:
                return None
            tried = set(self._attempted_company_sequence)
            chosen = next(
                (
                    row
                    for row in eligible
                    if canonical_model_company(str(row.get("model") or "")) not in tried
                ),
                eligible[0],
            )
            model = str(chosen.get("model") or "").strip()
            self._standby_claimed.add(model)
            return dict(chosen)

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        self._ensure_production_failure_state()

        # A truncation is first treated as a request-shape problem. Reuse the
        # same model exactly once with a feedback-derived larger allowance before
        # paying for a different model/company.
        if category == FailureCategory.OUTPUT_TRUNCATED and attempts:
            source = attempts[-1]
            adapted, adaptation = self._replacement_adaptation(
                selected, source, False
            )
            retried = call(adapted, "retry")
            if retried is not None:
                if adaptation is not None:
                    retried.answer_transformations.append(adaptation)
                self._same_model_truncation_retries.append(
                    {
                        "model": selected.model,
                        "source_attempt_index": int(getattr(source, "attempt_index", 0)),
                        "retry_attempt_index": int(getattr(retried, "attempt_index", 0)),
                        "status": str(getattr(retried, "status", "")),
                        "policy": "same-model-feedback-rebind-before-cross-model-recovery",
                    }
                )
                if retried.status == "passed":
                    return (
                        self._node_result(
                            selected,
                            adapted,
                            attempts,
                            retried,
                            "success_retried",
                        ),
                        best,
                        adapted,
                    )
                best = self._better_degraded(
                    best,
                    retried,
                    adapted,
                    self._degraded_usable(adapted, retried),
                )
                category = self._category(retried)

        filtered = [
            row
            for row in recovery_rows
            if str(row.get("model") or "").strip() not in self._hard_failed_model_ids
        ]
        ranked = self._diversify_rows(filtered, category)
        self._rerank_standby_for_failure(category)
        return DynamicExecutionEngine._recover_node(
            self,
            selected,
            attempts,
            ranked,
            category,
            best,
            call,
        )

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        self._ensure_production_failure_state()
        sequence = list(self._attempted_company_sequence)
        value.update(
            {
                "company_heterogeneity_soft_objective": True,
                "company_diversity_is_execution_gate": False,
                "attempted_company_sequence": sequence,
                "distinct_attempted_company_count": len(set(sequence)),
                "same_model_truncation_retry_before_substitution": True,
                "same_model_truncation_retries": list(self._same_model_truncation_retries),
            }
        )
        return value

    @classmethod
    def _actual_company_audit(
        cls,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        audit = dict(super()._actual_company_audit(result))
        rows = audit.get("all_called_models")
        rows = rows if isinstance(rows, list) else []
        sequence = [
            canonical_model_company(str(row.get("model") or ""))
            for row in rows
            if isinstance(row, Mapping) and str(row.get("model") or "").strip()
        ]
        sequence = [company for company in sequence if company and company != "unknown"]
        consecutive = [
            {"left_call": index, "right_call": index + 1, "company": sequence[index - 1]}
            for index in range(1, len(sequence))
            if sequence[index - 1] == sequence[index]
        ]
        distinct = len(set(sequence))
        calls = len(sequence)
        first_repeat = next(
            (
                index
                for index, company in enumerate(sequence, 1)
                if company in sequence[: index - 1]
            ),
            None,
        )
        audit.update(
            {
                "company_heterogeneity_policy": "soft-task-quality-risk-first-diversity-preference",
                "company_diversity_is_execution_gate": False,
                "called_company_sequence": sequence,
                "called_company_count": calls,
                "distinct_called_company_count": distinct,
                "company_heterogeneity_ratio": round(distinct / max(1, calls), 6),
                "same_company_attempt_reuse_count": max(0, calls - distinct),
                "consecutive_same_company_pairs": consecutive,
                "consecutive_same_company_pair_count": len(consecutive),
                "distinct_companies_before_first_repeat": (
                    first_repeat - 1 if first_repeat is not None else distinct
                ),
                "heterogeneity_observation": (
                    "all-calls-distinct-company" if distinct == calls else "mixed-company-reuse"
                ),
            }
        )
        return audit


def install_run387_hardening(runtime: ProductionRuntime) -> ProductionRuntime:
    """Install semantic truthfulness and heterogeneity without new hard gates."""
    # ConstitutionalExecutionEngine resolves these module globals at runtime.
    # Patch the active compatibility module explicitly so both per-attempt and
    # final evidence checks use the hardened semantics.
    constitutional_legacy.validate_answer_evidence = hardened_validate_answer_evidence
    constitutional_legacy.normalize_answer = hardened_normalize_answer

    runtime.execution_engine = HeterogeneousEvidenceExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "HeterogeneousEvidenceExecutionEngine",
    "arithmetic_consistency_violations",
    "hardened_normalize_answer",
    "hardened_validate_answer_evidence",
    "install_run387_hardening",
    "task_obligation_violations",
]
