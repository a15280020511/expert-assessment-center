from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_deterministic_answer_normalization as normalization  # noqa: E402
import v5_task_delivery_contract as delivery_contract  # noqa: E402
from v5_generic_heading_typo_normalization import (  # noqa: E402
    install_generic_heading_typo_normalization,
)
from v5_task_constraints import compile_task_constraints  # noqa: E402

TASK = "比较四类工作，给出排序、决策表、适用边界和不确定性。"
GENERIC_FIELDS = ["核心判断", "方案比较", "决策表", "适用边界", "不确定性与反例"]
GENERIC_CONTRACT = {
    "required_fields": GENERIC_FIELDS,
    "machine_readable_required": False,
    "explicit_markdown_contract": False,
    "explicit_user_contract": False,
}


def render(last_heading: str = "不确定性与反例") -> str:
    bodies = {
        "核心判断": "给出总体排序。",
        "方案比较": "比较收入、稳定性和风险。",
        "决策表": "|情形|建议|\n|---|---|\n|总体|保安与快递优先比较|",
        "适用边界": "已有车辆时网约车条件会变化。",
        last_heading: "缺乏2026年实时薪资，因此结论需条件化理解。",
    }
    headings = ["核心判断", "方案比较", "决策表", "适用边界", last_heading]
    return "\n\n".join(f"## {heading}\n{bodies[heading]}" for heading in headings) + "\n"


class GenericHeadingTypoNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        install_generic_heading_typo_normalization()

    def test_run416_single_character_generic_typo_is_canonicalized_before_exact_gate(self) -> None:
        value, audit = normalization.normalize_answer(
            TASK,
            render("不确定性与反流"),
            GENERIC_CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertTrue(audit["generic_h2_typo_repair_applied"])
        self.assertEqual(
            [{"observed": "不确定性与反流", "canonical": "不确定性与反例", "edit_distance": 1}],
            audit["generic_h2_typo_corrections"],
        )
        self.assertIn("## 不确定性与反例", value)
        self.assertNotIn("## 不确定性与反流", value)
        self.assertFalse(audit["explicit_user_markdown_contract_relaxed"])
        self.assertFalse(audit["substantive_text_invented"])
        self.assertEqual(
            [],
            delivery_contract.validate_answer_contract(value, GENERIC_CONTRACT, {}),
        )

    def test_explicit_user_exact_markdown_contract_remains_strict(self) -> None:
        explicit = {
            **GENERIC_CONTRACT,
            "explicit_markdown_contract": True,
            "explicit_user_contract": True,
            "exact_markdown_headings": list(GENERIC_FIELDS),
        }
        value, audit = normalization.normalize_answer(
            TASK,
            render("不确定性与反流"),
            explicit,
            compile_task_constraints(TASK),
        )
        self.assertFalse(audit.get("generic_h2_typo_repair_applied", False))
        self.assertIn("## 不确定性与反流", value)
        self.assertTrue(
            delivery_contract.validate_answer_contract(value, explicit, {})
        )

    def test_two_character_generic_typo_is_not_repaired(self) -> None:
        value, audit = normalization.normalize_answer(
            TASK,
            render("不确定性与回流"),
            GENERIC_CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertFalse(audit.get("generic_h2_typo_repair_applied", False))
        self.assertIn("## 不确定性与回流", value)
        self.assertTrue(
            delivery_contract.validate_answer_contract(value, GENERIC_CONTRACT, {})
        )

    def test_ambiguous_one_character_match_is_not_repaired(self) -> None:
        contract = {
            "required_fields": ["abcd", "abce"],
            "machine_readable_required": False,
            "explicit_markdown_contract": False,
            "explicit_user_contract": False,
        }
        answer = "## abcf\n第一节。\n\n## abce\n第二节。\n"
        value, audit = normalization.normalize_answer(
            "比较两个部分。",
            answer,
            contract,
            compile_task_constraints("比较两个部分。"),
        )
        self.assertFalse(audit.get("generic_h2_typo_repair_applied", False))
        self.assertIn("## abcf", value)
        self.assertTrue(delivery_contract.validate_answer_contract(value, contract, {}))

    def test_empty_section_is_never_repaired(self) -> None:
        answer = (
            "## 核心判断\n结论。\n\n"
            "## 方案比较\n比较。\n\n"
            "## 决策表\n表格。\n\n"
            "## 适用边界\n边界。\n\n"
            "## 不确定性与反流\n"
        )
        value, audit = normalization.normalize_answer(
            TASK,
            answer,
            GENERIC_CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertFalse(audit.get("generic_h2_typo_repair_applied", False))
        self.assertIn("## 不确定性与反流", value)


if __name__ == "__main__":
    unittest.main()
