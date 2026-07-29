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
import seat_scoring  # noqa: E402

CONFIG = ROOT / "open-model-market" / "config.json"


class ValueFirstSelectionTests(unittest.TestCase):
    def run_config(self, output_dir: Path, *, api_key=None):
        args = argparse.Namespace(
            task="评估一个通用分析方案",
            config=str(CONFIG),
            output_dir=str(output_dir),
            quality_tier="value",
            ranking_limit=None,
            max_estimated_cost_usd=None,
            max_completion_tokens=None,
            reasoning_effort=None,
            catalog_file=None,
            require_live_catalog=False,
            dry_run=False,
        )
        return replace(model_market.build_run_config(args), api_key=api_key)

    @staticmethod
    def model(author: str, rank: int, prompt: float, completion: float):
        model = model_market.ModelInfo(
            id=f"{author}/model-{rank}",
            name=f"Model {rank}",
            description="general reasoning analysis business risk audit",
            author=author,
            context_length=131072,
            max_completion_tokens=12000,
            prompt_price_per_million=prompt,
            completion_price_per_million=completion,
            supported_parameters=["max_tokens", "reasoning", "structured_outputs"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        model.ranks = {"intelligence-high-to-low": rank}
        model.components = {"history": 0.8, "quality": 1.0 - rank / 100.0}
        return model

    @staticmethod
    def profile():
        return model_market.TaskProfile(
            domains=["general"],
            primary_domain="general",
            secondary_domain="general",
            complexity="simple",
            complexity_score=0,
            high_stakes=False,
            chinese=True,
            long_context=False,
            requested_context=16384,
        )

    def test_value_tier_prefers_benchmark_points_per_price(self):
        cheap = self.model("cheap", 20, 0.5, 0.5)
        expensive = self.model("expensive", 1, 10.0, 10.0)
        cheap.benchmark_scores = {"intelligence_index": 70.0}
        expensive.benchmark_scores = {"intelligence_index": 90.0}
        cheap.benchmark_source = expensive.benchmark_source = "test"

        self.assertGreater(
            seat_scoring._value_index(cheap, "general"),
            seat_scoring._value_index(expensive, "general"),
        )
        self.assertLess(
            seat_scoring._priority_key(cheap, "core", "general", "value"),
            seat_scoring._priority_key(expensive, "core", "general", "value"),
        )

    def test_quality_tier_still_prefers_absolute_benchmark(self):
        cheap = self.model("cheap", 20, 0.5, 0.5)
        expensive = self.model("expensive", 1, 10.0, 10.0)
        cheap.benchmark_scores = {"intelligence_index": 70.0}
        expensive.benchmark_scores = {"intelligence_index": 90.0}
        cheap.benchmark_source = expensive.benchmark_source = "test"
        self.assertLess(
            seat_scoring._priority_key(expensive, "core", "general", "quality"),
            seat_scoring._priority_key(cheap, "core", "general", "quality"),
        )

    def test_benchmark_endpoint_is_read_and_missing_rows_are_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            run = self.run_config(output, api_key="test-key")
            direct = self.model("direct", 2, 1.0, 2.0)
            fallback = self.model("fallback", 10, 1.0, 2.0)
            payload = {
                "data": [{
                    "model_permaslug": direct.id,
                    "intelligence_index": 82.0,
                    "coding_index": 74.0,
                    "agentic_index": 69.0,
                }],
                "meta": {"as_of": "2026-07-26T00:00:00Z", "source": "test"},
            }
            with mock.patch.object(seat_scoring, "request_json", return_value=payload) as call:
                evidence = seat_scoring._enrich_benchmarks(run, [direct, fallback])

            call.assert_called_once()
            self.assertEqual(evidence["direct_benchmark_count"], 1)
            self.assertEqual(evidence["fallback_count"], 1)
            self.assertTrue(evidence["degraded"])
            self.assertEqual(direct.benchmark_source, "openrouter-benchmarks")
            self.assertEqual(fallback.benchmark_source, "intelligence-rank-fallback")
            stored = json.loads((output / "benchmark-market.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["meta"]["as_of"], "2026-07-26T00:00:00Z")

    def test_seat_candidate_evidence_matches_actual_provider_diverse_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            run = self.run_config(output, api_key="test-key")
            models = [
                self.model("maker1", 1, 8.0, 8.0),
                self.model("maker2", 5, 1.0, 1.0),
                self.model("maker3", 10, 2.0, 2.0),
                self.model("maker4", 20, 0.5, 0.5),
                self.model("maker5", 30, 3.0, 3.0),
            ]
            payload = {
                "data": [
                    {
                        "model_permaslug": model.id,
                        "intelligence_index": score,
                        "coding_index": score,
                        "agentic_index": score,
                    }
                    for model, score in zip(models, [95.0, 80.0, 75.0, 65.0, 60.0])
                ],
                "meta": {"as_of": "2026-07-26T00:00:00Z"},
            }
            with mock.patch.object(seat_scoring, "request_json", return_value=payload):
                experts, judge, _ = seat_scoring.select_team(models, self.profile(), run)

            evidence = seat_scoring.top_candidates_for_evidence(models, self.profile(), run, 3)
            selected = {expert.seat_key: expert.model_id for expert in experts}
            selected["judge"] = judge.model_id
            for seat, model_id in selected.items():
                self.assertTrue(evidence[seat])
                self.assertEqual(evidence[seat][0]["model"], model_id)
                self.assertTrue(evidence[seat][0]["selected"])

            value = json.loads((output / "value-selection.json").read_text(encoding="utf-8"))
            self.assertEqual(value["primary_rule"], "seat-qualified-cost-performance-first")
            self.assertEqual(len(value["selected"]), 4)


if __name__ == "__main__":
    unittest.main()
