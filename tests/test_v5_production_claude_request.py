from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_claude_red_team_policy import parse_claude_red_team_advice  # noqa: E402
from v5_production_claude_request import (  # noqa: E402
    build_claude_red_team_request,
    exact_review_targets,
)
from v5_structured_output_compat import (  # noqa: E402
    normalize_strict_response_format,
)


def payload() -> dict[str, object]:
    task = "仅依据题面比较三个方案并给出条件化结论。"
    return {
        "task_digest": "a" * 64,
        "proposal_digest": "b" * 64,
        "approved_total_calls": 10,
        "governance_calls_reserved": 3,
        "approved_recovery_calls": 3,
        "cost_anomaly_usd": 1.0,
        "task_excerpt": task,
        "task_characters": len(task),
        "task_truncated": False,
        "task_constraints": {
            "external_tools_allowed": False,
            "external_facts_allowed": False,
            "fail_closed": True,
        },
        "explicit_delivery_contract": {
            "required_fields": ["结论"],
            "must_separate_fact_assumption_inference": True,
        },
        "work_items": [
            {
                "work_id": "W1",
                "objective": "比较成本",
                "dependencies": [],
                "required_outputs": ["成本排序"],
            },
            {
                "work_id": "W2",
                "objective": "形成结论",
                "dependencies": ["W1"],
                "required_outputs": ["条件化建议"],
            },
        ],
        "nodes": [
            {
                "node_id": "N1",
                "candidate_id": "company-a/model-a@provider-a",
                "work_ids": ["W1"],
                "role": "成本分析",
                "functions": ["比较"],
                "model": "company-a/model-a",
                "company": "company-a",
                "provider": "provider-a",
                "estimated_cost_usd": 0.01,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidates": [
                    {
                        "candidate_id": "company-c/model-c@provider-c",
                        "model": "company-c/model-c",
                        "company": "company-c",
                        "provider": "provider-c",
                        "estimated_cost_usd": 0.01,
                    }
                ],
            },
            {
                "node_id": "N2",
                "candidate_id": "company-b/model-b@provider-b",
                "work_ids": ["W2"],
                "role": "结论综合",
                "functions": ["综合"],
                "model": "company-b/model-b",
                "company": "company-b",
                "provider": "provider-b",
                "estimated_cost_usd": 0.01,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidates": [
                    {
                        "candidate_id": "company-d/model-d@provider-d",
                        "model": "company-d/model-d",
                        "company": "company-d",
                        "provider": "provider-d",
                        "estimated_cost_usd": 0.01,
                    },
                    {
                        "candidate_id": "company-e/model-e@provider-e",
                        "model": "company-e/model-e",
                        "company": "company-e",
                        "provider": "provider-e",
                        "estimated_cost_usd": 0.01,
                    },
                ],
            },
        ],
        "edges": [
            {
                "source": "N1",
                "target": "N2",
                "relation_type": "synthesis",
            }
        ],
        "final_nodes": ["N2"],
    }


class ProductionClaudeRequestTests(unittest.TestCase):
    def test_exact_targets_are_compiled_from_reviewed_objects(self) -> None:
        self.assertEqual(
            (
                "contract",
                "edge:N1->N2",
                "node:N1",
                "node:N2",
                "task",
                "work:W1",
                "work:W2",
            ),
            exact_review_targets(payload()),
        )

    def test_provider_schema_uses_exact_enum_not_removable_pattern(self) -> None:
        request = build_claude_red_team_request(payload())
        target = request["response_format"]["json_schema"]["schema"][
            "properties"
        ]["suggestions"]["items"]["properties"]["target"]
        self.assertEqual(list(exact_review_targets(payload())), target["enum"])
        self.assertNotIn("pattern", target)
        self.assertNotIn("minLength", target)
        self.assertNotIn("maxLength", target)
        self.assertEqual(
            "task-specific-exact-enum",
            request["red_team_policy"]["target_constraint"],
        )

    def test_structured_output_compatibility_preserves_target_enum(self) -> None:
        request = build_claude_red_team_request(payload())
        normalized, receipt = normalize_strict_response_format(
            request["response_format"]
        )
        target = normalized["json_schema"]["schema"]["properties"][
            "suggestions"
        ]["items"]["properties"]["target"]
        self.assertEqual(list(exact_review_targets(payload())), target["enum"])
        self.assertEqual("PASS", receipt["status"])

    def test_valid_live_target_parses_and_invalid_target_still_fails_closed(self) -> None:
        valid = parse_claude_red_team_advice(
            json.dumps(
                {
                    "suggestions": [
                        {
                            "code": "WORK_UNCOVERED",
                            "target": "work:W2",
                            "change": "为W2明确最终交付责任。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual("work:W2", valid["suggestions"][0]["target"])
        with self.assertRaisesRegex(ValueError, "exact review target"):
            parse_claude_red_team_advice(
                json.dumps(
                    {
                        "suggestions": [
                            {
                                "code": "WORK_UNCOVERED",
                                "target": "nodes[1]",
                                "change": "明确责任。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    def test_fixed_prompt_and_provider_lock_are_unchanged(self) -> None:
        request = build_claude_red_team_request(payload())
        self.assertFalse(request["provider"]["allow_fallbacks"])
        self.assertEqual(["anthropic"], request["provider"]["only"])
        self.assertEqual("~anthropic/claude-opus-latest", request["model"])
        self.assertEqual(2, len(request["messages"]))
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)


if __name__ == "__main__":
    unittest.main()
