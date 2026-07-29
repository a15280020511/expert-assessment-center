import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import benchmark_selection  # noqa: E402
import capability_selection  # noqa: E402
import direct_calls  # noqa: E402
import model_market  # noqa: E402
import seat_scoring  # noqa: E402


class CapabilitySelectionTests(unittest.TestCase):
    @staticmethod
    def profile():
        return model_market.TaskProfile(
            domains=["business", "medical", "legal", "supply_chain"],
            primary_domain="business",
            secondary_domain="medical",
            complexity="complex",
            complexity_score=5,
            high_stakes=True,
            chinese=True,
            long_context=True,
            requested_context=65536,
        )

    @staticmethod
    def model(author, description, *, reasoning=True, bounded=True):
        supported = ["max_tokens", "structured_outputs"]
        if reasoning:
            supported.append("reasoning")
        model = model_market.ModelInfo(
            id=f"{author}/model",
            name=f"{author} Model",
            description=description,
            author=author,
            context_length=131072,
            max_completion_tokens=8192,
            prompt_price_per_million=1.0,
            completion_price_per_million=1.0,
            supported_parameters=supported,
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": bounded} if reasoning else {},
        )
        model.ranks = {"intelligence-high-to-low": 10}
        model.components = {"quality": 0.8, "history": 0.55}
        return model

    @staticmethod
    def selected_experts(core="core/model", cross="cross/model", red="red/model"):
        return [
            model_market.SelectedExpert("core", "核心主研席", "商业战略分析师", "business", "core mission", core, "core reason"),
            model_market.SelectedExpert("cross", "交叉验证席", "医疗交叉验证官", "medical", "cross mission", cross, "cross reason"),
            model_market.SelectedExpert("red", "独立反证席", "安全失效模式审计官", "security", "red mission", red, "red reason"),
        ]

    def test_capabilities_add_security_and_math_domains(self):
        domains = capability_selection.capability_domains(
            self.profile(),
            ["跨境数据与网络安全", "财务建模", "公共政策分析"],
        )
        self.assertIn("security", domains)
        self.assertIn("math", domains)
        self.assertIn("public_policy", domains)

    def test_routed_capabilities_change_red_seat_and_enter_mission(self):
        captured = {}
        judge_model = self.model("judge", "business strategy evidence reasoning")

        def fake_select(ranked, profile, run):
            seats, judge = seat_scoring.build_fixed_seats(profile)
            captured["seats"] = seats
            captured["judge"] = judge
            return [], model_market.SelectedJudge("综合裁决席", judge, judge_model.id, "test"), 0.1

        with mock.patch.object(benchmark_selection, "select_team", side_effect=fake_select):
            capability_selection.select_team(
                [judge_model],
                self.profile(),
                mock.Mock(),
                ["医疗设备合规", "跨境数据与网络安全", "财务建模"],
            )

        by_key = {seat.key: seat for seat in captured["seats"]}
        self.assertEqual(by_key["core"].domain_focus, "business")
        self.assertEqual(by_key["cross"].domain_focus, "medical")
        self.assertEqual(by_key["red"].domain_focus, "security")
        self.assertIn("失效模式", by_key["red"].profession)
        self.assertIn("跨境数据与网络安全", by_key["red"].mission)

    def test_non_coding_pool_excludes_code_specialists_when_diverse(self):
        rows = [
            self.model("coder", "code coding software programming developer"),
            self.model("a", "business strategy finance analysis"),
            self.model("b", "business enterprise investment analysis"),
            self.model("c", "business economics strategy analysis"),
            self.model("d", "business finance market analysis"),
        ]
        pool = capability_selection._filtered_pool(rows, "core", "business")
        self.assertNotIn("coder/model", {model.id for model in pool})
        self.assertGreaterEqual(len({model.author for model in pool}), 4)

    def test_red_pool_requires_risk_fit_when_provider_diverse(self):
        rows = [
            self.model("a", "security risk audit adversarial"),
            self.model("b", "security cyber risk analysis"),
            self.model("c", "security threat audit reasoning"),
            self.model("d", "security safety adversarial analysis"),
            self.model("e", "business strategy finance analysis"),
        ]
        pool = capability_selection._filtered_pool(rows, "red", "security")
        self.assertEqual({model.author for model in pool}, {"a", "b", "c", "d"})

    def test_judge_prefers_bounded_or_no_reasoning_before_unbounded(self):
        safe = self.model("safe", "business strategy analysis", reasoning=True, bounded=True)
        unsafe = self.model("unsafe", "business strategy analysis", reasoning=True, bounded=False)
        original = seat_scoring._priority_key
        captured = {}

        def fake_select(ranked, profile, run):
            captured["safe"] = seat_scoring._priority_key(safe, "judge", "business", "value")
            captured["unsafe"] = seat_scoring._priority_key(unsafe, "judge", "business", "value")
            return [], model_market.SelectedJudge("综合裁决席", "评审官", safe.id, "test"), 0.1

        with mock.patch.object(benchmark_selection, "select_team", side_effect=fake_select):
            capability_selection.select_team([safe, unsafe], self.profile(), mock.Mock(), [])

        self.assertLess(captured["safe"], captured["unsafe"])
        self.assertIs(seat_scoring._priority_key, original)

    def test_selected_reasons_disclose_capability_domain_and_pool_fallback(self):
        models = [
            self.model("core", "general reasoning"),
            self.model("cross", "medical clinical evidence"),
            self.model("red", "security risk audit adversarial"),
            self.model("judge", "business strategy analysis", reasoning=False),
        ]
        experts = self.selected_experts()
        judge = model_market.SelectedJudge("综合裁决席", "决策评审官", "judge/model", "judge reason")
        annotated, annotated_judge = capability_selection._annotate_selected(models, experts, judge)
        by_key = {expert.seat_key: expert for expert in annotated}
        self.assertIn("能力席位领域=business", by_key["core"].selection_reason)
        self.assertIn("专业池不足", by_key["core"].selection_reason)
        self.assertIn("领域专业池命中", by_key["cross"].selection_reason)
        self.assertIn("风险与领域专业池命中", by_key["red"].selection_reason)
        self.assertIn("裁判最终答案安全优先=是", annotated_judge.selection_reason)

    def test_candidate_flags_are_reconciled_to_actual_models(self):
        experts = self.selected_experts()
        judge = model_market.SelectedJudge("综合裁决席", "决策评审官", "judge/model", "judge reason")
        data = {
            "seat_candidates": {
                "core": [{"model": "other/a", "selected": True}, {"model": "core/model", "selected": False}],
                "cross": [{"model": "cross/model", "selected": False}, {"model": "other/b", "selected": True}],
                "red": [{"model": "other/c", "selected": True}, {"model": "red/model", "selected": False}],
                "judge": [{"model": "other/d", "selected": True}, {"model": "judge/model", "selected": False}],
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model-selection.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            capability_selection._reconcile_candidate_flags(
                path,
                experts,
                judge,
                {"seat_domains": {"core": "business", "cross": "medical", "red": "security", "judge": "business"}},
            )
            reconciled = json.loads(path.read_text(encoding="utf-8"))
        expected = {"core": "core/model", "cross": "cross/model", "red": "red/model", "judge": "judge/model"}
        for seat, model_id in expected.items():
            selected = [row["model"] for row in reconciled["seat_candidates"][seat] if row["selected"]]
            self.assertEqual(selected, [model_id])
        self.assertTrue(reconciled["capability_seat_policy"]["candidate_audit_consistent"])

    def test_missing_actual_model_in_candidate_audit_is_a_hard_failure(self):
        experts = self.selected_experts()
        judge = model_market.SelectedJudge("综合裁决席", "决策评审官", "judge/model", "judge reason")
        data = {
            "seat_candidates": {
                "core": [{"model": "other/a", "selected": True}],
                "cross": [{"model": "cross/model", "selected": True}],
                "red": [{"model": "red/model", "selected": True}],
                "judge": [{"model": "judge/model", "selected": True}],
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "model-selection.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(model_market.ExpertTeamError):
                capability_selection._reconcile_candidate_flags(path, experts, judge, {})

    def test_direct_calls_writer_is_bound_before_expert_team_imports_it(self):
        self.assertTrue(getattr(direct_calls.write_selection_artifacts, "_capability_policy_bound", False))


if __name__ == "__main__":
    unittest.main()
