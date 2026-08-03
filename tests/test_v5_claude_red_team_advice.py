from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from v5_claude_red_team_policy import (  # noqa: E402
    CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
    CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK,
    CLAUDE_RED_TEAM_MODEL,
    GPT_SYNTHESIS_CALLS,
    RED_TEAM_SCOPE,
    build_claude_red_team_request,
    fixed_prompt,
    forbidden_claude_capabilities,
    parse_claude_red_team_advice,
)
from v5_proposal_materializer import _bounded_task_excerpt  # noqa: E402


def unified_payload() -> dict[str, object]:
    task = "仅依据题面：南门外有2名身份未核验人员。比较方案并给出完整结论。"
    return {
        "task_digest": "a" * 64,
        "proposal_digest": "b" * 64,
        "approved_total_calls": 8,
        "governance_calls_reserved": 3,
        "approved_recovery_calls": 1,
        "cost_anomaly_usd": 0.5,
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
                "work_id": "work-1",
                "objective": "Analyze the bounded decision problem",
                "dependencies": [],
                "required_outputs": ["analysis"],
            },
            {
                "work_id": "work-2",
                "objective": "Synthesize the final decision",
                "dependencies": ["work-1"],
                "required_outputs": ["recommendation"],
            },
        ],
        "nodes": [
            {
                "node_id": "node-1",
                "candidate_id": "model-a@provider-a",
                "work_ids": ["work-1", "work-2"],
                "role": "Decision analyst and synthesizer",
                "functions": ["analysis", "synthesis"],
                "model": "company-a/model-a",
                "company": "company-a",
                "provider": "provider-a",
                "estimated_cost_usd": 0.01,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidates": [],
            }
        ],
        "edges": [],
        "final_nodes": ["node-1"],
    }


class ClaudeRedTeamAdviceTests(unittest.TestCase):
    def test_request_uses_latest_and_one_unified_advice_schema(self) -> None:
        request = build_claude_red_team_request(unified_payload())
        self.assertEqual("~anthropic/claude-opus-latest", CLAUDE_RED_TEAM_MODEL)
        self.assertEqual(CLAUDE_RED_TEAM_MODEL, request["model"])
        user = json.loads(request["messages"][1]["content"])
        self.assertEqual(RED_TEAM_SCOPE, user["scope"])
        self.assertIn("task_excerpt", user["payload"])
        self.assertIn("nodes", user["payload"])
        self.assertEqual(["node-1"], user["payload"]["final_nodes"])
        self.assertEqual([], user["payload"]["nodes"][0]["recovery_candidates"])
        schema = request["response_format"]["json_schema"]["schema"]
        self.assertEqual(["suggestions"], schema["required"])
        self.assertNotIn("decision", schema["properties"])
        self.assertNotIn("APPROVE", json.dumps(schema))
        self.assertNotIn("REJECT", json.dumps(schema))
        self.assertFalse(request["provider"]["allow_fallbacks"])
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)

    def test_unicode_function_descriptions_are_valid_and_bounded(self) -> None:
        payload = unified_payload()
        payload["nodes"][0]["functions"] = ["对比月费与流量", "形成唯一建议"]
        request = build_claude_red_team_request(payload)
        user = json.loads(request["messages"][1]["content"])
        self.assertEqual(
            ["对比月费与流量", "形成唯一建议"],
            user["payload"]["nodes"][0]["functions"],
        )

    def test_duplicate_function_descriptions_are_rejected(self) -> None:
        payload = unified_payload()
        payload["nodes"][0]["functions"] = ["分析", "分析"]
        with self.assertRaisesRegex(ValueError, "functions contains duplicates"):
            build_claude_red_team_request(payload)

    def test_unicode_advisory_target_is_valid_and_normalized(self) -> None:
        result = parse_claude_red_team_advice(
            json.dumps(
                {
                    "suggestions": [
                        {
                            "code": "CONTRACT_MISMATCH",
                            "target": "contract",
                            "change": "给出唯一推荐并列出两条题面内理由。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual("contract", result["suggestions"][0]["target"])

    def test_advisory_target_control_character_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "target contains control characters"):
            parse_claude_red_team_advice(
                json.dumps(
                    {
                        "suggestions": [
                            {
                                "code": "CONTRACT_MISMATCH",
                                "target": "node:final\u0001section",
                                "change": "修复交付合同。",
                            }
                        ]
                    }
                )
            )

    def test_empty_advice_is_valid_and_not_approval(self) -> None:
        result = parse_claude_red_team_advice('{"suggestions":[]}')
        self.assertEqual([], result["suggestions"])
        self.assertFalse(result["hard_gate"])
        self.assertFalse(result["approval_authority"])
        self.assertTrue(result["covers_internal_selection"])
        self.assertTrue(result["covers_external_information"])

    def test_internal_and_information_modifications_share_one_schema(self) -> None:
        result = parse_claude_red_team_advice(
            json.dumps(
                {
                    "suggestions": [
                        {
                            "code": "WORK_UNCOVERED",
                            "target": "work:work-2",
                            "change": "为work-2增加明确负责节点并补齐依赖边。",
                        },
                        {
                            "code": "LOCATION_CONFLICT",
                            "target": "task",
                            "change": "保留南门外位置限定，不得泛化为现场。",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        )
        self.assertEqual(2, len(result["suggestions"]))

    def test_prompt_is_exact_nonduplicative_and_soft_resource_safe(self) -> None:
        prompt = fixed_prompt()
        self.assertIn("一条suggestion只处理一个缺陷", prompt)
        self.assertIn("不得重述已满足条件", prompt)
        self.assertIn("只返回REVIEW_INPUT_INCOMPLETE", prompt)
        self.assertIn("不得因费用或Token建议值", prompt)

    def test_complete_task_is_preserved_without_local_truncation(self) -> None:
        task = "甲" * 120_000
        excerpt, truncated = _bounded_task_excerpt(task)
        self.assertFalse(truncated)
        self.assertEqual(task, excerpt)
        payload = unified_payload()
        payload["task_excerpt"] = excerpt
        payload["task_characters"] = len(task)
        payload["task_truncated"] = False
        request = build_claude_red_team_request(payload)
        user = json.loads(request["messages"][1]["content"])
        self.assertFalse(user["payload"]["task_truncated"])
        self.assertEqual(task, user["payload"]["task_excerpt"])

    def test_recovery_details_and_edge_relation_are_reviewable(self) -> None:
        payload = unified_payload()
        payload["nodes"][0]["recovery_candidates"] = [
            {
                "candidate_id": "company-b/model-b@provider-b",
                "model": "company-b/model-b",
                "company": "company-b",
                "provider": "provider-b",
                "estimated_cost_usd": 0.02,
            }
        ]
        payload["nodes"].append(
            {
                "node_id": "node-2",
                "candidate_id": "company-c/model-c@provider-c",
                "work_ids": ["work-2"],
                "role": "Final decision synthesizer",
                "functions": ["synthesis"],
                "model": "company-c/model-c",
                "company": "company-c",
                "provider": "provider-c",
                "estimated_cost_usd": 0.03,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidates": [],
            }
        )
        payload["edges"] = [
            {
                "source": "node-1",
                "target": "node-2",
                "relation_type": "synthesis",
            }
        ]
        payload["final_nodes"] = ["node-2"]
        request = build_claude_red_team_request(payload)
        user = json.loads(request["messages"][1]["content"])["payload"]
        self.assertEqual("company-b", user["nodes"][0]["recovery_candidates"][0]["company"] )
        self.assertEqual("synthesis", user["edges"][0]["relation_type"])
        self.assertEqual(["node-2"], user["final_nodes"])

    def test_non_exact_target_and_duplicate_suggestions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact review target"):
            parse_claude_red_team_advice(
                json.dumps(
                    {
                        "suggestions": [
                            {
                                "code": "ROLE_MISMATCH",
                                "target": "some vague section",
                                "change": "明确角色职责。",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )
        row = {
            "code": "ROLE_MISMATCH",
            "target": "node:node-1",
            "change": "使角色与分配工作一致。",
        }
        with self.assertRaisesRegex(ValueError, "exact duplicates"):
            parse_claude_red_team_advice(
                json.dumps({"suggestions": [row, row]}, ensure_ascii=False)
            )

    def test_approval_or_rejection_fields_are_rejected(self) -> None:
        for payload in (
            '{"decision":"APPROVE","suggestions":[]}',
            '{"decision":"REJECT","suggestions":[]}',
        ):
            with self.assertRaises(ValueError):
                parse_claude_red_team_advice(payload)

    def test_call_budget_is_fixed_three(self) -> None:
        self.assertEqual(1, CLAUDE_RED_TEAM_MAX_CALLS_PER_TASK)
        self.assertEqual(1, GPT_SYNTHESIS_CALLS)
        self.assertEqual(3, CLAUDE_RED_TEAM_GOVERNANCE_CALLS)

    def test_forbidden_capabilities_include_gatekeeping(self) -> None:
        forbidden = set(forbidden_claude_capabilities())
        self.assertIn("approve_proposal", forbidden)
        self.assertIn("reject_proposal", forbidden)
        self.assertIn("block_execution", forbidden)
        self.assertIn("repeat_red_team_review", forbidden)


if __name__ == "__main__":
    unittest.main()
