import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import team_optimizer  # noqa: E402


class TeamOptimizerTests(unittest.TestCase):
    @staticmethod
    def profile(complexity="simple", *, high_stakes=False, long_context=False, domains=None):
        domains = domains or ["business"]
        return model_market.TaskProfile(
            domains=domains,
            primary_domain=domains[0],
            secondary_domain=domains[1] if len(domains) > 1 else domains[0],
            complexity=complexity,
            complexity_score={"simple": 1, "medium": 2, "complex": 5}[complexity],
            high_stakes=high_stakes,
            chinese=True,
            long_context=long_context,
            requested_context=32768,
        )

    @staticmethod
    def run(task, output_dir):
        return SimpleNamespace(
            task=task,
            quality_tier="value",
            max_estimated_cost_usd=None,
            candidate_pool_per_seat=3,
            output_dir=output_dir,
            catalog_file=Path("fixture.json"),
            api_key=None,
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
            require_all_experts=True,
        )

    @staticmethod
    def model(index):
        author = f"vendor{index}"
        row = model_market.ModelInfo(
            id=f"{author}/model",
            name=f"Model {index}",
            description="business finance research evidence risk audit reasoning analysis",
            author=author,
            context_length=131072,
            max_completion_tokens=8192,
            prompt_price_per_million=0.5 + index * 0.1,
            completion_price_per_million=1.0 + index * 0.2,
            supported_parameters=["reasoning", "temperature", "verbosity", "max_tokens"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        row.ranks = {"intelligence-high-to-low": index + 1}
        row.components = {"quality": 0.95 - index * 0.03, "history": 0.80}
        row.benchmark_scores = {"intelligence_index": 95 - index}
        row.benchmark_source = "test"
        return row

    def test_topology_is_inferred_from_task_risk_and_complexity(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.run("普通商业问题", Path(temp))
            self.assertEqual(team_optimizer.infer_task_input(self.profile(), run)["expert_count"], 1)
            self.assertEqual(
                team_optimizer.infer_task_input(self.profile("medium", domains=["business", "legal"]), run)["expert_count"],
                2,
            )
            self.assertEqual(
                team_optimizer.infer_task_input(
                    self.profile("complex", high_stakes=True, domains=["business", "legal", "security"]), run
                )["expert_count"],
                4,
            )

    def test_embedded_task_parameters_override_objective_and_count(self):
        task = '<expert-team-config>{"objective":"quality","expert_count":2,"budget_usd":1.5}</expert-team-config>\n分析项目'
        with tempfile.TemporaryDirectory() as temp:
            inputs = team_optimizer.infer_task_input(self.profile("medium"), self.run(task, Path(temp)))
        self.assertEqual(inputs["objective"], "quality")
        self.assertEqual(inputs["expert_count"], 2)
        self.assertEqual(inputs["budget_usd"], 1.5)

    def test_cp_sat_selects_provider_distinct_dynamic_team(self):
        models = [self.model(index) for index in range(7)]
        profile = self.profile("medium", domains=["business", "research"])
        with tempfile.TemporaryDirectory() as temp:
            run = self.run("比较商业方案并核验证据", Path(temp))
            with mock.patch.object(team_optimizer.base, "_stable_pool", return_value=models), mock.patch.object(
                team_optimizer.base, "_enrich_benchmarks", return_value={}
            ), mock.patch.object(team_optimizer, "activate_runtime"):
                experts, judge, estimated = team_optimizer.select_team(models, profile, run)
            evidence = json.loads((Path(temp) / "team-optimization.json").read_text(encoding="utf-8"))
        self.assertEqual(len(experts), 2)
        self.assertEqual(evidence["solver_status"], "OPTIMAL")
        providers = {expert.model_id.split("/", 1)[0] for expert in experts}
        providers.add(judge.model_id.split("/", 1)[0])
        self.assertEqual(len(providers), 3)
        self.assertGreater(estimated, 0)


if __name__ == "__main__":
    unittest.main()
