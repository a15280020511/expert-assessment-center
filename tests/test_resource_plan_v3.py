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
import resource_call_budget  # noqa: E402
import resource_requirements as requirements  # noqa: E402
import value_resource_plan_optimizer as optimizer  # noqa: E402


class ResourcePlanV3Tests(unittest.TestCase):
    @staticmethod
    def profile(high_stakes=False, domains=None):
        domains = domains or ["business"]
        return model_market.TaskProfile(
            domains=domains,
            primary_domain=domains[0],
            secondary_domain=domains[1] if len(domains) > 1 else domains[0],
            complexity="complex" if len(domains) > 1 else "medium",
            complexity_score=5 if len(domains) > 1 else 2,
            high_stakes=high_stakes,
            chinese=True,
            long_context=False,
            requested_context=32768,
        )

    @staticmethod
    def run_config(task, output_dir):
        return SimpleNamespace(
            task=task,
            max_estimated_cost_usd=None,
            candidate_pool_per_seat=3,
            output_dir=output_dir,
            require_all_experts=True,
            soft_price_cap=15.0,
        )

    @staticmethod
    def model(index):
        vendor = f"vendor{index}"
        row = model_market.ModelInfo(
            id=f"{vendor}/model",
            name=f"Model {index}",
            description="business finance research evidence reasoning decision risk analysis",
            author=vendor,
            context_length=131072,
            max_completion_tokens=16000,
            prompt_price_per_million=0.5 + index * 0.1,
            completion_price_per_million=1.0 + index * 0.2,
            supported_parameters=["reasoning", "structured_outputs", "temperature", "verbosity", "max_tokens"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        row.ranks = {"intelligence-high-to-low": index + 1}
        row.components = {"quality": 0.95 - index * 0.02, "fit": 0.8}
        row.benchmark_scores = {"intelligence_index": 95 - index}
        row.benchmark_source = "test"
        return row

    def test_task_is_compiled_before_market_lookup(self):
        with tempfile.TemporaryDirectory() as temp:
            data = requirements.compile_requirements(
                self.profile(domains=["business", "legal"]),
                self.run_config("比较投资方案，核验证据、计算收益并找出失败风险", Path(temp)),
            )
        self.assertEqual(data["architecture"], "task-to-resource-requirements-before-market-lookup")
        self.assertTrue(data["atomic_work_units"])
        self.assertIn("requested_market_attributes", data)
        self.assertTrue(all(row["required_prompt_modules"] for row in data["atomic_work_units"]))
        self.assertTrue(all("minimum_reasoning_level" in row for row in data["atomic_work_units"]))
        self.assertIsNone(data["constraints"]["max_experts"])
        self.assertFalse(data["fixed_team_mode_used"])
        self.assertFalse(data["fixed_prompt_template_used"])
        self.assertFalse(data["fixed_parameter_template_used"])

    def test_work_packages_are_generated_from_atomic_units(self):
        with tempfile.TemporaryDirectory() as temp:
            data = requirements.compile_requirements(
                self.profile(domains=["business", "legal"]),
                self.run_config("比较商业和法律方案，核验证据并进行风险反证", Path(temp)),
            )
        packages = optimizer.generate_packages(data)
        self.assertTrue(packages)
        self.assertTrue(all(row["unit_ids"] for row in packages))
        self.assertTrue(any(len(row["unit_ids"]) > 1 for row in packages))
        self.assertTrue(any(row["id"] == "red" for row in packages))
        self.assertFalse(any(row["id"] in {"core", "cross", "evidence_quant"} for row in packages))

    def test_prompt_and_parameter_profiles_are_joint_resources(self):
        package = {
            "required_prompt_modules": ["scope", "decision"],
            "unit_ids": ["u1", "u2"],
            "operations": ["analysis", "decision"],
            "minimum_reasoning_level": 1,
            "structured_output_required": True,
            "expected_output_tokens": 1800,
        }
        model = self.model(0)
        requirement_data = {
            "operation_scores": {"decision": 0.9, "creative": 0.0},
            "task_signals": {"complexity": "complex"},
        }
        prompts = optimizer._prompt_profiles(package, high_stakes=True)
        params = optimizer._parameter_profiles(model, package, requirement_data)
        self.assertGreaterEqual(len(prompts), 2)
        self.assertGreater(len(params), 2)
        self.assertTrue(all(row["id"].startswith("prompt-") for row in prompts))
        self.assertTrue(all(row["id"].startswith("params-") for row in params))

    def test_full_selector_maximizes_cost_performance(self):
        models = [self.model(index) for index in range(8)]
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config("比较两个商业投资方案并给出最优选择", Path(temp))
            with mock.patch.object(optimizer.legacy, "_eligible_pool", return_value=models), mock.patch.object(
                optimizer.scoring, "_enrich_benchmarks", return_value={}
            ), mock.patch.object(optimizer.dynamic_runtime, "activate_runtime"):
                experts, judge, estimated = optimizer.select_team(models, self.profile(), run)
            plan = json.loads((Path(temp) / "team-optimization.json").read_text(encoding="utf-8"))
        self.assertTrue(experts)
        self.assertTrue(judge.model_id)
        self.assertGreater(estimated, 0)
        self.assertEqual(plan["version"], 3)
        self.assertEqual(plan["highest_principle"], "maximum_cost_performance")
        self.assertEqual(
            plan["objective_order"],
            ["hard_resource_coverage", "maximum_cost_performance"],
        )
        self.assertGreater(plan["cost_performance_ratio"], 0)
        self.assertTrue(all(row["prompt_modules"] for row in plan["selected"].values()))
        self.assertTrue(all(row["resource_profile_id"].startswith("params-") for row in plan["selected"].values()))

    def test_call_budget_is_ceiling_not_team_mode(self):
        run = self.run_config("test", Path("out"))
        self.assertEqual(resource_call_budget.total_model_calls_from_env(run, {}), 16)
        self.assertEqual(resource_call_budget.total_model_calls_from_env(run, {"TOTAL_MODEL_CALLS": "9"}), 9)


if __name__ == "__main__":
    unittest.main()
