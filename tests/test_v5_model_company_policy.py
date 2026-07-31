import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402
from v5_model_company_policy import (  # noqa: E402
    build_disjoint_recovery_pool,
    model_company,
)
from v5_planner import CandidateNode, V5PlanningError  # noqa: E402
from v5_value_optimizer import optimize_execution_graph  # noqa: E402


class TestV5ModelCompanyPolicy(unittest.TestCase):
    @staticmethod
    def candidate(
        candidate_id: str,
        model: str,
        coverage: str,
        *,
        quality: float,
        cost: float,
        provider: str,
    ) -> CandidateNode:
        work_id = coverage.split("#", 1)[0]
        return CandidateNode(
            candidate_id=candidate_id,
            interpretation_id="interpretation-test",
            coverage_keys=(coverage,),
            assigned_work=(work_id,),
            copy_indices=(int(coverage.rsplit("#", 1)[1]),),
            professional_capabilities={"general_analysis": quality},
            functions=("analysis",),
            prompt_profile={"profile_id": f"prompt-{candidate_id}", "modules": []},
            reasoning_profile={"reasoning_enabled": True, "effort": "low"},
            parameter_profile={"profile_id": f"params-{candidate_id}", "parameters": {}},
            model=model,
            provider_endpoint=f"{model}@{provider}",
            provider_slug=provider,
            output_contract={"required_fields": []},
            estimated_quality=quality,
            quality_uncertainty=0.05,
            estimated_cost=cost,
            failure_probability=0.02,
            request_config={
                "provider": {
                    "order": [provider],
                    "only": [provider],
                    "allow_fallbacks": False,
                    "require_parameters": True,
                }
            },
            independence_groups=(),
        )

    @staticmethod
    def bundle(candidates):
        return {
            "version": 5,
            "candidates": [candidate.to_dict() for candidate in candidates],
            "interpretations": {
                "interpretation-test": {
                    "metrics": {"interpretation_score": 0.8},
                    "work_ids": ["work-1", "work-2"],
                    "copies_by_work": {"work-1": 1, "work-2": 1},
                    "atomic_edges": [],
                }
            },
        }

    def test_known_namespace_aliases_collapse_to_one_company(self):
        self.assertEqual(model_company("google/gemini-test"), "google")
        self.assertEqual(model_company("deepmind/gemini-test"), "google")
        self.assertEqual(model_company("meta-llama/llama-test"), "meta")
        self.assertEqual(model_company("qwen/qwen-test"), "alibaba")
        self.assertEqual(model_company("z-ai/glm-test"), "zhipu-ai")

    def test_optimizer_enforces_company_uniqueness_across_entire_task(self):
        candidates = [
            self.candidate(
                "node-openai-1",
                "openai/model-a",
                "work-1#0",
                quality=0.95,
                cost=0.001,
                provider="provider-a",
            ),
            self.candidate(
                "node-google-1",
                "google/model-b",
                "work-1#0",
                quality=0.75,
                cost=0.003,
                provider="provider-b",
            ),
            self.candidate(
                "node-openai-2",
                "openai/model-c",
                "work-2#0",
                quality=0.96,
                cost=0.001,
                provider="provider-c",
            ),
            self.candidate(
                "node-anthropic-2",
                "anthropic/model-d",
                "work-2#0",
                quality=0.76,
                cost=0.003,
                provider="provider-d",
            ),
        ]
        result = optimize_execution_graph(
            self.bundle(candidates),
            limits=GraphLimits(max_nodes=4, max_replacements=2),
            solver_timeout_seconds=5,
        )
        audit = result["model_company_policy"]
        self.assertTrue(audit["unique"])
        self.assertEqual(audit["selected_model_count"], 2)
        self.assertEqual(audit["selected_company_count"], 2)
        self.assertEqual(
            len({row["model_company"] for row in audit["selected"]}),
            2,
        )
        metadata = result["execution_graph"]["metadata"]
        self.assertTrue(metadata["independence_policy"]["hard_model_company_uniqueness"])

    def test_optimizer_fails_closed_when_company_diversity_is_impossible(self):
        candidates = [
            self.candidate(
                "node-openai-1",
                "openai/model-a",
                "work-1#0",
                quality=0.95,
                cost=0.001,
                provider="provider-a",
            ),
            self.candidate(
                "node-openai-2",
                "openai/model-b",
                "work-2#0",
                quality=0.96,
                cost=0.001,
                provider="provider-b",
            ),
        ]
        with self.assertRaises(V5PlanningError):
            optimize_execution_graph(
                self.bundle(candidates),
                limits=GraphLimits(max_nodes=4),
                solver_timeout_seconds=5,
            )

    def test_recovery_pool_preserves_exact_copy_and_global_company_disjointness(self):
        selected = [
            self.candidate(
                "node-a",
                "openai/model-a",
                "work-1#1",
                quality=0.9,
                cost=0.002,
                provider="provider-a",
            ).to_dict(),
            self.candidate(
                "node-b",
                "anthropic/model-b",
                "work-2#0",
                quality=0.9,
                cost=0.002,
                provider="provider-b",
            ).to_dict(),
        ]
        candidates = selected + [
            self.candidate(
                "node-openai-other",
                "openai/model-c",
                "work-1#1",
                quality=0.99,
                cost=0.001,
                provider="provider-c",
            ).to_dict(),
            self.candidate(
                "node-google-a",
                "google/model-d",
                "work-1#1",
                quality=0.8,
                cost=0.003,
                provider="provider-d",
            ).to_dict(),
            self.candidate(
                "node-google-b",
                "google/model-e",
                "work-2#0",
                quality=0.85,
                cost=0.002,
                provider="provider-e",
            ).to_dict(),
            self.candidate(
                "node-mistral-b",
                "mistralai/model-f",
                "work-2#0",
                quality=0.8,
                cost=0.003,
                provider="provider-f",
            ).to_dict(),
            self.candidate(
                "node-wrong-copy",
                "cohere/model-g",
                "work-1#0",
                quality=0.99,
                cost=0.001,
                provider="provider-g",
            ).to_dict(),
        ]
        pool, audit = build_disjoint_recovery_pool(
            selected,
            candidates,
            interpretation_id="interpretation-test",
            maximum_rows_per_node=1,
        )
        self.assertEqual(pool["node-a"][0]["candidate_id"], "node-google-a")
        self.assertEqual(pool["node-a"][0]["coverage_keys"], ("work-1#1",))
        self.assertEqual(pool["node-b"][0]["candidate_id"], "node-mistral-b")
        companies = {
            row["model_company"]
            for rows in pool.values()
            for row in rows
        }
        self.assertEqual(companies, {"google", "mistral-ai"})
        self.assertFalse(audit["selected_company_reuse_allowed"])
        self.assertFalse(audit["recovery_company_reuse_across_nodes_allowed"])

    def test_run_config_accepts_top_150(self):
        args = SimpleNamespace(
            config=str(model_market.DEFAULT_CONFIG),
            task="测试任务",
            quality_tier=None,
            max_estimated_cost_usd=None,
            ranking_limit=150,
            max_completion_tokens=None,
            reasoning_effort=None,
            catalog_file=None,
            output_dir="unused",
            dry_run=True,
            require_live_catalog=False,
        )
        run = model_market.build_run_config(args)
        self.assertEqual(run.ranking_limit, 150)


if __name__ == "__main__":
    unittest.main()
