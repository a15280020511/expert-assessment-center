from __future__ import annotations

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
            "1200元 + 300元 × 24个月 = 8400元。\n"
            + ("300元 + 450元 × 24个月 = 10800元 = 11100元。" if include_bad else "300元 + 450元 × 24个月 = 11100元。")
        ),
        "conclusions": "方案A总成本8400元，方案B总成本11100元，差额2700元。",
        "criteria": "以题面总成本比较为准。",
    }
    return "\n\n".join(f"## {key}\n{sections[key]}" for key in order) + "\n"


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
            RuntimeConfig(4, 1, 0.01),
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
            RuntimeConfig(4, 1, 0.01),
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
