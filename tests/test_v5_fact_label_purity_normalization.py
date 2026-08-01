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
from v5_task_constraints import compile_task_constraints, validate_answer_evidence  # noqa: E402

TASK = (
    "仅依据题面：西门存在不明液体且电气风险未知；"
    "东门地面干燥但门锁卡滞；来访者身份无法核验。"
    "禁止接触不明液体、强行开门或放行无法核验身份的来访者。"
)
ANSWER = (
    "## conclusions\n"
    "- 事实：西门存在不明液体且电气风险未知，必须否决该路线。\n"
    "- 事实：东门地面干燥但门锁卡滞，禁止强行开门，需采取非破坏性替代方案。\n"
    "- 事实：来访者身份无法核验，禁止放行。\n"
)
CONTRACT = {
    "required_fields": ["conclusions"],
    "exact_markdown_headings": ["conclusions"],
    "machine_readable_required": False,
}


def node() -> SelectedNode:
    return SelectedNode(
        node_id="node-fact-purity",
        assigned_work=("work-fact-purity",),
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
        request_config={"provider": {"order": ["provider-a"], "only": ["provider-a"], "allow_fallbacks": False, "require_parameters": True}},
    )


class V5FactLabelPurityNormalizationTests(unittest.TestCase):
    def test_splits_production_mixed_fact_and_normative_lines(self) -> None:
        value, audit = normalize_answer(
            TASK,
            ANSWER,
            CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertEqual(3, audit["structural_labels_inserted"])
        self.assertEqual(3, len(audit["mixed_fact_labels_split"]))
        self.assertIn("事实：西门存在不明液体且电气风险未知。", value)
        self.assertIn("结论：必须否决该路线。", value)
        self.assertIn("事实：东门地面干燥但门锁卡滞。", value)
        self.assertIn("结论：禁止强行开门，需采取非破坏性替代方案。", value)
        self.assertIn("事实：来访者身份无法核验。", value)
        self.assertIn("结论：禁止放行。", value)
        self.assertEqual([], validate_answer_evidence(TASK, value))
        self.assertFalse(audit["substantive_text_invented"])

    def test_pure_fact_line_is_unchanged(self) -> None:
        answer = "事实：来访者身份无法核验。\n"
        value, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertEqual(answer, value)
        self.assertEqual(0, audit["structural_labels_inserted"])

    def test_engine_promotes_only_after_full_revalidation(self) -> None:
        engine = ConstitutionalExecutionEngine(
            RuntimeConfig(5, 1, 0.35, "value"),
            prompt_policy=SimpleNamespace(),
            retry_policy=SimpleNamespace(),
            recovery_policy=SimpleNamespace(),
            quality_policy=SimpleNamespace(evaluate=lambda *_: (True, 0.93, [])),
            output_policy=SimpleNamespace(schema_version="v5-node-result-1"),
        )
        failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model="openai/test-model",
            provider_endpoint="openai/test-model@provider-a",
            request_sent=True,
            response_received=True,
            message="unsupported-fact-label",
        ).to_dict()
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="node-fact-purity",
            model="openai/test-model",
            provider_endpoint="openai/test-model@provider-a",
            request={},
            status="quality_gate_failed",
            answer=ANSWER,
            quality_score=1.0,
            gate_reasons=["unsupported-fact-label"],
            latency_seconds=0.1,
            usage={},
            response_id="response-test",
            response_model="openai/test-model",
            response_provider="provider-a",
            failure=failure,
        )
        self.assertTrue(
            engine._normalize_attempt(
                node(), TASK, attempt, compile_task_constraints(TASK)
            )
        )
        self.assertEqual("passed", attempt.status)
        self.assertIsNone(attempt.failure)
        self.assertEqual([], validate_answer_evidence(TASK, attempt.answer or ""))


if __name__ == "__main__":
    unittest.main()
