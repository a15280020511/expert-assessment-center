import argparse
import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import runtime_guards  # noqa: E402,F401 - installs the unified no-limit runtime policy
import task_router  # noqa: E402

CONFIG = ROOT / "open-model-market" / "config.json"


class TaskRouterTests(unittest.TestCase):
    def run_config(self, output_dir: Path, task: str, *, budget=1.0, dry_run=False):
        args = argparse.Namespace(
            task=task,
            config=str(CONFIG),
            output_dir=str(output_dir),
            quality_tier="value",
            ranking_limit=None,
            max_estimated_cost_usd=str(budget),
            max_completion_tokens=None,
            reasoning_effort=None,
            catalog_file=None,
            require_live_catalog=False,
            dry_run=dry_run,
        )
        return replace(model_market.build_run_config(args), api_key="test-key")

    @staticmethod
    def profile(*, domains=None, primary="general", secondary="general", complexity="simple", high=False):
        domains = domains or [primary]
        return model_market.TaskProfile(
            domains=domains,
            primary_domain=primary,
            secondary_domain=secondary,
            complexity=complexity,
            complexity_score={"simple": 0, "medium": 2, "complex": 5}[complexity],
            high_stakes=high,
            chinese=True,
            long_context=False,
            requested_context=16384,
        )

    @staticmethod
    def model(author="router", rank=10, price=0.5):
        model = model_market.ModelInfo(
            id=f"{author}/route-model",
            name="Route Model",
            description="general reasoning structured analysis",
            author=author,
            context_length=131072,
            max_completion_tokens=4096,
            prompt_price_per_million=price,
            completion_price_per_million=price,
            supported_parameters=["max_tokens", "temperature", "reasoning", "structured_outputs", "response_format"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        model.ranks = {"intelligence-high-to-low": rank}
        model.components = {"quality": 0.8, "history": 0.55}
        return model

    def setUp(self):
        self.policy = model_market.load_json(model_market.POLICY_FILE)
        self.routing = task_router.RoutingConfig(True, 0.68, 0.82, 900, 0.20, 30)

    def test_clear_task_uses_deterministic_route_without_paid_call(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(Path(temp), "修复Python API接口中的软件代码bug并补充测试")
            profile = self.profile(domains=["coding"], primary="coding", secondary="coding", complexity="medium")
            with mock.patch.object(task_router, "call_model") as call:
                outcome = task_router.route_task(run, profile, [self.model()], self.policy, self.routing, 5)
            call.assert_not_called()
            self.assertEqual(outcome.status, "deterministic_confident")
            self.assertFalse(outcome.call_consumed)

    def test_ambiguous_cross_domain_task_uses_one_semantic_router_call(self):
        task = "评估制裁升级、供应链中断、投资融资收紧下企业三年战略，并进行风险推演和政策分析"
        semantic = {
            "primary_domain": "business",
            "secondary_domains": ["supply_chain", "international_relations"],
            "complexity": "complex",
            "high_stakes": True,
            "required_capabilities": ["企业战略", "供应链韧性", "地缘政策风险"],
            "confidence": 0.91,
            "reason": "跨商业、供应链和国际关系领域。",
        }
        response = {
            "id": "route-1",
            "model": "router/route-model",
            "provider": "router",
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(semantic, ensure_ascii=False)}}],
            "usage": {"cost": 0.01, "prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        }
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(Path(temp), task)
            initial = self.profile(
                domains=["business", "research", "international_relations"],
                primary="business",
                secondary="research",
                complexity="complex",
                high=True,
            )
            with mock.patch.object(task_router, "call_model", return_value=(response, 0.1)) as call:
                outcome = task_router.route_task(run, initial, [self.model()], self.policy, self.routing, 5)
            call.assert_called_once()
            self.assertEqual(outcome.status, "semantic_success")
            self.assertTrue(outcome.call_consumed)
            self.assertTrue(outcome.semantic_profile_used)
            self.assertEqual(outcome.profile.primary_domain, "business")
            self.assertEqual(outcome.profile.secondary_domain, "supply_chain")
            self.assertIn("地缘政策风险", outcome.required_capabilities)

    def test_four_call_budget_forces_deterministic_fallback_without_hidden_call(self):
        task = "评估制裁、供应链、投资和政策风险的综合影响"
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(Path(temp), task)
            profile = self.profile(
                domains=["international_relations", "supply_chain", "business"],
                primary="international_relations",
                secondary="supply_chain",
                complexity="complex",
                high=True,
            )
            with mock.patch.object(task_router, "call_model") as call:
                outcome = task_router.route_task(run, profile, [self.model()], self.policy, self.routing, 4)
            call.assert_not_called()
            self.assertEqual(outcome.status, "skipped_no_call_budget")
            self.assertFalse(outcome.call_consumed)

    def test_router_cannot_answer_task_or_choose_models(self):
        invalid = {
            "primary_domain": "business",
            "secondary_domains": ["supply_chain"],
            "complexity": "complex",
            "high_stakes": True,
            "required_capabilities": ["使用GPT-5模型"],
            "confidence": 0.9,
            "reason": "route",
            "answer": "应当立即调整战略",
        }
        response = {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(invalid, ensure_ascii=False)}}],
            "usage": {"cost": 0.01},
        }
        task = "评估制裁、供应链、投资和政策风险的综合影响"
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(Path(temp), task)
            profile = self.profile(
                domains=["international_relations", "supply_chain", "business"],
                primary="international_relations",
                secondary="supply_chain",
                complexity="complex",
                high=True,
            )
            with mock.patch.object(task_router, "call_model", return_value=(response, 0.1)):
                outcome = task_router.route_task(run, profile, [self.model()], self.policy, self.routing, 5)
            self.assertEqual(outcome.status, "semantic_failed_deterministic_fallback")
            self.assertTrue(outcome.call_consumed)
            self.assertFalse(outcome.semantic_profile_used)
            self.assertEqual(outcome.profile, profile)
            self.assertIn("forbidden fields", outcome.error)

    def test_semantic_router_cannot_lower_deterministic_safety_level(self):
        initial = self.profile(
            domains=["legal", "business"],
            primary="legal",
            secondary="business",
            complexity="complex",
            high=True,
        )
        semantic = {
            "primary_domain": "business",
            "secondary_domains": [],
            "complexity": "simple",
            "high_stakes": False,
            "required_capabilities": ["商业分析"],
            "confidence": 0.9,
            "reason": "route",
        }
        refined = task_router._refine_profile(initial, semantic)
        self.assertEqual(refined.complexity, "complex")
        self.assertTrue(refined.high_stakes)

    def test_call_allocation_reserves_router_before_replacements(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.run_config(Path(temp), "综合跨领域任务", budget=1.0)
            profile = self.profile(complexity="complex", high=True)
            outcome = task_router.RoutingOutcome(
                profile=profile,
                deterministic_confidence=0.4,
                trigger_reasons=["ambiguous"],
                attempted=True,
                semantic_profile_used=True,
                call_consumed=True,
                model_id="router/model",
                estimated_cost_usd=0.02,
                actual_cost_usd=0.01,
                budget_reservation_usd=0.025,
                status="semantic_success",
                error="",
                response_diagnostics={},
                response={},
                required_capabilities=["route"],
                semantic_confidence=0.9,
            )
            adjusted = task_router.execution_run_after_routing(run, outcome, 5)
            self.assertEqual(adjusted.maximum_replacements, 0)
            self.assertIsNone(adjusted.max_estimated_cost_usd)
            adjusted_six = task_router.execution_run_after_routing(run, outcome, 6)
            self.assertEqual(adjusted_six.maximum_replacements, 1)
            self.assertIsNone(adjusted_six.max_estimated_cost_usd)


if __name__ == "__main__":
    unittest.main()
