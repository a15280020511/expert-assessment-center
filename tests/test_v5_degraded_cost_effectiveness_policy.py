from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from publish_report import strict_publication_gate, write_comments
from v5_gpt_expert_selector_policy import MAXIMUM_RECOVERY_CANDIDATES_PER_NODE, parse_proposal
from v5_production_answer_normalization import relabel_task_derived_fact_lines
from v5_quality_status_integrity import enforce_result_integrity
from v5_task_constraints import compile_task_constraints


def proposal(count: int) -> dict[str, object]:
    return {
        "work_items": [{"work_id": "W1", "objective": "compare", "dependencies": [], "required_outputs": ["recommendation"]}],
        "nodes": [{"node_id": "N1", "work_ids": ["W1"], "role": "analyst", "functions": ["compare"], "model": "company/model", "provider": "provider", "reasoning_effort": "medium", "max_output_tokens": 1024, "recovery": [{"model": f"company{i}/model", "provider": f"provider{i}"} for i in range(count)]}],
        "edges": [],
        "final_nodes": ["N1"],
    }


class DegradedPolicyTests(unittest.TestCase):
    def test_default_and_explicit_denial(self) -> None:
        self.assertTrue(compile_task_constraints("分析并建议").allow_degraded_success)
        self.assertFalse(compile_task_constraints("不得降级交付").allow_degraded_success)

    def test_single_node_three_recoveries(self) -> None:
        self.assertEqual(3, len(parse_proposal(json.dumps(proposal(3)))["nodes"][0]["recovery"]))
        with self.assertRaisesRegex(RuntimeError, "node recovery"):
            parse_proposal(json.dumps(proposal(MAXIMUM_RECOVERY_CANDIDATES_PER_NODE + 1)))

    def test_failed_noncritical_node_is_disclosed(self) -> None:
        result = {"status": "success", "completion_mode": "degraded", "quality_status": "degraded_success", "final_answer": "usable", "delivery_policy": {"allow_degraded_success": True, "blockers": [], "missing_non_degradable_work_ids": []}, "work_coverage": {"coverage_ratio": 0.75, "minimum_degraded_coverage": 2 / 3, "successful_content_nodes": 1}, "node_results": [{"node_id": "N1", "status": "success", "contract": {"required_fields_complete": True}}, {"node_id": "N2", "status": "failed", "contract": {"required_fields_complete": False}}]}
        normalized = enforce_result_integrity(result)
        self.assertEqual("DEGRADED", normalized["quality_integrity"]["status"])
        self.assertEqual(["N2"], normalized["quality_integrity"]["failed_node_ids"])

    def test_normative_fact_label_is_repaired(self) -> None:
        normalized, audit = relabel_task_derived_fact_lines("用户优先稳定、低投入；不得立即辞职。", "- 事实：用户优先稳定、低投入，因此不得立即辞职。\n")
        self.assertIn("推断：", normalized)
        self.assertTrue(audit["applied"])

    def test_degraded_publication_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = {"status": "success", "completion_mode": "degraded", "quality_status": "degraded_success", "quality_integrity": {"status": "DEGRADED"}, "delivery_policy": {"allow_degraded_success": True, "blockers": [], "missing_non_degradable_work_ids": []}, "work_coverage": {"coverage_ratio": 0.75, "minimum_degraded_coverage": 2 / 3, "successful_content_nodes": 1}}
            (root / "v5-execution-summary.json").write_text(json.dumps(summary), encoding="utf-8")
            (root / "expert-team-result.json").write_text(json.dumps(summary), encoding="utf-8")
            (root / "v5-node-results.json").write_text(json.dumps([{ "node_id": "N1", "status": "success", "contract": {"required_fields_complete": True}}, {"node_id": "N2", "status": "failed", "contract": {"required_fields_complete": False}}]), encoding="utf-8")
            allowed, blockers = strict_publication_gate(root)
            self.assertTrue(allowed, blockers)
            report = root / "report.md"
            report.write_text("usable audited degraded report", encoding="utf-8")
            manifest = write_comments(report, root / "comments", run_url="https://github.com/o/r/actions/runs/123", max_chars=5000, delivery_status="degraded_success")
            self.assertEqual("prepared_degraded_success", manifest["publication_status"])


if __name__ == "__main__":
    unittest.main()
