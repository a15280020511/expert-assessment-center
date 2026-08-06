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
from v5_endpoint_catalog import (  # noqa: E402
    ZDR_ENDPOINTS_URL,
    fetch_live_endpoint_payloads,
)

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
RANKS = [251, 28, 25, 23, 129, 38, 63, 274]


def model(model_id: str, rank: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=model_id,
        context_length=131072,
        input_modalities=["text"],
        output_modalities=["text"],
        prompt_price_per_million=1.0,
        completion_price_per_million=3.0,
        ranks={"intelligence-high-to-low": rank},
    )


def plan() -> dict:
    rows = [
        {"model": model_id, "company": model_id.split("/", 1)[0]}
        for model_id in MODEL_IDS
    ]
    return {"selected_models": rows[:4], "recovery_models": rows[4:]}


class PlannedCatalogScopeTests(unittest.TestCase):
    def test_only_planned_models_are_fetched_in_plan_order(self) -> None:
        models = {
            model_id: model(model_id, rank)
            for model_id, rank in zip(MODEL_IDS, RANKS, strict=True)
        }
        models["unused/cheap"] = model("unused/cheap", 1)
        captured: list[str] = []

        def fake_fetch(ranked, _run, **_kwargs):
            captured.extend(row.id for row in ranked)
            return {row.id: {"data": {"endpoints": []}} for row in ranked}

        args = SimpleNamespace(ranking_limit=1000, endpoint_file=None)
        run = SimpleNamespace(dry_run=False, catalog_file=None)
        envelope = {"required_context_tokens": 16384}
        with patch.object(
            pipeline.model_market,
            "fetch_catalog",
            return_value=(models, "live"),
        ), patch.object(
            pipeline,
            "fetch_live_endpoint_payloads",
            side_effect=fake_fetch,
        ), patch.object(
            pipeline,
            "compact_endpoint_catalog",
            return_value={"endpoints": []},
        ):
            catalog, source, endpoint_source = pipeline._catalog_state(
                args,
                run,
                envelope,
                plan(),
            )
        self.assertEqual(captured, MODEL_IDS)
        self.assertNotIn("unused/cheap", captured)
        self.assertEqual(source, "live")
        self.assertEqual(endpoint_source, "openrouter-live-exact-endpoints")
        self.assertEqual(catalog["catalog_scope"], "governance-plan-models-only")
        self.assertEqual(catalog["planned_model_ids"], MODEL_IDS)

    def test_missing_planned_model_fails_closed(self) -> None:
        models = {
            model_id: model(model_id, rank)
            for model_id, rank in zip(
                MODEL_IDS[:-1],
                RANKS[:-1],
                strict=True,
            )
        }
        args = SimpleNamespace(ranking_limit=1000, endpoint_file=None)
        run = SimpleNamespace(dry_run=False, catalog_file=None)
        envelope = {"required_context_tokens": 16384}
        with patch.object(
            pipeline.model_market,
            "fetch_catalog",
            return_value=(models, "live"),
        ):
            with self.assertRaisesRegex(CatalogViewError, "perplexity/sonar-pro"):
                pipeline._catalog_state(args, run, envelope, plan())

    def test_all_companies_use_authenticated_zdr_filter(self) -> None:
        models = [
            SimpleNamespace(id="openai/gpt-latest"),
            SimpleNamespace(id="anthropic/claude-opus-latest"),
        ]
        run = SimpleNamespace(
            api_key="test-key",
            catalog_timeout_seconds=5,
            catalog_max_retries=0,
        )

        def fake_request(url, *_args):
            if url == ZDR_ENDPOINTS_URL:
                return {
                    "data": [
                        {"model_id": item.id, "tag": "direct"}
                        for item in models
                    ]
                }
            return {
                "data": {
                    "endpoints": [
                        {"tag": "direct"},
                        {"tag": "not-zdr"},
                    ]
                }
            }

        with patch(
            "v5_endpoint_catalog.request_json",
            side_effect=fake_request,
        ) as request:
            result = fetch_live_endpoint_payloads(models, run)
        self.assertEqual(request.call_count, 3)
        for item in models:
            self.assertEqual(
                result[item.id]["data"]["endpoints"],
                [{"tag": "direct"}],
            )
            self.assertTrue(result[item.id]["zdr_endpoint_filter"]["required"])

    def test_default_matches_governance_rank_ceiling(self) -> None:
        args = pipeline.build_parser().parse_args(["--task", "test"])
        self.assertEqual(args.ranking_limit, 1000)
        self.assertEqual(pipeline.model_market.MAX_CATALOG_MODELS, 1000)


if __name__ == "__main__":
    unittest.main()
