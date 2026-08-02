from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
from v5_catalog_view import (  # noqa: E402
    CatalogViewError,
    compact_endpoint_catalog,
    eligible_models,
)


class CatalogEndpointEligibilityTests(unittest.TestCase):
    @staticmethod
    def model(
        *,
        model_id: str = "moonshotai/kimi-k3",
        rank: int = 4,
        model_maximum: int = 0,
    ) -> model_market.ModelInfo:
        return model_market.ModelInfo(
            id=model_id,
            name=model_id,
            description="fixture",
            author=model_id.split("/", 1)[0],
            context_length=1_048_576,
            max_completion_tokens=model_maximum,
            prompt_price_per_million=1.0,
            completion_price_per_million=3.0,
            supported_parameters=["max_tokens", "temperature"],
            input_modalities=["text"],
            output_modalities=["text"],
            knowledge_cutoff=None,
            expiration_date=None,
            ranks={"intelligence-high-to-low": rank},
        )

    def test_unknown_model_level_completion_is_deferred_to_endpoint(self) -> None:
        model = self.model(model_maximum=0)
        rows = eligible_models(
            {model.id: model},
            requested_context=16_384,
            maximum_models=12,
        )
        self.assertEqual([model], rows)

    def test_exact_endpoint_limit_restores_valid_model(self) -> None:
        model = self.model(model_maximum=0)
        catalog = compact_endpoint_catalog(
            [model],
            {
                model.id: {
                    "data": {
                        "endpoints": [
                            {
                                "tag": "morph",
                                "context_length": 1_048_576,
                                "max_completion_tokens": 262_144,
                                "supported_parameters": [
                                    "max_tokens",
                                    "temperature",
                                ],
                                "pricing": {
                                    "prompt": "0.000001",
                                    "completion": "0.000003",
                                },
                            }
                        ]
                    }
                }
            },
            required_context_tokens=16_384,
        )
        endpoint = catalog["endpoints"][0]
        self.assertEqual("moonshotai/kimi-k3", endpoint["model"])
        self.assertEqual("morph", endpoint["provider"])
        self.assertEqual(262_144, endpoint["max_completion_tokens"])

    def test_zero_endpoint_completion_is_rejected(self) -> None:
        model = self.model(
            model_id="x-ai/grok-4.5",
            rank=8,
            model_maximum=0,
        )
        with self.assertRaisesRegex(
            CatalogViewError,
            "no exact constitutionally usable endpoint rows",
        ):
            compact_endpoint_catalog(
                [model],
                {
                    model.id: {
                        "data": {
                            "endpoints": [
                                {
                                    "tag": "xai",
                                    "context_length": 500_000,
                                    "max_completion_tokens": 0,
                                    "supported_parameters": ["max_tokens"],
                                    "pricing": {
                                        "prompt": "0.000002",
                                        "completion": "0.000006",
                                    },
                                }
                            ]
                        }
                    }
                },
                required_context_tokens=16_384,
            )

    def test_endpoint_without_output_limit_parameter_is_rejected(self) -> None:
        model = self.model(model_maximum=262_144)
        with self.assertRaises(CatalogViewError):
            compact_endpoint_catalog(
                [model],
                {
                    model.id: {
                        "data": {
                            "endpoints": [
                                {
                                    "tag": "unsafe-provider",
                                    "context_length": 1_048_576,
                                    "max_completion_tokens": 262_144,
                                    "supported_parameters": ["temperature"],
                                    "pricing": {
                                        "prompt": "0.000001",
                                        "completion": "0.000003",
                                    },
                                }
                            ]
                        }
                    }
                },
                required_context_tokens=16_384,
            )

    def test_explicit_zero_recovery_is_preserved(self) -> None:
        args = argparse.Namespace(
            task="zero recovery regression",
            output_dir="unused",
            config=str(ROOT / "open-model-market" / "config.json"),
            catalog_file=None,
            ranking_limit=24,
            max_completion_tokens=512,
            reasoning_effort="low",
            maximum_total_calls=4,
            maximum_recovery_calls=0,
            require_live_catalog=False,
            dry_run=True,
        )
        run = model_market.build_run_config(args)
        self.assertEqual(0, run.maximum_recovery_calls)
        self.assertEqual(0, run.maximum_replacements)


if __name__ == "__main__":
    unittest.main()
