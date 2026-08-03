from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_independent_artifact_revalidation as independent  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402
from v5_task_envelope import work_output_contract  # noqa: E402


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
TRAILING_REQUIREMENT_TASK = (
    "严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空；不得调用外部工具。"
)
NUMBERED_TRAILING_REQUIREMENT_TASK = (
    TASK
    + "不得出现任何其他Markdown二级标题，每个指定标题下必须有非空正文。"
)
TRAILING_REQUIREMENT_HEADINGS = [
    "题面事实",
    "计算与校验",
    "推断与未知",
    "结论与反转条件",
]
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


def final_contract(task: str) -> dict:
    return work_output_contract(
        task,
        INTERNAL_HEADINGS,
        final_node=True,
    )


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
        self.assertEqual(contract["task_explicit_delivery_section_count"], 8)
        self.assertTrue(contract["markdown_heading_order_required"])

    def test_trailing_requirements_are_not_absorbed_into_last_heading(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(
            TRAILING_REQUIREMENT_TASK
        )
        self.assertEqual(
            contract["exact_markdown_headings"],
            TRAILING_REQUIREMENT_HEADINGS,
        )
        self.assertEqual(contract["task_explicit_delivery_section_count"], 4)

    def test_numbered_list_with_trailing_requirements_strips_enumerators(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(
            NUMBERED_TRAILING_REQUIREMENT_TASK
        )
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertEqual(contract["task_explicit_delivery_section_count"], 8)
        synthesis = final_contract(NUMBERED_TRAILING_REQUIREMENT_TASK)
        self.assertEqual(synthesis["required_fields"], HEADINGS)

    def test_final_contract_replaces_internal_generic_headings(self) -> None:
        contract = final_contract(TASK)
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
        contract = final_contract(TASK)
        self.assertEqual(
            independent._final_contract_violations(
                graph(contract),
                report(HEADINGS),
                TASK,
            ),
            [],
        )

    def test_wrong_order_is_rejected_from_original_task(self) -> None:
        contract = final_contract(TASK)
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
