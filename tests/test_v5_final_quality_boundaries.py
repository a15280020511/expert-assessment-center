from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

import v5_price_ranked_pipeline_legacy as pipeline_legacy  # noqa: E402
from v5_final_audit_hardening import (  # noqa: E402
    install_final_request_audit_hardening,
)
from v5_final_semantic_gate import (  # noqa: E402
    _final_attempt_obligation_failure,
    work_product_evidence_validator,
)
from v5_run387_hardening import HeterogeneousEvidenceExecutionEngine  # noqa: E402
from v5_runtime import RuntimeAttempt  # noqa: E402


TASK = (
    "A/B两方案。A成本100元，B成本200元。请计算总成本，给出关键临界值，"
    "并说明±50%误差情景。"
)
EMPTY_FINAL = """## 核心判断
总成本：
临界值：
±50%误差：
## 关键依据
题面。
## 不确定性与反例
无。
## 可执行结论
推荐A。
"""


class FinalQualityBoundaryTests(unittest.TestCase):
    @staticmethod
    def _attempt(answer: str) -> RuntimeAttempt:
        return RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="c1",
            model="openai/test-model",
            provider_endpoint="openai/test-model@openrouter-auto",
            request={"model": "openai/test-model"},
            status="passed",
            answer=answer,
            quality_score=1.0,
            gate_reasons=[],
            latency_seconds=0.1,
            usage={},
            response_id="r1",
            response_model="openai/test-model",
            response_provider="provider",
            failure=None,
        )

    def test_whole_task_obligations_do_not_gate_internal_work_product(self) -> None:
        violations = work_product_evidence_validator(TASK, "内部分析：A可能更便宜。")
        self.assertFalse(
            any(value.startswith("missing-task-obligation:") for value in violations)
        )

    def test_final_delivery_empty_obligations_are_hard_failure(self) -> None:
        engine = object.__new__(HeterogeneousEvidenceExecutionEngine)
        node = SimpleNamespace(
            model="openai/test-model",
            provider_endpoint="openai/test-model@openrouter-auto",
            output_contract={"final_delivery_node": True},
        )
        attempt = self._attempt(EMPTY_FINAL)
        result = _final_attempt_obligation_failure(engine, node, TASK, attempt)
        self.assertIsNotNone(result)
        self.assertEqual("quality_gate_failed", result.status)
        self.assertTrue(
            any(value.startswith("missing-task-obligation:") for value in result.gate_reasons)
        )

    def test_internal_delivery_is_not_forced_to_answer_entire_task(self) -> None:
        engine = object.__new__(HeterogeneousEvidenceExecutionEngine)
        node = SimpleNamespace(
            model="openai/test-model",
            provider_endpoint="openai/test-model@openrouter-auto",
            output_contract={"final_delivery_node": False},
        )
        attempt = self._attempt("内部分析：A可能更便宜。")
        result = _final_attempt_obligation_failure(engine, node, TASK, attempt)
        self.assertEqual("passed", result.status)
        self.assertEqual([], result.gate_reasons)

    def test_runtime_knob_coverage_is_written_after_ordinary_final_audit(self) -> None:
        original = pipeline_legacy._request_audit
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def ordinary_writer(output: Path, *, approved_total_calls: int) -> None:
                del approved_total_calls
                (Path(output) / "v5-request-audit.json").write_text(
                    json.dumps({"status": "PASS", "ordinary_writer": True}),
                    encoding="utf-8",
                )

            def coverage_writer(output: Path, integrity_status: str) -> None:
                del integrity_status
                path = Path(output) / "v5-request-audit.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["runtime_knob_coverage_status"] = "PASS"
                payload["runtime_knob_coverage"] = {"status": "PASS"}
                path.write_text(json.dumps(payload), encoding="utf-8")

            try:
                pipeline_legacy._request_audit = ordinary_writer
                with patch(
                    "v5_final_audit_hardening.quality_integrity._rewrite_request_audit",
                    side_effect=coverage_writer,
                ):
                    install_final_request_audit_hardening()
                    pipeline_legacy._request_audit(
                        root,
                        approved_total_calls=8,
                    )
                payload = json.loads(
                    (root / "v5-request-audit.json").read_text(encoding="utf-8")
                )
                self.assertTrue(payload["ordinary_writer"])
                self.assertEqual("PASS", payload["runtime_knob_coverage_status"])
            finally:
                pipeline_legacy._request_audit = original


if __name__ == "__main__":
    unittest.main()
