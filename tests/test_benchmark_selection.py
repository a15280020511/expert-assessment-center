import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import benchmark_selection  # noqa: E402
import model_market  # noqa: E402


class BenchmarkSelectionTests(unittest.TestCase):
    @staticmethod
    def model(model_id, name):
        return model_market.ModelInfo(
            id=model_id,
            name=name,
            description="reasoning analysis",
            author=model_id.split("/", 1)[0],
            context_length=131072,
            max_completion_tokens=8192,
            prompt_price_per_million=1.0,
            completion_price_per_million=2.0,
            supported_parameters=["reasoning", "max_tokens"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            reasoning={},
        )

    def test_exact_permaslug_is_not_duplicated(self):
        payload = {
            "data": [{"model_permaslug": "openai/gpt-5", "display_name": "GPT-5", "intelligence_index": 90}],
            "meta": {"model_count": 1},
        }
        result = benchmark_selection.augment_benchmark_payload(payload, [self.model("openai/gpt-5", "GPT-5")])
        self.assertEqual(len(result["data"]), 1)
        audit = result["meta"]["alias_resolution"]
        self.assertEqual(audit["methods"]["exact_model_permaslug"], 1)
        self.assertEqual(audit["added_alias_count"], 0)

    def test_versioned_permaslug_resolves_to_request_id(self):
        payload = {
            "data": [
                {
                    "model_permaslug": "deepseek/deepseek-v4-flash-2026-07-20",
                    "display_name": "DeepSeek V4 Flash",
                    "intelligence_index": 76,
                }
            ],
            "meta": {"model_count": 1},
        }
        result = benchmark_selection.augment_benchmark_payload(
            payload,
            [self.model("deepseek/deepseek-v4-flash", "DeepSeek: DeepSeek V4 Flash")],
        )
        aliases = [row for row in result["data"] if row["model_permaslug"] == "deepseek/deepseek-v4-flash"]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["intelligence_index"], 76)
        self.assertIn(aliases[0]["resolution_method"], {"slug_key", "unique_display_name"})

    def test_unique_display_name_resolves_different_slug(self):
        payload = {
            "data": [
                {
                    "model_permaslug": "anthropic/claude-fable-5-20260715",
                    "display_name": "Claude Fable 5",
                    "intelligence_index": 88,
                }
            ],
            "meta": {"model_count": 1},
        }
        result = benchmark_selection.augment_benchmark_payload(
            payload,
            [self.model("anthropic/claude-fable-5", "Anthropic: Claude Fable 5")],
        )
        aliases = [row for row in result["data"] if row["model_permaslug"] == "anthropic/claude-fable-5"]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["resolved_from_permaslug"], "anthropic/claude-fable-5-20260715")

    def test_ambiguous_display_name_is_not_resolved(self):
        payload = {
            "data": [
                {"model_permaslug": "vendor-a/same", "display_name": "Same Model", "intelligence_index": 80},
                {"model_permaslug": "vendor-b/same", "display_name": "Same Model", "intelligence_index": 70},
            ],
            "meta": {"model_count": 2},
        }
        result = benchmark_selection.augment_benchmark_payload(
            payload,
            [self.model("vendor-c/same-current", "Same Model")],
        )
        self.assertFalse(any(row["model_permaslug"] == "vendor-c/same-current" for row in result["data"]))
        self.assertEqual(result["meta"]["alias_resolution"]["unresolved_count"], 1)

    def test_input_payload_is_not_mutated(self):
        payload = {
            "data": [{"model_permaslug": "x/model-20260720", "display_name": "Model X", "intelligence_index": 50}],
            "meta": {"model_count": 1},
        }
        original = repr(payload)
        benchmark_selection.augment_benchmark_payload(payload, [self.model("x/model", "Model X")])
        self.assertEqual(repr(payload), original)


if __name__ == "__main__":
    unittest.main()
