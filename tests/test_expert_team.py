import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "open-model-market" / "expert_team.py"
SPEC = importlib.util.spec_from_file_location("expert_team", MODULE_PATH)
expert_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = expert_team
SPEC.loader.exec_module(expert_team)

import direct_calls  # noqa: E402
import reasoning_policy  # noqa: E402
import response_audit  # noqa: E402
import seat_scoring  # noqa: E402
from model_market import ModelInfo  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "models.json"
CONFIG = ROOT / "open-model-market" / "config.json"
FORBIDDEN = {"tools", "tool_choice", "plugins", "web_search_options", "file_search", "models"}
OPTIONAL_PARAMETERS = {"max_tokens", "max_completion_tokens", "temperature", "reasoning", "verbosity"}


class ExpertTeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / "history.json"
        self.env = mock.patch.dict(os.environ, {"MODEL_HISTORY_PATH": str(self.history)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def parse(self, task="Compare a software investment with technical and financial risks", *extra):
        return expert_team.build_parser().parse_args([
            "--task", task,
            "--config", str(CONFIG),
            "--catalog-file", str(FIXTURE),
            "--output-dir", str(Path(self.temp.name) / "out"),
            *extra,
        ])

    def prepare(self, task="Compare a software investment with technical and financial risks", *extra):
        run = expert_team.build_run_config(self.parse(task, *extra))
        profile = expert_team.classify_task(run.task, run)
        models, source = expert_team.fetch_catalog(run)
        ranked = expert_team.rank_models(models, profile, run)
        experts, judge, estimate = expert_team.select_team(ranked, profile, run)
        return run, profile, models, source, ranked, experts, judge, estimate

    def test_task_matrix_provider_diverse_selection_and_capability_floor(self):
        run, profile, _, _, ranked, experts, judge, estimate = self.prepare("复杂商业、代码和风险建模比较")
        by_id = {model.id: model for model in ranked}
        self.assertGreaterEqual(len(experts), 1)
        self.assertLessEqual(len(experts), 4)
        self.assertEqual(experts[0].seat_key, "primary")
        self.assertIn("red", {item.seat_key for item in experts})
        authors = {by_id[item.model_id].author for item in experts} | {by_id[judge.model_id].author}
        self.assertEqual(len(authors), len(experts) + 1)
        self.assertGreater(estimate, 0)
        chosen = [by_id[item.model_id] for item in experts] + [by_id[judge.model_id]]
        self.assertTrue(all(model.ranks["intelligence-high-to-low"] <= seat_scoring.MAX_INTELLIGENCE_RANK for model in chosen))
        self.assertTrue(all(not seat_scoring._is_explicitly_small(model) for model in chosen))
        self.assertTrue(all(model.max_completion_tokens > 0 for model in chosen))
        self.assertTrue(all("任务矩阵+CP-SAT" in item.selection_reason for item in experts))
        optimization = json.loads((run.output_dir / "team-optimization.json").read_text(encoding="utf-8"))
        matrix = optimization["task_matrix"]
        self.assertFalse(matrix["history_input_used"])
        self.assertFalse(matrix["fixed_team_mode_used"])
        self.assertFalse(matrix["fixed_parameter_template_used"])
        self.assertEqual(optimization["expert_count"], len(experts))
        self.assertEqual(optimization["team_pattern"], f"{len(experts)}-experts-plus-one-judge")

    def test_models_and_professions_change_with_task(self):
        *_, coding_experts, coding_judge, _ = self.prepare("审计Python软件仓库的架构、可靠性和安全问题")
        *_, business_experts, business_judge, _ = self.prepare("评估餐饮企业的投资、利润和竞争战略")
        self.assertNotEqual([item.profession for item in coding_experts], [item.profession for item in business_experts])
        self.assertNotEqual([item.model_id for item in coding_experts], [item.model_id for item in business_experts])
        self.assertNotEqual(coding_judge.profession, business_judge.profession)

    def test_high_stakes_excludes_unstable_and_small_models(self):
        *_, ranked, experts, judge, _ = self.prepare("分析外交战争制裁和重大升级风险")
        by_id = {model.id: model for model in ranked}
        chosen = [by_id[item.model_id] for item in experts] + [by_id[judge.model_id]]
        self.assertTrue(all("spark" not in model.id and "preview" not in model.id for model in chosen))
        self.assertTrue(all(not seat_scoring._is_explicitly_small(model) for model in chosen))

    def test_requests_forbid_tools_and_send_only_supported_optional_parameters(self):
        run, profile, _, _, ranked, experts, judge, _ = self.prepare("复杂国际关系博弈的短期中期长期目标和反证")
        by_id = {model.id: model for model in ranked}
        pairs = [(direct_calls.build_expert_payload(run, profile, item, by_id[item.model_id]), by_id[item.model_id]) for item in experts]
        pairs.append((direct_calls.build_judge_payload(run, profile, judge, by_id[judge.model_id], []), by_id[judge.model_id]))
        for payload, model in pairs:
            self.assertFalse(FORBIDDEN.intersection(payload))
            self.assertNotIn(":online", payload["model"])
            self.assertNotIn("max_tokens", payload)
            self.assertNotIn("max_completion_tokens", payload)
            supported = set(model.supported_parameters)
            for key in OPTIONAL_PARAMETERS.intersection(payload):
                self.assertIn(key, supported)
            if "reasoning" in payload:
                self.assertIn(payload["reasoning"]["effort"], {"low", "medium", "high"})
                self.assertTrue(payload["reasoning"]["exclude"])

    def test_all_substantive_seats_omit_request_token_ceilings(self):
        run, profile, _, _, ranked, experts, judge, _ = self.prepare("分析复杂商业架构风险")
        by_id = {model.id: model for model in ranked}
        expert_plans = [
            reasoning_policy.expert_inference_plan(run, profile, expert, by_id[expert.model_id])
            for expert in experts
        ]
        judge_plan = reasoning_policy.judge_inference_plan(run, profile, judge, by_id[judge.model_id])
        for plan in expert_plans + [judge_plan]:
            evidence = plan.evidence()
            self.assertFalse(evidence["request_token_ceiling_sent"])
            self.assertFalse(evidence["reasoning_token_ceiling_sent"])
            self.assertEqual(evidence["output_token_policy"], "provider-model-limit-only")
            self.assertNotIn("max_tokens", evidence)
            self.assertGreater(evidence["provider_max_completion_tokens"], 0)
            self.assertIn(plan.effort, {"low", "medium", "high"})

    def test_unsupported_temperature_is_omitted_and_unbounded_reasoning_is_low(self):
        run, profile, _, _, _, experts, _, _ = self.prepare("分析复杂商业架构风险")
        model = ModelInfo(
            id="example/no-temperature",
            name="No Temperature",
            description="general reasoning analysis",
            author="example",
            context_length=131072,
            max_completion_tokens=12000,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
            supported_parameters=["max_tokens", "reasoning"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={},
        )
        plan = reasoning_policy.expert_inference_plan(run, profile, experts[0], model)
        payload = {"model": model.id, "max_tokens": 10000}
        reasoning_policy.apply_plan(payload, plan, model)
        self.assertNotIn("temperature", payload)
        self.assertIn(payload["reasoning"]["effort"], {"low", "medium", "high"})
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)

    def test_model_below_old_output_floor_is_allowed_without_request_cap(self):
        run, profile, _, _, _, experts, _, _ = self.prepare("分析复杂商业架构风险")
        model = ModelInfo(
            id="example/short-output",
            name="Short Output",
            description="general reasoning analysis",
            author="example",
            context_length=131072,
            max_completion_tokens=8000,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
            supported_parameters=["max_tokens"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
        )
        plan = reasoning_policy.expert_inference_plan(run, profile, experts[0], model)
        payload = {"model": model.id}
        reasoning_policy.apply_plan(payload, plan, model)
        self.assertEqual(plan.max_tokens, 8000)
        self.assertNotIn("max_tokens", payload)

    def test_legacy_budget_constraint_is_accepted_but_ignored(self):
        run, *_ = self.prepare("复杂商业风险评估", "--max-estimated-cost-usd", "0.000001")
        self.assertIsNone(run.max_estimated_cost_usd)

    def test_dry_run_publishes_full_ranking_manifest_and_dynamic_candidates(self):
        run, profile, _, source, ranked, experts, judge, estimate = self.prepare("Compare coding, business and risk", "--dry-run")
        direct_calls.write_selection_artifacts(run, profile, source, ranked, experts, judge, estimate)
        direct_calls.write_dry_run_artifacts(run, profile, ranked, experts, judge, estimate)
        selection = json.loads((run.output_dir / "model-selection.json").read_text())
        dry = json.loads((run.output_dir / "expert-team-dry-run.json").read_text())
        optimization = json.loads((run.output_dir / "team-optimization.json").read_text())
        self.assertEqual(len(selection["ranking"]), len(ranked))
        self.assertEqual(len(selection["seat_candidates"]), len(experts) + 1)
        self.assertTrue(all(len(items) <= run.candidate_pool_per_seat for items in selection["seat_candidates"].values()))
        self.assertEqual(len(dry["expert_requests"]), len(experts))
        self.assertEqual(optimization["expert_count"], len(experts))
        self.assertEqual(optimization["optimizer"], "google-or-tools-cp-sat")
        self.assertTrue((run.output_dir / "artifact-manifest.json").exists())

    def test_substantial_length_answer_is_recovered_as_partial(self):
        run = expert_team.build_run_config(self.parse())
        response = {
            "id": "partial-1",
            "model": "alpha/prime",
            "choices": [{"finish_reason": "length", "message": {"content": "完整要点。" * 100}}],
            "usage": {"completion_tokens": 3000},
        }
        result = direct_calls.ExpertResult(
            "core", "核心主研席", "测试专家", "alpha/prime", "alpha/prime", "fixture",
            "failed", None, "partial-1", "length", "length", {}, 0.01, 1.0,
            [{"sanitized_response": response}],
        )
        recovered = list(expert_team._recover_substantial_partials(run, [result]))
        self.assertEqual(recovered[0].status, "success_partial")
        self.assertGreaterEqual(len(recovered[0].answer), expert_team.MIN_USABLE_PARTIAL_CHARS)
        self.assertTrue(recovered[0].attempts[0]["recovered_as_usable_partial"])

    def test_tiny_length_fragment_remains_failed(self):
        run = expert_team.build_run_config(self.parse())
        response = {
            "id": "partial-small",
            "model": "alpha/prime",
            "choices": [{"finish_reason": "length", "message": {"content": "过短片段" * 10}}],
            "usage": {},
        }
        result = direct_calls.ExpertResult(
            "core", "核心主研席", "测试专家", "alpha/prime", "alpha/prime", "fixture",
            "failed", None, "partial-small", "length", "length", {}, 0.01, 1.0,
            [{"sanitized_response": response}],
        )
        recovered = list(expert_team._recover_substantial_partials(run, [result]))
        self.assertEqual(recovered[0].status, "failed")

    def test_response_length_is_not_claimed_complete(self):
        response = {"id": "x", "model": "alpha/prime", "choices": [{"finish_reason": "length", "message": {"content": "partial"}}], "usage": {}}
        self.assertEqual(response_audit.extract_answer(response), "partial")
        self.assertEqual(response_audit.diagnostics(response)["finish_reason"], "length")

    def test_model_post_retries_are_disabled(self):
        run = expert_team.build_run_config(self.parse())
        self.assertEqual(run.model_max_retries, 0)
        self.assertGreaterEqual(run.catalog_max_retries, 1)

    def test_nonfinite_legacy_budget_is_ignored(self):
        run = expert_team.build_run_config(self.parse("test", "--max-estimated-cost-usd", "nan"))
        self.assertIsNone(run.max_estimated_cost_usd)


if __name__ == "__main__":
    unittest.main()
