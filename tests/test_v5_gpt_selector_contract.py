from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_gpt_expert_selector import GPTSelectorError, parse_proposal  # noqa: E402


def valid_proposal() -> dict[str, object]:
    return {
        "work_items": [
            {
                "work_id": "work-1",
                "objective": "比较两个给定方案",
                "dependencies": [],
                "required_outputs": ["唯一建议"],
            }
        ],
        "nodes": [
            {
                "node_id": "node-1",
                "work_ids": ["work-1"],
                "role": "方案比较专家",
                "functions": ["对比月费与流量", "形成唯一建议"],
                "model": "company/model",
                "provider": "provider-a",
                "reasoning_effort": "low",
                "max_output_tokens": 512,
                "recovery": [],
            }
        ],
        "edges": [],
        "final_nodes": ["node-1"],
    }


class GPTSelectorContractTests(unittest.TestCase):
    def test_unicode_descriptions_are_allowed_but_ids_stay_identifiers(self) -> None:
        proposal = valid_proposal()
        parsed = parse_proposal(json.dumps(proposal, ensure_ascii=False))
        self.assertEqual("方案比较专家", parsed["nodes"][0]["role"])
        self.assertEqual("work-1", parsed["work_items"][0]["work_id"])

    def test_empty_descriptive_functions_are_valid(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"][0]["functions"] = []
        parsed = parse_proposal(json.dumps(proposal, ensure_ascii=False))
        self.assertEqual([], parsed["nodes"][0]["functions"])

    def test_unicode_internal_id_is_rejected_before_claude_boundary(self) -> None:
        proposal = valid_proposal()
        proposal["work_items"][0]["work_id"] = "工作一"
        proposal["nodes"][0]["work_ids"] = ["工作一"]
        with self.assertRaisesRegex(GPTSelectorError, "bounded identifier"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))

    def test_unicode_model_identifier_is_rejected_before_materialization(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"][0]["model"] = "公司/模型"
        with self.assertRaisesRegex(GPTSelectorError, "bounded identifier"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))

    def test_unknown_relation_type_is_rejected_by_parser(self) -> None:
        proposal = valid_proposal()
        proposal["nodes"].append(copy.deepcopy(proposal["nodes"][0]))
        proposal["nodes"][1]["node_id"] = "node-2"
        proposal["edges"] = [
            {
                "source": "node-1",
                "target": "node-2",
                "relation_type": "invented",
            }
        ]
        proposal["final_nodes"] = ["node-2"]
        with self.assertRaisesRegex(GPTSelectorError, "relation_type"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))

    def test_duplicate_required_outputs_are_rejected_like_schema(self) -> None:
        proposal = valid_proposal()
        proposal["work_items"][0]["required_outputs"] = ["建议", "建议"]
        with self.assertRaisesRegex(GPTSelectorError, "required_outputs contain duplicates"):
            parse_proposal(json.dumps(proposal, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
