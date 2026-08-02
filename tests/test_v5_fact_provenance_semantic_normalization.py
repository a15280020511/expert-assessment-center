from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_deterministic_answer_normalization import normalize_answer  # noqa: E402
from v5_independent_artifact_revalidation import main as revalidation_main  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    compile_task_constraints,
    fact_claim_supported,
    validate_answer_evidence,
)

TASK = (
    "仅依据题面：某社区夜间临时物资登记点只有1名值守人员。"
    "值守手机剩余46%电量，应急灯剩余63%电量。"
    "东侧出口外地面干燥，但可见散落玻璃碎片；"
    "西侧出口外有不明液体，无法确认来源及是否存在电气风险。"
    "门外有2名无法核验身份、自称设备巡检人员的人要求进入。"
    "纸质登记表显示4件反光背心已领用，但现场只能确认3件。"
    "禁止联网、调用工具或编造外部事实。"
)


class V5FactProvenanceSemanticNormalizationTests(unittest.TestCase):
    def test_quantity_order_and_source_suffix_are_supported(self) -> None:
        claims = (
            "值守人员1名（题面）",
            "手机剩余电量46%（题面）",
            "西侧出口外有不明液体，来源和电气风险未知（题面）",
            "门外有2名自称设备巡检人员，无法核验身份（题面）",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                self.assertTrue(fact_claim_supported(TASK, claim))
        answer = "事实：" + "。 | ".join(claims) + "。\n"
        self.assertEqual([], validate_answer_evidence(TASK, answer))

    def test_same_quantity_with_conflicting_location_is_rejected(self) -> None:
        self.assertFalse(fact_claim_supported(TASK, "门内有2名设备巡检人员"))
        violations = validate_answer_evidence(
            TASK,
            "事实：门内有2名设备巡检人员。\n",
        )
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )

    def test_task_anchored_risk_synthesis_is_relabelled_as_inference(self) -> None:
        answer = (
            "事实：当前存在双侧出口隐患（东侧物理伤害风险、"
            "西侧未知化学/电气风险）、外部未核验人员试图进入、"
            "资产记录缺口、以及通信与照明资源受限。\n"
        )
        normalized, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertIn("推断：当前存在双侧出口隐患", normalized)
        self.assertNotIn("事实：当前存在双侧出口隐患", normalized)
        self.assertEqual(1, len(audit["inferential_fact_labels_relabelled"]))
        self.assertEqual([], validate_answer_evidence(TASK, normalized))
        self.assertFalse(audit["substantive_text_invented"])

    def test_unrelated_external_claim_is_not_relabelled(self) -> None:
        answer = "事实：纽约港当前存在严重航运风险和资产记录缺口。\n"
        normalized, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertEqual(answer, normalized)
        self.assertEqual([], audit["inferential_fact_labels_relabelled"])
        self.assertTrue(validate_answer_evidence(TASK, normalized))

    def test_failed_artifact_always_writes_structured_v3_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "artifact"
            artifact.mkdir()
            archive = root / "artifact.zip"
            archive.write_bytes(b"not-a-real-zip-but-digestible")
            output = root / "independent.json"
            argv = [
                "v5_independent_artifact_revalidation.py",
                "--artifact-dir",
                str(artifact),
                "--expected-sha",
                "a" * 40,
                "--expected-run-id",
                "123",
                "--maximum-calls",
                "5",
                "--maximum-cost-usd",
                "0.35",
                "--archive",
                str(archive),
                "--expected-artifact-digest",
                "deadbeef",
                "--output",
                str(output),
            ]
            with patch.object(sys, "argv", argv):
                exit_code = revalidation_main()
            self.assertEqual(1, exit_code)
            verdict = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                "v5-independent-artifact-revalidation-3",
                verdict["schema_version"],
            )
            self.assertEqual("FAIL", verdict["status"])
            self.assertFalse(verdict["recomputed_from_primitive_evidence"])
            self.assertEqual(
                "FileNotFoundError",
                verdict["revalidation_exception"]["type"],
            )
            self.assertTrue(verdict["failures"])


if __name__ == "__main__":
    unittest.main()
