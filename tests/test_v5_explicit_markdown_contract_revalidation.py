from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import task_semantic_compiler as compiler  # noqa: E402
import v5_independent_artifact_revalidation as independent  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402


HEADINGS = [
    "已知事实与未知",
    "风险优先级",
    "前15分钟行动",
    "通信降级",
    "门禁与人员清点",
    "资源分配",
    "失败模式与红队",
    "最终条件式建议",
]
TASK = (
    "真实闭卷实战。最终输出必须严格使用以下8个Markdown二级标题且顺序不得改变："
    "1. 已知事实与未知；2. 风险优先级；3. 前15分钟行动；4. 通信降级；"
    "5. 门禁与人员清点；6. 资源分配；7. 失败模式与红队；8. 最终条件式建议。"
)
INTERNAL_HEADINGS = [
    "agreements",
    "assumptions",
    "conclusions",
    "conflict_resolution",
    "disagreements",
    "evidence_gaps",
    "final_recommendation",
    "uncertainties",
]


def report(headings: list[str]) -> str:
    return "\n\n".join(
        f"## {heading}\n\n{index}号章节正文。"
        for index, heading in enumerate(headings, 1)
    )


def graph(contract: dict) -> dict:
    return {
        "final_nodes": ["final"],
        "nodes": [
            {
                "node_id": "final",
                "output_contract": contract,
                "parameter_profile": {},
            }
        ],
    }


class V5ExplicitMarkdownContractRevalidationTests(unittest.TestCase):
    def test_production_wording_extracts_exact_semicolon_numbered_headings(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(TASK)
        self.assertTrue(contract["explicit_markdown_contract"])
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertTrue(contract["markdown_heading_order_required"])

    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        self.assertEqual(contract["required_fields"], HEADINGS)
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertNotIn("agreements", contract["required_fields"])

    def test_internal_standard_report_is_rejected_by_recompiled_task_contract(self) -> None:
        internal_contract = {
            "required_fields": INTERNAL_HEADINGS,
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        }
        violations = independent._final_contract_violations(
            graph(internal_contract),
            report(INTERNAL_HEADINGS),
            TASK,
        )
        self.assertIn(
            "final-graph-contract-kind-mismatch:exact-markdown:generic",
            violations,
        )
        self.assertIn(
            "final-graph-contract-differs-from-task-recompilation",
            violations,
        )
        self.assertTrue(
            any(
                value.startswith(
                    "task-recompiled-final-report-contract:missing-exact-markdown-heading:"
                )
                for value in violations
            )
        )

    def test_exact_report_and_graph_contract_pass_independent_revalidation(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        self.assertEqual(
            independent._final_contract_violations(
                graph(contract),
                report(HEADINGS),
                TASK,
            ),
            [],
        )

    def test_wrong_order_is_rejected_from_original_task(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        violations = independent._final_contract_violations(
            graph(contract),
            report(list(reversed(HEADINGS))),
            TASK,
        )
        self.assertIn(
            "task-recompiled-final-report-contract:exact-markdown-heading-order-mismatch",
            violations,
        )


if __name__ == "__main__":
    unittest.main()
