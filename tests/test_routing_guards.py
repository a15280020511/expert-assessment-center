import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import routing_guards  # noqa: E402
import task_router  # noqa: E402


class RoutingGuardTests(unittest.TestCase):
    @staticmethod
    def profile(primary="business"):
        return model_market.TaskProfile(
            domains=[primary],
            primary_domain=primary,
            secondary_domain=primary,
            complexity="complex",
            complexity_score=5,
            high_stakes=True,
            chinese=True,
            long_context=False,
            requested_context=16384,
        )

    def failed_model_name_outcome(self, capabilities):
        semantic = {
            "primary_domain": "business",
            "secondary_domains": ["supply_chain", "international_relations", "security"],
            "complexity": "complex",
            "high_stakes": True,
            "required_capabilities": capabilities,
            "confidence": 0.85,
            "reason": "跨商业、供应链、国际关系和安全领域。",
        }
        return task_router.RoutingOutcome(
            profile=self.profile("research"),
            deterministic_confidence=0.18,
            trigger_reasons=["ambiguous"],
            attempted=True,
            semantic_profile_used=False,
            call_consumed=True,
            model_id="deepseek/deepseek-v4-flash",
            estimated_cost_usd=0.0004,
            actual_cost_usd=0.0002,
            budget_reservation_usd=0.0005,
            status="semantic_failed_deterministic_fallback",
            error="Semantic router must not choose or name models.",
            response_diagnostics={"finish_reason": "stop"},
            response={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(semantic, ensure_ascii=False),
                        }
                    }
                ]
            },
            required_capabilities=[],
            semantic_confidence=None,
        )

    def test_evidence_keywords_are_not_used_for_task_classification(self):
        task = (
            "评估一家餐饮企业的经营方案"
            "\n\n用户提供的证据目录（专家禁止联网；未附正文时不得声称已读取或核验 URL 内容）："
            "\n- [1] 来源=测试；说明=战争 制裁 医疗 法律 网络安全 供应链"
            "\n- 证据边界：仅为线索。"
        )
        stripped = routing_guards.strip_evidence_for_classification(task)
        self.assertEqual(stripped, "评估一家餐饮企业的经营方案")
        self.assertNotIn("战争", stripped)
        self.assertNotIn("供应链", stripped)

    def test_execution_note_is_not_used_for_task_classification(self):
        task = "修复Python代码\n\n用户提供的执行说明：\n这里提到医疗、战争、投资和供应链"
        self.assertEqual(routing_guards.strip_evidence_for_classification(task), "修复Python代码")

    def test_low_semantic_confidence_falls_back_but_keeps_call_audit(self):
        deterministic = self.profile("business")
        semantic = self.profile("international_relations")
        outcome = task_router.RoutingOutcome(
            profile=semantic,
            deterministic_confidence=0.40,
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
            response_diagnostics={"finish_reason": "stop"},
            response={"id": "route-1"},
            required_capabilities=["地缘风险"],
            semantic_confidence=0.44,
        )
        guarded = routing_guards.enforce_semantic_confidence(outcome, deterministic, 0.65)
        self.assertEqual(guarded.status, "semantic_low_confidence_deterministic_fallback")
        self.assertEqual(guarded.profile, deterministic)
        self.assertFalse(guarded.semantic_profile_used)
        self.assertTrue(guarded.call_consumed)
        self.assertEqual(guarded.actual_cost_usd, 0.01)
        self.assertEqual(guarded.model_id, "router/model")
        self.assertEqual(guarded.required_capabilities, [])

    def test_high_semantic_confidence_is_kept(self):
        profile = self.profile("business")
        outcome = task_router.RoutingOutcome(
            profile=profile,
            deterministic_confidence=0.40,
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
            required_capabilities=["商业分析"],
            semantic_confidence=0.90,
        )
        self.assertEqual(routing_guards.enforce_semantic_confidence(outcome, profile, 0.65), outcome)

    def test_financial_modeling_is_not_a_concrete_model_name(self):
        deterministic = self.profile("research")
        outcome = self.failed_model_name_outcome(
            ["strategic analysis", "supply chain risk assessment", "financial modeling"]
        )
        guarded = routing_guards.enforce_semantic_confidence(outcome, deterministic, 0.65)
        self.assertEqual(guarded.status, "semantic_success_validation_recovered")
        self.assertTrue(guarded.semantic_profile_used)
        self.assertTrue(guarded.call_consumed)
        self.assertEqual(guarded.profile.primary_domain, "business")
        self.assertIn("financial modeling", guarded.required_capabilities)
        self.assertEqual(guarded.semantic_confidence, 0.85)

    def test_explicit_model_family_remains_forbidden(self):
        deterministic = self.profile("research")
        outcome = self.failed_model_name_outcome(["GPT-5 reasoning", "supply chain risk assessment"])
        guarded = routing_guards.enforce_semantic_confidence(outcome, deterministic, 0.65)
        self.assertEqual(guarded.status, "semantic_failed_deterministic_fallback")
        self.assertFalse(guarded.semantic_profile_used)
        self.assertEqual(guarded.profile, outcome.profile)
        self.assertEqual(guarded.error, "Semantic router must not choose or name models.")

    def test_exact_vendor_model_id_remains_forbidden(self):
        deterministic = self.profile("research")
        outcome = self.failed_model_name_outcome(["openai/gpt-5", "financial analysis"])
        guarded = routing_guards.enforce_semantic_confidence(outcome, deterministic, 0.65)
        self.assertEqual(guarded.status, "semantic_failed_deterministic_fallback")
        self.assertFalse(guarded.semantic_profile_used)


if __name__ == "__main__":
    unittest.main()
