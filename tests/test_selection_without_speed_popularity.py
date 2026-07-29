import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import performance_history  # noqa: E402
import seat_scoring  # noqa: E402

CONFIG = ROOT / "open-model-market" / "config.json"


class SelectionWithoutSpeedPopularityTests(unittest.TestCase):
    def run_config(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        args = argparse.Namespace(
            task="评估一个商业与技术方案",
            config=str(CONFIG),
            output_dir=str(Path(temp.name) / "out"),
            quality_tier=None,
            ranking_limit=None,
            max_estimated_cost_usd=None,
            max_completion_tokens=None,
            reasoning_effort=None,
            catalog_file=None,
            require_live_catalog=False,
            dry_run=True,
        )
        return model_market.build_run_config(args)

    @staticmethod
    def model(model_id: str, intelligence_rank: int, popularity_rank: int, speed_rank: int):
        model = model_market.ModelInfo(
            id=model_id,
            name=model_id,
            description="business reasoning analysis risk audit",
            author=model_id.split("/", 1)[0],
            context_length=131072,
            max_completion_tokens=12000,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
            supported_parameters=["max_tokens", "reasoning", "structured_outputs"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        model.ranks = {
            "intelligence-high-to-low": intelligence_rank,
            "pricing-low-to-high": intelligence_rank,
            "top-weekly": popularity_rank,
            "throughput-high-to-low": speed_rank,
            "latency-low-to-high": speed_rank,
        }
        return model

    def test_catalog_does_not_request_speed_or_popularity_rankings(self):
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        sorts = config["selection"]["catalog_sorts"]
        self.assertEqual(sorts, ["intelligence-high-to-low", "pricing-low-to-high"])
        for weights in config["selection"]["weights"].values():
            self.assertEqual(weights["speed"], 0.0)
            self.assertEqual(weights["popularity"], 0.0)

    def test_global_ranking_is_unchanged_by_speed_or_popularity(self):
        run = self.run_config()
        first = {
            f"maker{i}/model": self.model(f"maker{i}/model", i, i, i)
            for i in range(1, 5)
        }
        second = {
            f"maker{i}/model": self.model(f"maker{i}/model", i, 100 - i, 100 - i)
            for i in range(1, 5)
        }
        profile = model_market.classify_task(run.task, run)
        first_scores = {
            model.id: model.score
            for model in model_market.rank_models(first, profile, run)
        }
        second_scores = {
            model.id: model.score
            for model in model_market.rank_models(second, profile, run)
        }
        self.assertEqual(first_scores, second_scores)

    def test_seat_priority_ignores_speed_component(self):
        model = self.model("maker/model", 1, 1, 1)
        model.components = {"history": 0.8, "quality": 0.9, "speed": 0.0}
        slow_keys = {
            (seat, tier): seat_scoring._priority_key(model, seat, "business", tier)
            for seat in ("core", "cross", "red", "judge")
            for tier in ("budget", "value", "quality")
        }
        model.components["speed"] = 1.0
        fast_keys = {
            (seat, tier): seat_scoring._priority_key(model, seat, "business", tier)
            for seat in ("core", "cross", "red", "judge")
            for tier in ("budget", "value", "quality")
        }
        self.assertEqual(slow_keys, fast_keys)
        self.assertTrue(all("速度" not in text for text in seat_scoring.RULE_ORDER.values()))

    def test_history_reliability_ignores_latency(self):
        normal = {
            "calls": 10,
            "successes": 8,
            "empty_answers": 1,
            "truncated": 1,
            "timeouts": 0,
            "avg_actual_to_estimated_cost": 1.0,
            "avg_reasoning_share": 0.4,
            "avg_latency_seconds": 1.0,
        }
        slow = dict(normal, avg_latency_seconds=600.0)
        self.assertEqual(
            performance_history.history_score(normal),
            performance_history.history_score(slow),
        )


if __name__ == "__main__":
    unittest.main()
