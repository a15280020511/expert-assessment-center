#!/usr/bin/env python3
"""Apply deterministic closed-world answer normalization as one qualified tree."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
TESTS = ROOT / "tests"


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one marker in {path}, got {count}: {old[:100]!r}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def replace_class_method(path: Path, class_name: str, method_name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for item in tree.body:
        if not isinstance(item, ast.ClassDef) or item.name != class_name:
            continue
        for child in item.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == method_name:
                matches.append(child)
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one {class_name}.{method_name} in {path}, got {len(matches)}"
        )
    node = matches[0]
    start = node.lineno
    if node.decorator_list:
        start = min(value.lineno for value in node.decorator_list)
    lines = source.splitlines(keepends=True)
    lines[start - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    path.write_text("".join(lines), encoding="utf-8")


def write_normalizer() -> None:
    path = MARKET / "v5_deterministic_answer_normalization.py"
    path.write_text(
        '''"""Deterministic, auditable normalization before constitutional quality gates."""
from __future__ import annotations

import re
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping, Sequence

from v5_task_constraints import TaskConstraints, normalized_quantities

_H2_RE = re.compile(r"^\\s{0,3}##\\s+(.+?)\\s*#*\\s*$")


def _heading_key(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\\d+(?:\\.\\d+)*[\\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\\u4e00-\\u9fff]+", "_", value)
    return value.strip("_")


def _quantity_token(value: tuple[str, str, str]) -> str:
    lo, hi, unit = value
    return f"{lo}{('-' + hi) if hi else ''}:{unit}"


def _required_h2(output_contract: Mapping[str, Any]) -> list[str]:
    if output_contract.get("machine_readable_required"):
        return []
    values = output_contract.get("exact_markdown_headings")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = output_contract.get("required_fields")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _canonicalize_h2(
    answer: str,
    output_contract: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    required = _required_h2(output_contract)
    audit: dict[str, Any] = {
        "required_h2": required,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reordered": False,
        "h2_reorder_blocked_reason": None,
    }
    if not required:
        return answer, audit

    lines = answer.splitlines()
    matches: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _H2_RE.match(line)
        if match:
            matches.append((index, match.group(1).strip(), line))
    observed = [heading for _, heading, _ in matches]
    audit["original_h2_order"] = observed
    audit["normalized_h2_order"] = observed
    if not matches:
        audit["h2_reorder_blocked_reason"] = "no-h2-headings"
        return answer, audit
    if any(line.strip() for line in lines[: matches[0][0]]):
        audit["h2_reorder_blocked_reason"] = "nonempty-preamble"
        return answer, audit

    required_keys = [_heading_key(value) for value in required]
    observed_keys = [_heading_key(value) for value in observed]
    if Counter(observed_keys) != Counter(required_keys):
        audit["h2_reorder_blocked_reason"] = "heading-set-not-exact"
        return answer, audit
    if len(set(observed_keys)) != len(observed_keys):
        audit["h2_reorder_blocked_reason"] = "duplicate-heading"
        return answer, audit

    blocks: dict[str, list[str]] = {}
    for offset, (start, _, _) in enumerate(matches):
        end = matches[offset + 1][0] if offset + 1 < len(matches) else len(lines)
        block = lines[start:end]
        body = "\n".join(block[1:]).strip()
        key = observed_keys[offset]
        if not body:
            audit["h2_reorder_blocked_reason"] = "empty-required-section"
            return answer, audit
        blocks[key] = block

    if observed_keys == required_keys:
        return answer, audit
    reordered: list[str] = []
    for key in required_keys:
        if reordered and reordered[-1].strip():
            reordered.append("")
        reordered.extend(blocks[key])
    normalized = "\n".join(reordered).strip() + "\n"
    audit["h2_reordered"] = True
    audit["normalized_h2_order"] = required
    return normalized, audit


def normalize_answer(
    task: str,
    answer: str,
    output_contract: Mapping[str, Any],
    constraints: TaskConstraints,
) -> tuple[str, dict[str, Any]]:
    """Remove unsupported numeric lines and canonically reorder complete H2 blocks.

    This function never invents text. It may delete the smallest physical lines
    containing unsupported exact quantities and may move already complete,
    uniquely named H2 sections into the compiled contract order.
    """
    original = str(answer or "")
    audit: dict[str, Any] = {
        "schema_version": "v5-deterministic-answer-normalization-1",
        "policy": "delete-unsupported-quantity-lines-and-reorder-complete-h2-only",
        "applied": False,
        "original_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "normalized_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "allowed_quantities": [],
        "removed_lines": [],
        "unsupported_quantities_removed": [],
        "h2_reordered": False,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reorder_blocked_reason": None,
        "text_invented": False,
    }
    working = original
    if not constraints.unsupported_precise_quantities_allowed:
        allowed = normalized_quantities(task)
        audit["allowed_quantities"] = sorted(_quantity_token(value) for value in allowed)
        kept: list[str] = []
        removed_tokens: set[str] = set()
        for line_number, line in enumerate(working.splitlines(), start=1):
            unsupported = normalized_quantities(line) - allowed
            if unsupported and not _H2_RE.match(line):
                tokens = sorted(_quantity_token(value) for value in unsupported)
                removed_tokens.update(tokens)
                audit["removed_lines"].append(
                    {
                        "line_number": line_number,
                        "text": line,
                        "unsupported_quantities": tokens,
                    }
                )
                continue
            kept.append(line)
        collapsed: list[str] = []
        blank_run = 0
        for line in kept:
            if line.strip():
                blank_run = 0
                collapsed.append(line.rstrip())
            else:
                blank_run += 1
                if blank_run <= 1:
                    collapsed.append("")
        working = "\n".join(collapsed).strip() + ("\n" if collapsed else "")
        audit["unsupported_quantities_removed"] = sorted(removed_tokens)

    working, h2_audit = _canonicalize_h2(working, output_contract)
    audit.update(h2_audit)
    audit["applied"] = working != original
    audit["normalized_answer_sha256"] = sha256(working.encode("utf-8")).hexdigest()
    return working, audit
''',
        encoding="utf-8",
    )


def patch_runtime_attempt() -> None:
    path = MARKET / "v5_runtime.py"
    replace_once(
        path,
        '''    response_provider: str | None
    failure: Mapping[str, Any] | None = None


@dataclass
class RuntimeNodeResult:
''',
        '''    response_provider: str | None
    failure: Mapping[str, Any] | None = None
    raw_answer: str | None = None
    answer_transformations: list[Mapping[str, Any]] = field(default_factory=list)


@dataclass
class RuntimeNodeResult:
''',
    )


def patch_constitutional_runtime() -> None:
    path = MARKET / "v5_constitutional_runtime.py"
    replace_once(
        path,
        '''import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_task_delivery_contract as delivery_contract
''',
        '''import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_task_delivery_contract as delivery_contract
from v5_deterministic_answer_normalization import normalize_answer
''',
    )
    replacement = '''    def _normalize_attempt(
        self,
        node: SelectedNode,
        original_task: str,
        attempt: RuntimeAttempt,
        constraints: TaskConstraints,
    ) -> bool:
        if not attempt.answer:
            return False
        original_answer = attempt.answer
        normalized, audit = normalize_answer(
            original_task,
            original_answer,
            node.output_contract,
            constraints,
        )
        if not audit.get("applied"):
            return False

        attempt.raw_answer = original_answer
        attempt.answer = normalized
        attempt.answer_transformations.append(audit)

        failure_category = ""
        if isinstance(attempt.failure, Mapping):
            failure_category = str(attempt.failure.get("category") or "")
        repairable = attempt.status == "passed" or failure_category == (
            FailureCategory.QUALITY_GATE_FAILED.value
        )
        if not repairable:
            return False

        quality_passed, quality_score, quality_reasons = (
            self.quality_policy.evaluate(node, {}, normalized)
        )
        contract_violations = delivery_contract.validate_answer_contract(
            normalized,
            node.output_contract,
            node.parameter_profile,
        )
        evidence_violations = validate_answer_evidence(
            original_task,
            normalized,
            constraints,
        )
        violations = list(
            dict.fromkeys(
                [*quality_reasons, *contract_violations, *evidence_violations]
            )
        )
        if not quality_passed or violations:
            attempt.gate_reasons = violations
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
                    actual_cost_usd=self._actual_cost({"usage": attempt.usage}),
                    message=";".join(violations),
                ).to_dict()
            return False

        attempt.status = "passed"
        attempt.quality_score = quality_score
        attempt.gate_reasons = []
        attempt.failure = None
        return True

    def _attempt(
        self,
        node: SelectedNode,
        selected_node_id: str,
        kind: str,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[
            [Any, Mapping[str, Any]],
            tuple[Mapping[str, Any], float],
        ],
        budget: BudgetController,
        attempt_index: int,
    ) -> RuntimeAttempt | None:
        attempt = super()._attempt(
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
        if attempt is None or not attempt.answer:
            return attempt

        constraints = compile_task_constraints(original_task)
        if self._normalize_attempt(node, original_task, attempt, constraints):
            return attempt

        contract_violations = delivery_contract.validate_answer_contract(
            attempt.answer,
            node.output_contract,
            node.parameter_profile,
        )
        evidence_violations = validate_answer_evidence(
            original_task,
            attempt.answer,
            constraints,
        )
        violations = list(
            dict.fromkeys([*contract_violations, *evidence_violations])
        )
        if not violations:
            return attempt

        attempt.gate_reasons = list(
            dict.fromkeys([*attempt.gate_reasons, *violations])
        )
        if attempt.status != "passed":
            return attempt

        attempt.status = "quality_gate_failed"
        attempt.failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request_sent=True,
            response_received=True,
            usage_received=bool(attempt.usage),
            actual_cost_usd=self._actual_cost({"usage": attempt.usage}),
            message=";".join(violations),
        ).to_dict()
        return attempt'''
    replace_class_method(path, "ConstitutionalExecutionEngine", "_attempt", replacement)


def write_tests() -> None:
    path = TESTS / "test_v5_deterministic_answer_normalization.py"
    path.write_text(
        '''from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_deterministic_answer_normalization import normalize_answer  # noqa: E402
from v5_runtime import (  # noqa: E402
    ExecutionFailure,
    FailureCategory,
    RuntimeAttempt,
    RuntimeConfig,
)
from v5_task_constraints import (  # noqa: E402
    compile_task_constraints,
    validate_answer_evidence,
)
import v5_task_delivery_contract as delivery_contract  # noqa: E402

TASK = (
    "仅依据题面完成封闭世界决策。方案A一次性投入1200元、每月300元；"
    "方案B一次性投入300元、每月450元；评估期24个月。"
    "题面给定并要求校验：盈亏平衡点为第6个月，方案A总成本8400元，"
    "方案B总成本11100元，差额2700元。不得引入题面外精确数量。"
)
HEADINGS = [
    "assumptions",
    "calculations",
    "conclusions",
    "criteria",
]
CONTRACT = {
    "required_fields": HEADINGS,
    "exact_markdown_headings": HEADINGS,
    "machine_readable_required": False,
}


def node() -> SelectedNode:
    return SelectedNode(
        node_id="node-normalize",
        assigned_work=("work-normalize",),
        professional_capabilities={"analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning"]},
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        output_contract=dict(CONTRACT),
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.001,
        failure_probability=0.02,
        request_config={
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def answer(order=HEADINGS, *, include_bad=True) -> str:
    sections = {
        "assumptions": "仅采用题面成本信息。",
        "calculations": (
            "1200元 + 300元 × 24个月 = 8400元。\\n"
            + ("300元 + 450元 × 24个月 = 10800元 = 11100元。" if include_bad else "300元 + 450元 × 24个月 = 11100元。")
        ),
        "conclusions": "方案A总成本8400元，方案B总成本11100元，差额2700元。",
        "criteria": "以题面总成本比较为准。",
    }
    return "\\n\\n".join(f"## {key}\\n{sections[key]}" for key in order) + "\\n"


def attempt(value: str, category: FailureCategory) -> RuntimeAttempt:
    failure = ExecutionFailure(
        category=category,
        retryable=False,
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        request_sent=True,
        response_received=True,
        message=category.value,
    ).to_dict()
    return RuntimeAttempt(
        attempt_index=1,
        attempt_kind="initial",
        candidate_id="node-normalize",
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        request={},
        status="quality_gate_failed",
        answer=value,
        quality_score=0.3,
        gate_reasons=[category.value],
        latency_seconds=0.1,
        usage={},
        response_id="response-test",
        response_model="openai/test-model",
        response_provider="provider-a",
        failure=failure,
    )


class V5DeterministicAnswerNormalizationTests(unittest.TestCase):
    def test_removes_only_lines_with_unsupported_quantities(self):
        value, audit = normalize_answer(
            TASK,
            answer(include_bad=True),
            CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertTrue(audit["applied"])
        self.assertIn("10800:yuan", audit["unsupported_quantities_removed"])
        self.assertNotIn("10800元", value)
        self.assertIn("1200元 + 300元 × 24个月 = 8400元", value)
        self.assertEqual([], validate_answer_evidence(TASK, value))

    def test_reorders_complete_unique_h2_sections(self):
        value, audit = normalize_answer(
            TASK,
            answer(["assumptions", "calculations", "conclusions", "criteria"], include_bad=False)
                .replace("## conclusions", "## criteria", 1)
                .replace("## criteria", "## conclusions", 1),
            CONTRACT,
            compile_task_constraints(TASK),
        )
        # Build an unambiguous reversed middle pair directly after the string replacement.
        original = answer(["assumptions", "calculations", "criteria", "conclusions"], include_bad=False)
        value, audit = normalize_answer(TASK, original, CONTRACT, compile_task_constraints(TASK))
        self.assertTrue(audit["h2_reordered"])
        observed = [line[3:] for line in value.splitlines() if line.startswith("## ")]
        self.assertEqual(HEADINGS, observed)
        self.assertEqual([], delivery_contract.validate_answer_contract(value, CONTRACT, {}))

    def test_does_not_reorder_when_required_heading_is_missing(self):
        incomplete = answer(["assumptions", "calculations", "conclusions"], include_bad=False)
        value, audit = normalize_answer(TASK, incomplete, CONTRACT, compile_task_constraints(TASK))
        self.assertFalse(audit["h2_reordered"])
        self.assertEqual("heading-set-not-exact", audit["h2_reorder_blocked_reason"])
        self.assertTrue(delivery_contract.validate_answer_contract(value, CONTRACT, {}))

    def test_engine_promotes_only_fully_revalidated_quality_failure(self):
        engine = ConstitutionalExecutionEngine(
            RuntimeConfig(4, 1, 0.01, "value"),
            prompt_policy=SimpleNamespace(),
            retry_policy=SimpleNamespace(),
            recovery_policy=SimpleNamespace(),
            quality_policy=SimpleNamespace(evaluate=lambda *_: (True, 0.91, [])),
            output_policy=SimpleNamespace(schema_version="v5-node-result-1"),
        )
        row = attempt(
            answer(["assumptions", "calculations", "criteria", "conclusions"], include_bad=True),
            FailureCategory.QUALITY_GATE_FAILED,
        )
        repaired = engine._normalize_attempt(node(), TASK, row, compile_task_constraints(TASK))
        self.assertTrue(repaired)
        self.assertEqual("passed", row.status)
        self.assertIsNone(row.failure)
        self.assertIsNotNone(row.raw_answer)
        self.assertEqual(1, len(row.answer_transformations))
        self.assertEqual([], validate_answer_evidence(TASK, row.answer or ""))
        self.assertEqual([], delivery_contract.validate_answer_contract(row.answer or "", CONTRACT, {}))

    def test_engine_never_overrides_non_quality_failure(self):
        engine = ConstitutionalExecutionEngine(
            RuntimeConfig(4, 1, 0.01, "value"),
            prompt_policy=SimpleNamespace(),
            retry_policy=SimpleNamespace(),
            recovery_policy=SimpleNamespace(),
            quality_policy=SimpleNamespace(evaluate=lambda *_: (True, 0.91, [])),
            output_policy=SimpleNamespace(schema_version="v5-node-result-1"),
        )
        row = attempt(answer(include_bad=True), FailureCategory.BUDGET_INSUFFICIENT)
        repaired = engine._normalize_attempt(node(), TASK, row, compile_task_constraints(TASK))
        self.assertFalse(repaired)
        self.assertEqual("quality_gate_failed", row.status)
        self.assertEqual(FailureCategory.BUDGET_INSUFFICIENT.value, row.failure["category"])
        self.assertIsNotNone(row.raw_answer)
        self.assertEqual(1, len(row.answer_transformations))


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def patch_p0() -> None:
    path = ROOT / "tools" / "run_v5_p0_regressions.py"
    replace_once(
        path,
        '''    (TESTS / "test_v5_v4_contract_isolation.py", "V5V4ContractIsolationTests", 7),
)
''',
        '''    (TESTS / "test_v5_v4_contract_isolation.py", "V5V4ContractIsolationTests", 7),
    (
        TESTS / "test_v5_deterministic_answer_normalization.py",
        "V5DeterministicAnswerNormalizationTests",
        5,
    ),
)
''',
    )


def main() -> int:
    write_normalizer()
    patch_runtime_attempt()
    patch_constitutional_runtime()
    write_tests()
    patch_p0()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
