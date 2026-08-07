from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_price_ranked_pipeline as pipeline  # noqa: E402
from v5_catalog_view import CatalogViewError  # noqa: E402

MODEL_IDS = [
    "openai/gpt-5.6-luna-pro",
    "nex-agi/nex-n2-pro",
    "xiaomi/mimo-v2.5-pro",
    "deepseek/deepseek-v4-pro",
    "amazon/nova-pro-v1",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "google/gemini-2.5-pro",
    "perplexity/sonar-pro",
]


def model_row(model_id: str) -> dict:
    return {
        "id": model_id,
        "context_length": 131072,
        "supported_parameters": ["reasoning", "max_tokens"],
        "pricing": {"prompt": "0.000001", "completion": "0.000003"},
        "top_provider": {"max_completion_tokens": 16000},
    }


def plan() -> dict:
    rows = [
        {
            "model": model_id,
            "company": model_id.split("/", 1)[0],
            "context_length": 131072,
            "max_completion_tokens": 16000,
            "prompt_usd_per_million": 1.0,
            "completion_usd_per_million": 3.0,
        }
        for model_id in MODEL_IDS
    ]
    return {
        "selected_from_top50_reasoning_pool_only": True,
        "optimizer": "ortools-cp-sat",
        "optimizer_audit": {"optimality_proven": True},
        "selected_models": rows[:4],
        "recovery_models": rows[4:],
    }


class PlannedCatalogScopeTests(unittest.TestCase):
    def _args(self) -> SimpleNamespace:
        return SimpleNamespace(
            catalog_file=None,
            endpoint_file=None,
            allow_synthetic_endpoints=True,
        )

    def _run(self) -> SimpleNamespace:
        return SimpleNamespace()

    def test_only_planned_models_are_kept_without_provider_endpoint_lookup(self) -> None:
        raw_catalog = {
            "data": [model_row(model_id) for model_id in MODEL_IDS]
            + [model_row("unused/cheap")]
        }
        captured: dict[str, object] = {}

        def fake_compact(
            planned_catalog,
            endpoint_payloads,
            task_envelope,
            *,
            model_ids,
            allow_synthetic_endpoints,
        ):
            captured["planned_catalog"] = planned_catalog
            captured["endpoint_payloads"] = endpoint_payloads
            captured["model_ids"] = list(model_ids)
            captured["allow_synthetic_endpoints"] = allow_synthetic_endpoints
            return {
                "endpoints": [],
                "planned_model_ids": list(model_ids),
            }

        with patch.object(
            pipeline.catalog_view,
            "fetch_live_model_catalog",
            return_value=raw_catalog,
        ), patch.object(
            pipeline,
            "fetch_live_endpoint_payloads",
            side_effect=AssertionError("Provider endpoint inventory must not be queried"),
        ), patch.object(
            pipeline.catalog_view,
            "compact_endpoint_catalog",
            side_effect=fake_compact,
        ):
            catalog, source, endpoint_source = pipeline._catalog_state(
                self._args(),
                self._run(),
                {"required_context_tokens": 16384},
                plan(),
            )

        self.assertEqual(captured["model_ids"], MODEL_IDS)
        self.assertNotIn(
            "unused/cheap",
            [row["id"] for row in captured["planned_catalog"]["data"]],
        )
        payloads = captured["endpoint_payloads"]
        self.assertEqual(list(payloads), MODEL_IDS)
        for model_id in MODEL_IDS:
            endpoint = payloads[model_id]["data"]["endpoints"][0]
            self.assertEqual(endpoint["tag"], "openrouter-unrestricted")
            self.assertFalse(endpoint["routing_constraint"])
        self.assertEqual(source, pipeline.catalog_view.OPENROUTER_MODELS_API)
        self.assertEqual(
            endpoint_source,
            "model-metadata-derived-openrouter-unrestricted",
        )
        self.assertEqual(
            catalog["catalog_scope"],
            "governance-signed-top50-assigned-models-only",
        )
        self.assertEqual(catalog["provider_routing_mode"], "unrestricted-openrouter")
        self.assertFalse(catalog["provider_restrictions_applied"])
        self.assertFalse(catalog["live_provider_endpoint_inventory_required"])

    def test_missing_planned_model_fails_closed(self) -> None:
        raw_catalog = {"data": [model_row(model_id) for model_id in MODEL_IDS[:-1]]}
        with patch.object(
            pipeline.catalog_view,
            "fetch_live_model_catalog",
            return_value=raw_catalog,
        ):
            with self.assertRaisesRegex(CatalogViewError, "perplexity/sonar-pro"):
                pipeline._catalog_state(
                    self._args(),
                    self._run(),
                    {"required_context_tokens": 16384},
                    plan(),
                )

    def test_provider_metadata_is_non_binding(self) -> None:
        payloads = pipeline._open_endpoint_payloads(
            MODEL_IDS,
            plan(),
            {"data": [model_row(model_id) for model_id in MODEL_IDS]},
        )
        self.assertEqual(list(payloads), MODEL_IDS)
        for row in payloads.values():
            endpoint = row["data"]["endpoints"][0]
            self.assertEqual(
                endpoint["provider_name"],
                "OpenRouter unrestricted routing",
            )
            self.assertFalse(endpoint["routing_constraint"])

    def test_model_market_rank_ceiling_remains_available_for_rollback_path(self) -> None:
        args = pipeline.build_parser().parse_args(["--task", "test"])
        self.assertEqual(args.ranking_limit, 1000)
        self.assertEqual(pipeline.model_market.MAX_CATALOG_MODELS, 1000)


if __name__ == "__main__":
    unittest.main()
