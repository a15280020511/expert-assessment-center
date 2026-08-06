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
from v5_free_first_preflight import SCHEMA_VERSION, evaluate_free_first_preflight  # noqa: E402
from v5_model_company import REQUIRE_DISTINCT_MODEL_COMPANIES, canonical_model_company  # noqa: E402


class ConstitutionPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = json.loads((MARKET / "constitutional_policy.json").read_text(encoding="utf-8"))
        cls.constitution = (ROOT / "CONSTITUTION.md").read_text(encoding="utf-8")

    def test_free_first_policy_is_fail_closed(self) -> None:
        free = self.policy["free_first_testing"]
        self.assertTrue(self.policy["fail_closed"])
        self.assertEqual(
            free["required_order"],
            [
                "zero_call_deterministic_validation",
                "zero_cost_free_model_canary",
                "explicitly_authorized_paid_acceptance_or_production",
            ],
        )
        self.assertEqual(free["free_model_actual_cost_usd"], 0.0)
        self.assertFalse(free["automatic_paid_full_task_retry_allowed"])
        self.assertFalse(free["free_evidence_can_move_production"])

    def test_free_preflight_cannot_promote_production(self) -> None:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "target_sha": "a" * 40,
            "simulation": {"status": "PASS", "model_calls": 0, "paid_model_calls": 0},
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

    def test_company_aliases_cannot_evade_all_different_rule(self) -> None:
        self.assertTrue(REQUIRE_DISTINCT_MODEL_COMPANIES)
        self.assertEqual(canonical_model_company("google/model-a"), "google")
        self.assertEqual(canonical_model_company("deepmind/model-b"), "google")
        diversity = self.policy["expert_company_diversity"]
        self.assertEqual(diversity["constraint"], "all_different")
        self.assertFalse(diversity["provider_can_override_company"])
        self.assertEqual(diversity["duplicate_company_action"], "fail_closed")

    def test_active_governance_has_zero_model_calls(self) -> None:
        self.assertEqual(
            self.policy["schema_version"],
            "v5-constitutional-policy-7-top50-ortools-open-provider",
        )
        chain = self.policy["governance_chain"]
        self.assertEqual(chain["candidate_pool_authority"], "decision-system-governance")
        self.assertEqual(chain["assignment_authority"], "expert-assessment-center-ortools-cp-sat")
        self.assertEqual(chain["provider_routing_authority"], "openrouter-unrestricted")
        self.assertFalse(chain["claude_mechanism_enabled"])
        self.assertEqual(chain["claude_calls_per_task"], 0)
        self.assertEqual(chain["gpt_selection_calls_per_task"], 0)
        self.assertEqual(chain["governance_model_calls_per_task"], 0)
        self.assertFalse(chain["model_loop_allowed"])
        self.assertFalse(chain["agent_framework_allowed"])

    def test_weekly_top50_ortools_organization_is_authoritative(self) -> None:
        pool = self.policy["candidate_pool"]
        self.assertEqual(pool["pool_size"], 50)
        self.assertEqual(pool["popularity_period"], "week")
        self.assertFalse(pool["daily_or_monthly_can_replace_primary_pool"])
        self.assertFalse(pool["provider_endpoint_qualification_required"])
        self.assertFalse(pool["zdr_provider_qualification_required"])
        optimizer = self.policy["optimizer_runtime"]
        self.assertEqual(optimizer["engine"], "ortools-cp-sat")
        self.assertEqual(optimizer["primary_expert_count"], 4)
        self.assertEqual(optimizer["warm_recovery_count"], 4)
        self.assertEqual(optimizer["required_solver_status"], "OPTIMAL")
        self.assertEqual(optimizer["deterministic_workers"], 1)
        self.assertFalse(optimizer["provider_metric_used"])
        self.assertNotIn("qualified_provider_resilience", optimizer["objective_components"])
        matching = self.policy["dynamic_task_matching"]
        self.assertEqual(matching["planner"], "expert-center-ortools-cp-sat")
        self.assertTrue(matching["provider_selection_delegated_to_openrouter"])
        self.assertEqual(
            matching["organization"],
            "parallel_independent_analysis_then_cross_review_then_final_synthesis",
        )
        self.assertIn("OR-Tools", self.constitution)
        self.assertIn("周榜前五十", self.constitution)
        self.assertIn("NetworkX", self.constitution)
        self.assertIn("Token 与费用实行软治理", self.constitution)

    def test_provider_routing_is_completely_open(self) -> None:
        privacy = self.policy["expert_endpoint_privacy"]
        self.assertEqual(privacy["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(privacy["zdr_required"])
        self.assertFalse(privacy["data_collection_filter_applied"])
        self.assertFalse(privacy["provider_allowlist_allowed"])
        self.assertFalse(privacy["provider_order_allowed"])
        self.assertTrue(privacy["provider_fallback_allowed"])
        self.assertTrue(privacy["unrestricted_provider_fallback_allowed"])
        self.assertTrue(privacy["openrouter_selects_provider"])
        self.assertFalse(privacy["model_substitution_allowed"])
        self.assertIn("Provider 完全开放", self.constitution)

    def test_dependency_set_is_minimal_and_sufficient(self) -> None:
        deps = self.policy["dependency_allowlist"]
        self.assertEqual(set(deps["runtime"]), {"jsonschema", "networkx", "ortools"})
        self.assertTrue(deps["minimum_sufficient_set"])
        self.assertFalse(deps["langchain_allowed"])
        self.assertFalse(deps["crewai_allowed"])
        self.assertFalse(deps["autogen_allowed"])

    def test_tool_prohibition_covers_selection_and_experts(self) -> None:
        tools = self.policy["tool_prohibition"]
        self.assertEqual(tools["scope"], ["deterministic_selection", "expert_execution", "expert_recovery"])
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
            nodes=(node("n1", "w1", "google/model-a"), node("n2", "w2", "deepmind/model-b")),
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
