from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open-model-market"
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from v6_governed_roster import (  # noqa: E402
    GovernedRosterError,
    materialize_execution_graph,
    validate_governed_ticket,
)


def canonical_sha(value):
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def member(member_id, kind, rank, model, company, work_id, role, score, cost):
    return {
        "member_id": member_id,
        "kind": kind,
        "roster_rank": rank,
        "model_id": model,
        "company": company,
        "estimated_task_cost_usd": cost,
        "prompt_usd_per_million": cost * 100,
        "completion_usd_per_million": cost * 200,
        "request_usd": None,
        "balanced_score": score,
        "intelligence_index": score,
        "coding_index": score,
        "agentic_index": score,
        "assigned_work_id": work_id,
        "assigned_role": role,
    }


def fixture_ticket():
    plan = {
        "schema_version": "expert-team-plan-v1",
        "expected_prompt_tokens_per_call": 10_000,
        "expected_completion_tokens_per_call": 2_000,
        "work_items": [
            {
                "work_id": "analysis",
                "objective": "分析主要事实、约束和可选方案。",
                "role": "primary-analysis",
                "dependencies": [],
                "required_outputs": ["findings", "assumptions"],
            },
            {
                "work_id": "challenge",
                "objective": "检查反例、失败模式和证据缺口。",
                "role": "adversarial-review",
                "dependencies": [],
                "required_outputs": ["counterpoints", "risks"],
            },
            {
                "work_id": "synthesis",
                "objective": "综合上游结果形成最终报告。",
                "role": "final-synthesis",
                "dependencies": ["analysis", "challenge"],
                "required_outputs": ["final-report"],
            },
        ],
        "final_work_id": "synthesis",
    }
    roster = {
        "schema_version": "governed-expert-roster-v1",
        "status": "GOVERNED_EXPERT_ROSTER_READY",
        "selection_policy": "lowest task cost with all-different companies",
        "governance_repository": "a15280020511/decision-system-governance",
        "governance_commit_sha": "a" * 40,
        "source_selector_schema_version": "selector-v1",
        "source_catalog_snapshot_sha256": "b" * 64,
        "task_cost_profile": {
            "expected_prompt_tokens_per_call": 10_000,
            "expected_completion_tokens_per_call": 2_000,
        },
        "team_size": 3,
        "recovery_size": 1,
        "approved_total_calls": 4,
        "final_work_id": "synthesis",
        "team_plan_sha256": canonical_sha(plan),
        "primary_members": [
            member("primary-1", "primary", 1, "openai/model-a", "openai", "analysis", "primary-analysis", 50, 0.001),
            member("primary-2", "primary", 2, "deepseek/model-b", "deepseek", "challenge", "adversarial-review", 55, 0.0012),
            member("primary-3", "primary", 3, "qwen/model-c", "qwen", "synthesis", "final-synthesis", 70, 0.0015),
        ],
        "recovery_members": [
            member("recovery-1", "recovery", 4, "anthropic/model-d", "anthropic", None, "preapproved-recovery", 65, 0.0018)
        ],
        "all_companies_unique": True,
        "model_calls_for_selection": 0,
        "selection_cost_usd": 0,
        "secret_values_exposed": False,
    }
    roster["roster_sha256"] = canonical_sha(roster)
    return {
        "task_id": "task-v6-test-0001",
        "route": "expert-team",
        "task": {
            "question": "依据给定事实形成一份完整研判。",
            "requirements": ["区分事实、推断和假设。"],
            "language": "zh-CN",
        },
        "team_plan": plan,
        "approved_budget": {
            "calls": 4,
            "maximum_recovery_calls": 1,
            "cost_policy": "prompt_led_soft_governance",
        },
        "governance_roster": roster,
        "private_output": False,
    }


def endpoint(model, provider, cost):
    return {
        "model": model,
        "provider": provider,
        "provider_endpoint": f"{model}@{provider}",
        "supported_parameters": ["reasoning", "max_tokens"],
        "prompt_price_per_million": 0.1,
        "completion_price_per_million": 0.5,
        "estimated_task_cost_usd": cost,
    }


class GovernedRosterValidationTests(unittest.TestCase):
    def test_valid_ticket_has_zero_governance_model_calls(self):
        validation = validate_governed_ticket(fixture_ticket())
        self.assertEqual(validation["status"], "PASS")
        self.assertEqual(validation["claude_calls"], 0)
        self.assertEqual(validation["gpt_planning_calls"], 0)
        self.assertEqual(validation["gpt_synthesis_calls"], 0)
        self.assertEqual(len(validation["primary_members"]), 3)
        self.assertEqual(len(validation["recovery_members"]), 1)

    def test_tampered_roster_is_rejected(self):
        ticket = fixture_ticket()
        ticket["governance_roster"]["primary_members"][0]["model_id"] = "other/tampered"
        with self.assertRaisesRegex(GovernedRosterError, "digest mismatch"):
            validate_governed_ticket(ticket)

    def test_duplicate_company_is_rejected(self):
        ticket = fixture_ticket()
        roster = ticket["governance_roster"]
        roster["recovery_members"][0]["company"] = "openai"
        roster["roster_sha256"] = canonical_sha({key: value for key, value in roster.items() if key != "roster_sha256"})
        with self.assertRaisesRegex(GovernedRosterError, "companies are not globally unique"):
            validate_governed_ticket(ticket)

    def test_budget_mismatch_is_rejected(self):
        ticket = fixture_ticket()
        ticket["approved_budget"]["calls"] = 5
        with self.assertRaisesRegex(GovernedRosterError, "approved budget"):
            validate_governed_ticket(ticket)

    def test_cycle_is_rejected(self):
        ticket = fixture_ticket()
        ticket["team_plan"]["work_items"][0]["dependencies"] = ["synthesis"]
        roster = ticket["governance_roster"]
        roster["team_plan_sha256"] = canonical_sha(ticket["team_plan"])
        roster["roster_sha256"] = canonical_sha({key: value for key, value in roster.items() if key != "roster_sha256"})
        with self.assertRaisesRegex(GovernedRosterError, "cyclic"):
            validate_governed_ticket(ticket)


class NetworkXMaterializationTests(unittest.TestCase):
    def test_declared_dag_becomes_parallel_then_synthesis_stages(self):
        ticket = fixture_ticket()
        validation = validate_governed_ticket(ticket)
        endpoints = {
            "openai/model-a": endpoint("openai/model-a", "provider-a", 0.001),
            "deepseek/model-b": endpoint("deepseek/model-b", "provider-b", 0.0012),
            "qwen/model-c": endpoint("qwen/model-c", "provider-c", 0.0015),
            "anthropic/model-d": endpoint("anthropic/model-d", "provider-d", 0.0018),
        }
        graph, limits, audit = materialize_execution_graph(
            ticket,
            "依据给定事实形成一份完整研判。",
            validation,
            endpoints,
        )
        self.assertEqual(graph.execution_stages, (("analysis", "challenge"), ("synthesis",)))
        self.assertEqual(graph.final_nodes, ("synthesis",))
        self.assertEqual(audit["status"], "PASS")
        self.assertEqual(audit["claude_calls"], 0)
        self.assertEqual(audit["gpt_planning_calls"], 0)
        self.assertEqual(audit["gpt_synthesis_calls"], 0)
        self.assertEqual(limits.max_model_calls, 3)

    def test_recovery_member_is_assigned_once_to_final_first(self):
        ticket = fixture_ticket()
        validation = validate_governed_ticket(ticket)
        endpoints = {
            model: endpoint(model, f"provider-{index}", 0.001 + index / 10000)
            for index, model in enumerate(
                ["openai/model-a", "deepseek/model-b", "qwen/model-c", "anthropic/model-d"], 1
            )
        }
        graph, _, audit = materialize_execution_graph(ticket, "任务", validation, endpoints)
        assignments = graph.metadata["recovery_assignment"]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["assigned_node_id"], "synthesis")
        pool_count = sum(len(rows) for rows in graph.metadata["recovery_pool"].values())
        self.assertEqual(pool_count, 1)
        self.assertTrue(audit["recovery_models_assigned_once"])

    def test_provider_fallbacks_are_disabled(self):
        ticket = fixture_ticket()
        validation = validate_governed_ticket(ticket)
        endpoints = {
            model: endpoint(model, f"provider-{index}", 0.001)
            for index, model in enumerate(
                ["openai/model-a", "deepseek/model-b", "qwen/model-c", "anthropic/model-d"], 1
            )
        }
        graph, _, _ = materialize_execution_graph(ticket, "任务", validation, endpoints)
        for node in graph.nodes:
            provider = node.request_config["provider"]
            self.assertFalse(provider["allow_fallbacks"])
            self.assertEqual(provider["only"], provider["order"])
            self.assertEqual(len(provider["only"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
