import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import v5_company_diversity as company_policy  # noqa: E402
import v5_planner  # noqa: E402
from v5_planning_runtime import PlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def candidate(candidate_id, coverage_key, model, *, quality=0.8, cost=0.01):
    work_id = coverage_key.split("#", 1)[0]
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [coverage_key],
        "assigned_work": [work_id],
        "copy_indices": [int(coverage_key.rsplit("#", 1)[1])],
        "professional_capabilities": {"general_analysis": 0.8},
        "functions": ["analysis"],
        "prompt_profile": {"profile_id": f"prompt-{candidate_id}"},
        "reasoning_profile": {"reasoning_enabled": True, "effort": "medium"},
        "parameter_profile": {"profile_id": f"params-{candidate_id}"},
        "model": model,
        "provider_endpoint": f"{model}@provider-{candidate_id}",
        "provider_slug": f"provider-{candidate_id}",
        "output_contract": {"required_fields": []},
        "estimated_quality": quality,
        "quality_uncertainty": 0.08,
        "estimated_cost": cost,
        "failure_probability": 0.04,
        "request_config": {
            "provider": {
                "order": [f"provider-{candidate_id}"],
                "only": [f"provider-{candidate_id}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
        "independence_groups": [],
    }


def bundle(rows):
    return {
        "version": 5,
        "candidates": rows,
        "interpretations": {
            "i1": {
                "metrics": {"interpretation_score": 0.8},
                "work_ids": ["w1", "w2"],
                "copies_by_work": {"w1": 1, "w2": 1},
                "atomic_edges": [],
            }
        },
    }


class V5CompanyDiversityTests(unittest.TestCase):
    @staticmethod
    def limits():
        return GraphLimits(
            max_nodes=3,
            max_edges=4,
            max_stages=3,
            max_model_calls=4,
            max_retries=1,
            max_replacements=1,
            max_budget_usd=0.20,
            cost_risk_multiplier=1.18,
        )

    def test_top150_is_ceiling_and_candidate_floor_is_not_fixed_width(self):
        self.assertTrue(company_policy.REQUIRE_DISTINCT_MODEL_COMPANIES)
        self.assertEqual(company_policy.MINIMUM_CANDIDATES_PER_WORK, 2)
        policy = PlannerPolicy(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                quality_tier="value",
            )
        )
        self.assertTrue(policy.require_distinct_model_companies)
        self.assertEqual(policy.minimum_candidates_per_work, 2)

        config = json.loads(
            (ROOT / "open-model-market" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(config["selection"]["ranking_limit"], 150)
        self.assertEqual(config["routing"]["max_intelligence_rank"], 150)

        policy_json = json.loads(
            (ROOT / "open-model-market" / "optimization_policy.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            policy_json["official_intelligence_ranking_limit"],
            150,
        )
        self.assertIn(
            "official intelligence rank within top 150",
            policy_json["hard_constraints"],
        )

    def test_company_aliases_are_canonicalized(self):
        aliases = {
            "meta-llama/llama-4": "meta",
            "qwen/qwen3": "alibaba",
            "z-ai/glm-5": "zhipu",
            "deepmind/gemini": "google",
            "amazon-nova/pro": "amazon",
            "thudm/glm": "zhipu",
        }
        for model_id, expected in aliases.items():
            self.assertEqual(
                company_policy.canonical_model_company(model_id),
                expected,
            )

    def test_pruner_preserves_dominated_distinct_company(self):
        rows = [
            v5_planner.CandidateNode(
                **candidate(
                    "openai-best",
                    "w1#0",
                    "openai/gpt-a",
                    quality=0.95,
                )
            ),
            v5_planner.CandidateNode(
                **candidate(
                    "openai-second",
                    "w1#0",
                    "openai/gpt-b",
                    quality=0.90,
                )
            ),
            v5_planner.CandidateNode(
                **candidate(
                    "anthropic-dominated",
                    "w1#0",
                    "anthropic/claude-a",
                    quality=0.70,
                    cost=0.02,
                )
            ),
        ]
        kept = company_policy.company_preserving_pareto_prune(
            rows,
            maximum_per_group=2,
        )
        companies = {
            company_policy.candidate_company(row) for row in kept
        }
        self.assertEqual(companies, {"openai", "anthropic"})

    def test_optimizer_uses_different_companies(self):
        result = company_policy.risk_budgeted_optimize_execution_graph(
            bundle(
                [
                    candidate(
                        "oa-w1",
                        "w1#0",
                        "openai/gpt-a",
                        quality=0.95,
                    ),
                    candidate(
                        "oa-w2",
                        "w2#0",
                        "openai/gpt-b",
                        quality=0.96,
                    ),
                    candidate(
                        "an-w2",
                        "w2#0",
                        "anthropic/claude-a",
                        quality=0.75,
                    ),
                ]
            ),
            limits=self.limits(),
            solver_timeout_seconds=3.0,
        )
        companies = [
            company_policy.canonical_model_company(row["model"])
            for row in result["execution_graph"]["nodes"]
        ]
        self.assertEqual(len(companies), len(set(companies)))
        self.assertEqual(set(companies), {"openai", "anthropic"})

    def test_company_shortage_fails_closed(self):
        with self.assertRaisesRegex(
            v5_planner.V5PlanningError,
            "distinct-model-company hard constraint",
        ):
            company_policy.risk_budgeted_optimize_execution_graph(
                bundle(
                    [
                        candidate("oa-w1", "w1#0", "openai/gpt-a"),
                        candidate("oa-w2", "w2#0", "openai/gpt-b"),
                    ]
                ),
                limits=self.limits(),
                solver_timeout_seconds=2.0,
            )


if __name__ == "__main__":
    unittest.main()
