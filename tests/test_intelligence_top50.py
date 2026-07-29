import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import seat_scoring  # noqa: E402
from model_market import ExpertTeamError, ModelInfo, TaskProfile  # noqa: E402


class IntelligenceTop50Tests(unittest.TestCase):
    @staticmethod
    def model(author: str, rank: int) -> ModelInfo:
        model = ModelInfo(
            id=f"{author}/model-{rank}",
            name=f"Model {rank}",
            description="general reasoning analysis",
            author=author,
            context_length=131072,
            max_completion_tokens=8192,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
            supported_parameters=["max_tokens", "reasoning"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        model.ranks = {"intelligence-high-to-low": rank}
        model.components = {"history": 0.55, "quality": 1.0}
        return model

    @staticmethod
    def profile() -> TaskProfile:
        return TaskProfile(
            domains=["general"],
            primary_domain="general",
            secondary_domain="general",
            complexity="simple",
            complexity_score=0,
            high_stakes=False,
            chinese=False,
            long_context=False,
            requested_context=16384,
        )

    def test_rank_50_is_allowed_and_rank_51_is_rejected(self):
        self.assertEqual(seat_scoring.MAX_INTELLIGENCE_RANK, 50)
        self.assertTrue(seat_scoring._within_capability_floor(self.model("maker50", 50)))
        self.assertFalse(seat_scoring._within_capability_floor(self.model("maker51", 51)))
        self.assertTrue(all("前50" in rule for rule in seat_scoring.RULE_ORDER.values()))
        self.assertTrue(all("前100" not in rule for rule in seat_scoring.RULE_ORDER.values()))

    def test_selector_does_not_widen_range_when_top_50_has_fewer_than_four_models(self):
        models = [
            self.model("maker1", 1),
            self.model("maker2", 20),
            self.model("maker3", 50),
            self.model("maker51", 51),
        ]
        with self.assertRaises(ExpertTeamError) as caught:
            seat_scoring._stable_pool(models, self.profile())
        self.assertIn("top 50", str(caught.exception))
        self.assertIn("will not widen", str(caught.exception))

    def test_four_models_inside_top_50_form_the_only_eligible_pool(self):
        models = [
            self.model("maker1", 1),
            self.model("maker2", 15),
            self.model("maker3", 30),
            self.model("maker4", 50),
            self.model("maker51", 51),
        ]
        pool = seat_scoring._stable_pool(models, self.profile())
        self.assertEqual([model.ranks["intelligence-high-to-low"] for model in pool], [1, 15, 30, 50])


if __name__ == "__main__":
    unittest.main()
