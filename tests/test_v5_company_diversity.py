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


def candidate(
    candidate_id,
    coverage_key,
    model,
    *,
    work_id=None,
    quality=0.80,
    cost=0.01,
    failure=0.04,
):
    work_id = work_id or coverage_key.split("#", 1)[0]
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [coverage_key],
        "assigned_work": [work_id],
        "copy_indices": [int(coverage_key.rsplit("#", 1)[1])],
        "professional_capabilities": {"general_analysis": 0.8},
        "functions": ["analysis"],
        "prompt_profile": {"profile_id": f"prompt-{candidate_id}"},
        "reasoning_profile": {
            "reasoning_enabled": True,
            "effort": "medium",
        },
        "parameter_profile": {"profile_id": f"params-{candidate_id}"},
        "model": model,
        "provider_endpoint": f"{model}@provider-{candidate_id}",
        "provider_slug": f"provider-{candidate_id}",
        "output_contract": {
            "required_fields": [],
            "machine_readable_required": False,
        },
        "estimated_quality": quality,
        "quality_uncertainty": 0.08,
        "estimated_cost": cost,
        "failure_probability": failure,
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
    def limits(self):
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

    def test_explicit_variable_pool_and_top100_range_are_enabled(self):
        self.assertTrue(company_policy.REQUIRE_DISTINCT_MODEL_COMPANIES)
        self.assertEqual(company_policy.MINIMUM_CANDIDATES_PER_WORK, 24)
        policy = PlannerPolicy(runtime_config=None)
        self.assertTrue(policy.require_distinct_model_companies)
        self.assertEqual(policy.minimum_candidates_per_work, 24)

        runtime_config = json.loads(
            (ROOT / "open-model-market" / "config.json").read_text(
                encoding="utf-8"
            )
        )
        selection = runtime_config["selection"]
        self.assertEqual(selection["ranking_limit"], 100)
        self.assertEqual(selection["candidate_pool_per_seat"], 24)
        self.assertTrue(selection["require_distinct_model_companies"])

        optimization = json.loads(
            (
                ROOT
                / "open-model-market"
                / "optimization_policy.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            optimization["official_intelligence_ranking_limit"],
            100,
        )
        self.assertTrue(optimization["require_distinct_model_companies"])
        self.assertEqual(optimization["candidate_pool_per_seat"], 24)
        self.assertIn(
            "official intelligence rank within top 100",
            optimization["hard_constraints"],
        )

    def test_company_aliases_are_canonicalized(self):
        self.assertEqual(
            company_policy.canonical_model_company("meta-llama/llama-4"),
            "meta",
        )
        self.assertEqual(
            company_policy.canonical_model_company("qwen/qwen3"),
            "alibaba",
        )
        self.assertEqual(
            company_policy.canonical_model_company("z-ai/glm-5"),
            "zhipu",
        )
        self.assertEqual(
            company_policy.canonical_model_company("unknown-lab/model"),
            "unknown-lab",
        )

    def test_pruner_keeps_dominated_distinct_company_alternative(self):
        rows = [
            v5_planner.CandidateNode(
                **candidate(
                    "openai-best",
                    "w1#0",
                    "openai/gpt-a",
                    quality=0.95,
                    cost=0.005,
                )
            ),
            v5_planner.CandidateNode(
                **candidate(
                    "openai-second",
                    "w1#0",
                    "openai/gpt-b",
                    quality=0.90,
                    cost=0.008,
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
        self.assertIn("openai", companies)
        self.assertIn("anthropic", companies)

    def test_optimizer_rejects_same_company_even_when_models_differ(self):
        rows = [
            candidate("oa-w1", "w1#0", "openai/gpt-a", quality=0.95),
            candidate("oa-w2", "w2#0", "openai/gpt-b", quality=0.96),
            candidate(
                "an-w2",
                "w2#0",
                "anthropic/claude-a",
                quality=0.75,
            ),
        ]
        result = company_policy.risk_budgeted_optimize_execution_graph(
            bundle(rows),
            limits=self.limits(),
            solver_timeout_seconds=3.0,
        )
        graph = result["execution_graph"]
        companies = [
            company_policy.canonical_model_company(row["model"])
            for row in graph["nodes"]
        ]
        self.assertEqual(len(companies), len(set(companies)))
        self.assertEqual(set(companies), {"openai", "anthropic"})
        self.assertTrue(result["require_distinct_model_companies"])
        self.assertEqual(
            graph["metadata"]["model_company_policy"][
                "same_company_reuse_allowed"
            ],
            False,
        )

    def test_company_shortage_fails_closed_before_execution(self):
        rows = [
            candidate("oa-w1", "w1#0", "openai/gpt-a"),
            candidate("oa-w2", "w2#0", "openai/gpt-b"),
        ]
        with self.assertRaisesRegex(
            v5_planner.V5PlanningError,
            "distinct-model-company hard constraint",
        ):
            company_policy.risk_budgeted_optimize_execution_graph(
                bundle(rows),
                limits=self.limits(),
                solver_timeout_seconds=2.0,
            )

    def test_recovery_pool_excludes_all_selected_companies(self):
        rows = [
            candidate("oa-w1", "w1#0", "openai/gpt-a", quality=0.95),
            candidate("go-w1", "w1#0", "google/gemini-a", quality=0.70),
            candidate(
                "an-w1",
                "w1#0",
                "anthropic/claude-b",
                quality=0.80,
            ),
            candidate(
                "an-w2",
                "w2#0",
                "anthropic/claude-a",
                quality=0.94,
            ),
            candidate(
                "mi-w2",
                "w2#0",
                "mistralai/mistral-a",
                quality=0.72,
            ),
            candidate("oa-w2", "w2#0", "openai/gpt-b", quality=0.82),
        ]
        result = company_policy.risk_budgeted_optimize_execution_graph(
            bundle(rows),
            limits=self.limits(),
            solver_timeout_seconds=3.0,
        )
        graph = result["execution_graph"]
        selected_companies = set(
            graph["metadata"]["model_company_policy"][
                "selected_companies"
            ]
        )
        recovery_pool = graph["metadata"]["recovery_pool"]
        for alternatives in recovery_pool.values():
            companies = [row["model_company"] for row in alternatives]
            self.assertTrue(selected_companies.isdisjoint(companies))
            self.assertEqual(len(companies), len(set(companies)))


if __name__ == "__main__":
    unittest.main()
