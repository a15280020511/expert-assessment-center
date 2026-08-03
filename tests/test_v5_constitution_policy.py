from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from execution_graph_validator import validate_execution_graph  # noqa: E402
from v5_free_first_preflight import (  # noqa: E402
    SCHEMA_VERSION,
    evaluate_free_first_preflight,
)
from v5_model_company import (  # noqa: E402
    REQUIRE_DISTINCT_MODEL_COMPANIES,
    canonical_model_company,
)


class ConstitutionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(
                encoding="utf-8"
            )
        )
        cls.constitution = (ROOT / "CONSTITUTION.md").read_text(
            encoding="utf-8"
        )

    def test_free_first_policy_is_fail_closed(self) -> None:
        policy = self.policy
        free = policy["free_first_testing"]
        self.assertTrue(policy["fail_closed"])
        self.assertEqual(
            free["required_order"],
            [
                "zero_call_deterministic_validation",
                "zero_cost_free_model_canary",
                "explicitly_authorized_paid_acceptance_or_production",
            ],
        )
        self.assertTrue(free["free_model_required_before_paid"])
        self.assertEqual(free["free_model_actual_cost_usd"], 0.0)
        self.assertFalse(free["automatic_paid_retry_allowed"])
        self.assertFalse(
            free["free_evidence_can_qualify_formal_model_identity"]
        )
        self.assertFalse(free["free_evidence_can_move_production"])

    def test_free_preflight_cannot_promote_production(self) -> None:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "target_sha": "a" * 40,
            "simulation": {
                "status": "PASS",
                "model_calls": 0,
                "paid_model_calls": 0,
            },
            "free_canary": {
                "status": "PASS",
                "requested_model": "openrouter/free",
                "model_requests": 1,
                "successful_model_calls": 1,
                "paid_model_calls": 0,
                "actual_cost_usd": 0.0,
            },
            "shadow_governance": None,
            "paid_acceptance_triggered": False,
            "production_ref_moved": False,
        }
        verdict = evaluate_free_first_preflight(receipt)
        self.assertEqual(verdict["status"], "PASS")
        self.assertTrue(verdict["paid_acceptance_allowed"])
        self.assertFalse(verdict["production_promotion_allowed"])
        self.assertFalse(verdict["formal_model_identity_qualified"])

    def test_company_aliases_cannot_evade_all_different_rule(self) -> None:
        self.assertTrue(REQUIRE_DISTINCT_MODEL_COMPANIES)
        self.assertEqual(canonical_model_company("google/model-a"), "google")
        self.assertEqual(canonical_model_company("deepmind/model-b"), "google")
        diversity = self.policy["expert_company_diversity"]
        self.assertEqual(diversity["constraint"], "all_different")
        self.assertFalse(diversity["provider_can_override_company"])
        self.assertEqual(diversity["duplicate_company_action"], "fail_closed")

    def test_dynamic_task_matching_is_required_and_task_local(self) -> None:
        self.assertEqual(
            self.policy["schema_version"],
            "v5-constitutional-policy-4",
        )
        dynamic = self.policy["dynamic_task_matching"]
        self.assertTrue(dynamic["required"])
        self.assertEqual(dynamic["planner"], "gpt_latest_single_pass")
        self.assertTrue(dynamic["recomputed_from_current_task"])
        self.assertFalse(dynamic["cross_task_history_allowed"])
        self.assertTrue(dynamic["live_complete_endpoint_catalog_required"])
        required_dynamic_fields = {
            "work_items",
            "dependency_graph",
            "expert_count",
            "expert_roles",
            "expert_functions",
            "execution_stages",
            "final_nodes",
            "model",
            "provider",
            "reasoning_effort",
            "output_capacity_advisory",
            "recovery_candidates",
        }
        self.assertEqual(
            set(dynamic["dynamically_determined"]),
            required_dynamic_fields,
        )
        for key in (
            "fixed_expert_slots_allowed",
            "fixed_expert_count_allowed",
            "fixed_roles_allowed",
            "fixed_model_combinations_allowed",
            "fixed_provider_routes_allowed",
            "fixed_parameter_tiers_allowed",
            "keyword_routing_allowed",
            "domain_hardcoding_allowed",
            "local_scoring_or_optimizer_selection_allowed",
            "deterministic_validator_can_redesign_team",
        ):
            self.assertFalse(dynamic[key], key)
        self.assertEqual(
            dynamic["invalid_dynamic_plan_action"],
            "fail_closed",
        )

    def test_tool_prohibition_covers_governance_and_all_experts(self) -> None:
        tools = self.policy["tool_prohibition"]
        self.assertEqual(
            tools["scope"],
            [
                "gpt_proposal",
                "claude_red_team",
                "gpt_synthesis",
                "expert_execution",
                "expert_recovery",
            ],
        )
        for key in (
            "external_tools_allowed",
            "web_browsing_allowed",
            "mcp_or_plugin_allowed",
            "code_or_shell_execution_allowed",
            "database_or_file_lookup_allowed",
            "connector_or_external_api_allowed",
            "request_tool_fields_allowed",
            "external_fact_collection_allowed",
        ):
            self.assertFalse(tools[key], key)
        self.assertEqual(tools["violation_action"], "fail_closed")

    def test_constitution_exposes_dynamic_matching_and_no_tools_as_top_articles(
        self,
    ) -> None:
        self.assertIn(
            "## 第六条：依据任务全动态拆解、选模、编组与参数匹配",
            self.constitution,
        )
        self.assertIn(
            "## 第七条：治理链和专家执行全面禁止使用工具",
            self.constitution,
        )
        self.assertIn("不得沿用跨任务历史编组", self.constitution)
        self.assertIn("模型参数必须根据任务复杂度", self.constitution)
        self.assertIn("请求体不得向模型暴露 `tools`", self.constitution)

    def test_duplicate_expert_companies_fail_graph_validation(self) -> None:
        def node(node_id: str, work: str, model: str) -> SelectedNode:
            return SelectedNode(
                node_id=node_id,
                assigned_work=(work,),
                professional_capabilities={},
                functions=("analysis",),
                prompt_profile={},
                reasoning_profile={},
                parameter_profile={},
                model=model,
                provider_endpoint="provider/example",
                output_contract={"required": True},
                estimated_quality=0.8,
                quality_uncertainty=0.1,
                estimated_cost=0.0,
                failure_probability=0.0,
                request_config={},
            )

        graph = ExecutionGraph(
            nodes=(
                node("n1", "w1", "google/model-a"),
                node("n2", "w2", "deepmind/model-b"),
            ),
            edges=(),
            execution_stages=(("n1", "n2"),),
            entry_nodes=("n1", "n2"),
            final_nodes=("n1", "n2"),
            required_work=("w1", "w2"),
            estimated_quality=0.8,
            quality_floor=0.7,
            estimated_total_cost=0.0,
        )
        issues = validate_execution_graph(graph)
        self.assertIn("model_company_reuse", {issue.code for issue in issues})


if __name__ == "__main__":
    unittest.main()
