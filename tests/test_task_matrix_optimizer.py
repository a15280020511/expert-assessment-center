import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import task_matrix_optimizer as optimizer  # noqa: E402


class TaskMatrixOptimizerTests(unittest.TestCase):
    @staticmethod
    def profile(*, high_stakes=False, domains=None, complexity="complex"):
        domains = domains or ["business", "legal"]
        return model_market.TaskProfile(
            domains=domains,
            primary_domain=domains[0],
            secondary_domain=domains[1] if len(domains) > 1 else domains[0],
            complexity=complexity,
            complexity_score=5 if complexity == "complex" else 2,
            high_stakes=high_stakes,
            chinese=True,
            long_context=False,
            requested_context=32768,
        )

    @staticmethod
    def run_config(task, output_dir):
        return SimpleNamespace(
            task=task,
            output_dir=output_dir,
            max_estimated_cost_usd=None,
            candidate_pool_per_seat=12,
            soft_price_cap=15.0,
            catalog_file=Path("fixture.json"),
            api_key=None,
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
            require_all_experts=True,
        )

    @staticmethod
    def model(index=0):
        row = model_market.ModelInfo(
            id=f"vendor{index}/model",
            name=f"Model {index}",
            description="business finance legal evidence risk reasoning analysis",
            author=f"vendor{index}",
            context_length=131072,
            max_completion_tokens=8192,
            prompt_price_per_million=0.5,
            completion_price_per_million=1.0,
            supported_parameters=["reasoning", "temperature", "verbosity", "max_tokens", "structured_outputs"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        row.ranks = {"intelligence-high-to-low": index + 1}
        return row

    def test_matrix_has_no_fixed_mode_or_history(self):
        with tempfile.TemporaryDirectory() as temp:
            matrix = optimizer.build_task_matrix(
                self.profile(high_stakes=True),
                self.run_config("评估商业投资，核验证据、计算风险并进行红队反证", Path(temp)),
            )
        self.assertFalse(matrix["history_input_used"])
        self.assertFalse(matrix["fixed_team_mode_used"])
        self.assertFalse(matrix["fixed_parameter_template_used"])
        self.assertIn("domain:business", matrix["required_demands"])
        self.assertIn("adversarial", matrix["required_demands"])
        self.assertIn("evidence", matrix["required_demands"])

    def test_only_concrete_constraints_are_accepted(self):
        task = (
            '<expert-team-input>{"budget_usd":1.2,"min_experts":2,"max_experts":4,'
            '"quality_tolerance_pct":1.5}</expert-team-input>\n分析项目'
        )
        with tempfile.TemporaryDirectory() as temp:
            constraints = optimizer.build_task_matrix(self.profile(), self.run_config(task, Path(temp)))["constraints"]
        self.assertEqual(constraints["budget_usd"], 1.2)
        self.assertEqual(constraints["min_experts"], 2)
        self.assertEqual(constraints["max_experts"], 4)
        self.assertEqual(constraints["quality_tolerance_pct"], 1.5)

    def test_generated_seats_cover_every_required_demand(self):
        with tempfile.TemporaryDirectory() as temp:
            profile = self.profile(high_stakes=True, domains=["coding", "business", "legal"])
            matrix = optimizer.build_task_matrix(
                profile,
                self.run_config("审计代码仓库、商业可行性、成本计算、合规风险和失败模式", Path(temp)),
            )
            seats = optimizer.generate_seats(matrix, profile)
        covered = {demand for seat in seats for demand in seat["covers"]}
        self.assertTrue(set(matrix["required_demands"]).issubset(covered))
        self.assertTrue(any(seat["kind"] == "adversarial" for seat in seats))
        self.assertTrue(any(seat["kind"] == "implementation" for seat in seats))

    def test_parameter_variants_are_generated_not_named_modes(self):
        with tempfile.TemporaryDirectory() as temp:
            matrix = optimizer.build_task_matrix(
                self.profile(high_stakes=True),
                self.run_config("进行高风险商业决策、定量分析与反证", Path(temp)),
            )
        variants = optimizer._variants(self.model(), "adversarial", matrix)
        self.assertTrue(variants)
        self.assertTrue(all(row["id"].startswith("generated-") for row in variants))
        self.assertTrue(all("expected_output_tokens" in row["parameters"] for row in variants))

    def test_live_ranking_overwrites_legacy_history_component(self):
        models = {self.model(index).id: self.model(index) for index in range(2)}
        for model in models.values():
            model.components = {"history": 0.01}
        with tempfile.TemporaryDirectory() as temp:
            ranked = optimizer.rank_models_live_only(
                models,
                self.profile(domains=["business"], complexity="medium"),
                self.run_config("商业分析", Path(temp)),
            )
        self.assertEqual(len(ranked), 2)
        self.assertTrue(all("history" not in model.components for model in ranked))
        self.assertTrue(all("live_stability" in model.components for model in ranked))


if __name__ == "__main__":
    unittest.main()
