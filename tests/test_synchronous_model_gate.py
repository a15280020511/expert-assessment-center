import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
import runtime_guards  # noqa: E402
import seat_scoring  # noqa: E402


class SynchronousModelGateTests(unittest.TestCase):
    @staticmethod
    def model(model_id: str, rank: int, *, history: float = 0.8):
        author = model_id.split("/", 1)[0]
        model = model_market.ModelInfo(
            id=model_id,
            name=model_id,
            description="general reasoning analysis risk audit",
            author=author,
            context_length=131072,
            max_completion_tokens=12000,
            prompt_price_per_million=1.0,
            completion_price_per_million=1.0,
            supported_parameters=["reasoning", "structured_outputs"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={"supports_max_tokens": True},
        )
        model.ranks = {"intelligence-high-to-low": rank}
        model.components = {"history": history, "quality": 1.0 - rank / 100.0}
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

    def test_endpoint_gate_rejects_router_online_and_batch_variants(self):
        self.assertFalse(runtime_guards._is_synchronous_direct_model(self.model("openrouter/auto", 1)))
        self.assertFalse(runtime_guards._is_synchronous_direct_model(self.model("vendor/model:online", 1)))
        self.assertFalse(runtime_guards._is_synchronous_direct_model(self.model("vendor/model:batch", 1)))
        self.assertTrue(runtime_guards._is_synchronous_direct_model(self.model("vendor/model", 1)))

    def test_initial_stable_pool_excludes_batch_only_model_even_when_ranked_first(self):
        batch = self.model("openai/gpt-5.4-nano:batch", 1)
        direct = [
            self.model("provider-a/model-a", 2),
            self.model("provider-b/model-b", 3),
            self.model("provider-c/model-c", 4),
            self.model("provider-d/model-d", 5),
        ]
        pool = seat_scoring._stable_pool([batch, *direct], self.profile())
        self.assertNotIn(batch.id, {model.id for model in pool})
        self.assertEqual({model.id for model in pool}, {model.id for model in direct})

    def test_replacement_candidates_never_return_batch_only_model(self):
        batch = self.model("openai/gpt-5.4-nano:batch", 1)
        direct = [
            self.model("provider-a/model-a", 2),
            self.model("provider-b/model-b", 3),
            self.model("provider-c/model-c", 4),
            self.model("provider-d/model-d", 5),
        ]
        expert = model_market.SelectedExpert(
            "core",
            "核心分析席",
            "通用分析专家",
            "general",
            "完成核心分析",
            "failed/model",
            "test",
        )
        candidates = runtime_guards._policy_aware_replacement_candidates(
            [batch, *direct],
            self.profile(),
            expert,
            used_ids=set(),
            used_authors=set(),
            tier="value",
        )
        self.assertTrue(candidates)
        self.assertNotIn(batch.id, {model.id for model in candidates})


if __name__ == "__main__":
    unittest.main()
