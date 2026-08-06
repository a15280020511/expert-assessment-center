#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "open-model-market" / "v5_price_ranked_pipeline.py"
TEST = ROOT / "tests" / "test_v5_planned_catalog_scope.py"


def patch_pipeline() -> None:
    text = PIPELINE.read_text(encoding="utf-8")
    text = text.replace("    eligible_models,\n", "", 1)
    start = text.index("def _catalog_state(")
    end = text.index("\ndef _catalog_snapshot(", start)
    replacement = textwrap.dedent('''\
    def _planned_model_descriptors(
        plan: Mapping[str, Any],
        *,
        required_context: int,
    ) -> tuple[list[Any], list[str]]:
        rows = [
            *list(plan.get("selected_models") or []),
            *list(plan.get("recovery_models") or []),
        ]
        if not rows:
            raise CatalogViewError("governance plan contains no model identities")
        minimum_completion = max(
            MINIMUM_EXPERT_COMPLETION_TOKENS,
            int(
                plan.get("minimum_native_completion_tokens")
                or MINIMUM_EXPERT_COMPLETION_TOKENS
            ),
        )
        descriptors: list[Any] = []
        planned_ids: list[str] = []
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise CatalogViewError(
                    f"governance plan model row {index} is not an object"
                )
            model_id = str(row.get("model") or "").strip()
            if not model_id or "/" not in model_id or model_id in seen:
                raise CatalogViewError(
                    "governance plan model identities are missing or repeated"
                )
            try:
                prompt_price = float(row.get("prompt_usd_per_million"))
                completion_price = float(row.get("completion_usd_per_million"))
                intelligence_rank = int(row.get("official_intelligence_rank"))
            except (TypeError, ValueError) as exc:
                raise CatalogViewError(
                    f"governance plan model facts are invalid: {model_id}"
                ) from exc
            if prompt_price < 0 or completion_price < 0 or intelligence_rank <= 0:
                raise CatalogViewError(
                    f"governance plan model facts are invalid: {model_id}"
                )
            descriptors.append(
                model_market.ModelInfo(
                    id=model_id,
                    name=model_id,
                    description="governance-signed execution model",
                    author=model_id.split("/", 1)[0],
                    context_length=max(1, int(required_context)),
                    max_completion_tokens=minimum_completion,
                    prompt_price_per_million=prompt_price,
                    completion_price_per_million=completion_price,
                    supported_parameters=[],
                    input_modalities=["text"],
                    output_modalities=["text"],
                    knowledge_cutoff=None,
                    expiration_date=None,
                    reasoning={},
                    ranks={"intelligence-high-to-low": intelligence_rank},
                )
            )
            planned_ids.append(model_id)
            seen.add(model_id)
        return descriptors, planned_ids


    def _catalog_state(
        args: argparse.Namespace,
        run: Any,
        task_envelope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], str, str]:
        del args
        required_context = int(task_envelope["required_context_tokens"])
        ranked, planned_ids = _planned_model_descriptors(
            plan,
            required_context=required_context,
        )
        catalog_source = "governance-signed-plan"
        if run.dry_run and run.catalog_file:
            payloads: Mapping[str, Any] = {}
            endpoint_source = "synthetic-fixture-endpoints"
            synthetic = True
        else:
            payloads = fetch_live_endpoint_payloads(
                ranked,
                run,
                maximum_models=len(ranked),
            )
            endpoint_source = "openrouter-live-exact-endpoints"
            synthetic = False
        catalog = dict(
            compact_endpoint_catalog(
                ranked,
                payloads,
                allow_synthetic_fixture=synthetic,
                required_context_tokens=required_context,
                minimum_completion_tokens=MINIMUM_EXPERT_COMPLETION_TOKENS,
            )
        )
        catalog.update(
            {
                "selection_authority": "decision-system-governance",
                "model_selection_performed_locally": False,
                "model_reranking_performed_locally": False,
                "model_substitution_allowed": False,
                "provider_resolution_only": True,
                "catalog_scope": "governance-plan-models-only",
                "model_identity_source": "governance-signed-plan",
                "planned_model_ids": planned_ids,
            }
        )
        return catalog, catalog_source, endpoint_source

    ''')
    PIPELINE.write_text(text[:start] + replacement + text[end + 1 :], encoding="utf-8")


def write_test() -> None:
    TEST.write_text(
        textwrap.dedent('''\
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


        def plan() -> dict:
            rows = []
            for index, (model_id, rank) in enumerate(
                zip(MODEL_IDS, RANKS, strict=True),
                1,
            ):
                rows.append(
                    {
                        "slot": index,
                        "model": model_id,
                        "company": model_id.split("/", 1)[0],
                        "prompt_usd_per_million": float(index) / 10,
                        "completion_usd_per_million": float(index),
                        "official_intelligence_rank": rank,
                    }
                )
            return {
                "minimum_native_completion_tokens": 1024,
                "selected_models": rows[:4],
                "recovery_models": rows[4:],
            }


        class PlannedCatalogScopeTests(unittest.TestCase):
            def test_live_execution_uses_signed_plan_without_second_catalog(self) -> None:
                captured: list[str] = []

                def fake_fetch(ranked, _run, **_kwargs):
                    captured.extend(row.id for row in ranked)
                    return {
                        row.id: {"data": {"endpoints": []}}
                        for row in ranked
                    }

                args = SimpleNamespace(ranking_limit=1000, endpoint_file=None)
                run = SimpleNamespace(dry_run=False, catalog_file=None)
                envelope = {"required_context_tokens": 16384}
                with patch.object(
                    pipeline.model_market,
                    "fetch_catalog",
                    side_effect=AssertionError(
                        "expert center must not fetch a second model ranking"
                    ),
                ) as aggregate_fetch, patch.object(
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
                aggregate_fetch.assert_not_called()
                self.assertEqual(captured, MODEL_IDS)
                self.assertEqual(source, "governance-signed-plan")
                self.assertEqual(
                    endpoint_source,
                    "openrouter-live-exact-endpoints",
                )
                self.assertEqual(
                    catalog["catalog_scope"],
                    "governance-plan-models-only",
                )
                self.assertEqual(
                    catalog["model_identity_source"],
                    "governance-signed-plan",
                )
                self.assertEqual(catalog["planned_model_ids"], MODEL_IDS)

            def test_signed_plan_facts_create_endpoint_descriptors(self) -> None:
                descriptors, model_ids = pipeline._planned_model_descriptors(
                    plan(),
                    required_context=16384,
                )
                self.assertEqual(model_ids, MODEL_IDS)
                self.assertEqual(
                    [row.ranks["intelligence-high-to-low"] for row in descriptors],
                    RANKS,
                )
                self.assertEqual(descriptors[0].context_length, 16384)
                self.assertEqual(descriptors[0].max_completion_tokens, 1024)
                self.assertEqual(descriptors[0].prompt_price_per_million, 0.1)
                self.assertEqual(descriptors[-1].completion_price_per_million, 8.0)

            def test_duplicate_signed_model_identity_fails_closed(self) -> None:
                value = plan()
                value["recovery_models"][0]["model"] = MODEL_IDS[0]
                with self.assertRaisesRegex(
                    CatalogViewError,
                    "missing or repeated",
                ):
                    pipeline._planned_model_descriptors(
                        value,
                        required_context=16384,
                    )

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
                    self.assertTrue(
                        result[item.id]["zdr_endpoint_filter"]["required"]
                    )

            def test_default_matches_governance_rank_ceiling(self) -> None:
                args = pipeline.build_parser().parse_args(["--task", "test"])
                self.assertEqual(args.ranking_limit, 1000)
                self.assertEqual(pipeline.model_market.MAX_CATALOG_MODELS, 1000)


        if __name__ == "__main__":
            unittest.main()
        '''),
        encoding="utf-8",
    )


def main() -> int:
    patch_pipeline()
    write_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
