#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]


def patch_catalog_view() -> None:
    path = ROOT / "open-model-market" / "v5_catalog_view.py"
    text = path.read_text(encoding="utf-8")
    if "MAX_VISIBLE_MODELS = 1000" in text:
        return
    if "MAX_VISIBLE_MODELS = 150" not in text:
        raise RuntimeError("catalog rank ceiling marker is missing")
    path.write_text(
        text.replace("MAX_VISIBLE_MODELS = 150", "MAX_VISIBLE_MODELS = 1000", 1),
        encoding="utf-8",
    )


def patch_endpoint_catalog() -> None:
    path = ROOT / "open-model-market" / "v5_endpoint_catalog.py"
    text = path.read_text(encoding="utf-8")
    if "zdr_model_ids = {str(model.id) for model in eligible}" in text:
        return
    text = text.replace("    GOVERNANCE_COMPANIES,\n", "", 1)
    text = text.replace("from v5_model_company import canonical_model_company\n", "", 1)
    start = text.index("    expert_model_ids = {")
    end = text.index("\n\n    timeout =", start)
    text = (
        text[:start]
        + "    zdr_model_ids = {str(model.id) for model in eligible}\n"
        + "    zdr_keys = _fetch_zdr_endpoint_keys(run)"
        + text[end:]
    )
    if "if model_id in expert_model_ids:" not in text:
        raise RuntimeError("endpoint ZDR filter marker is missing")
    text = text.replace(
        "if model_id in expert_model_ids:",
        "if model_id in zdr_model_ids:",
        1,
    )
    path.write_text(text, encoding="utf-8")


def patch_pipeline() -> None:
    path = ROOT / "open-model-market" / "v5_price_ranked_pipeline.py"
    text = path.read_text(encoding="utf-8")
    if '"catalog_scope": "governance-plan-models-only"' in text:
        return
    if "    MINIMUM_EXPERT_COMPLETION_TOKENS,\n" not in text:
        raise RuntimeError("catalog import marker is missing")
    text = text.replace(
        "    MINIMUM_EXPERT_COMPLETION_TOKENS,\n",
        "    CatalogViewError,\n    MINIMUM_EXPERT_COMPLETION_TOKENS,\n",
        1,
    )
    marker = 'parser.add_argument("--ranking-limit", type=int, default=150)'
    if marker not in text:
        raise RuntimeError("runtime rank limit marker is missing")
    text = text.replace(
        marker,
        'parser.add_argument("--ranking-limit", type=int, default=1000)',
        1,
    )
    start = text.index("def _catalog_state(")
    end = text.index("\ndef _catalog_snapshot(", start)
    function = textwrap.dedent('''\
    def _catalog_state(
        args: argparse.Namespace,
        run: Any,
        task_envelope: Mapping[str, Any],
        plan: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], str, str]:
        required_context = int(task_envelope["required_context_tokens"])
        models, catalog_source = model_market.fetch_catalog(run)
        eligible = eligible_models(
            models,
            requested_context=required_context,
            maximum_models=int(args.ranking_limit),
            exclude_governance_companies=False,
        )
        planned_rows = [
            *list(plan.get("selected_models") or []),
            *list(plan.get("recovery_models") or []),
        ]
        planned_ids = [
            str(row.get("model") or "").strip()
            for row in planned_rows
            if isinstance(row, Mapping)
        ]
        if not planned_ids or len(planned_ids) != len(set(planned_ids)):
            raise CatalogViewError(
                "governance plan model identities are missing or repeated"
            )
        eligible_by_id = {
            str(getattr(model, "id", "") or ""): model
            for model in eligible
        }
        missing = [
            model_id
            for model_id in planned_ids
            if model_id not in eligible_by_id
        ]
        if missing:
            raise CatalogViewError(
                "governance-planned models are not live-eligible: "
                + ", ".join(missing)
            )
        ranked = [eligible_by_id[model_id] for model_id in planned_ids]
        if args.endpoint_file:
            payloads = _load_mapping(Path(args.endpoint_file))
            endpoint_source = f"fixture:{args.endpoint_file}"
            synthetic = False
        elif run.dry_run and run.catalog_file:
            payloads = {}
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
                "planned_model_ids": planned_ids,
            }
        )
        return catalog, catalog_source, endpoint_source

    ''')
    text = text[:start] + function + text[end + 1 :]
    call_start = text.index(
        "catalog, catalog_source, endpoint_source = _catalog_state(",
        end,
    )
    args_start = text.index("args, run, task_envelope", call_start)
    args_end = args_start + len("args, run, task_envelope")
    text = (
        text[:args_start]
        + "args, run, task_envelope, plan"
        + text[args_end:]
    )
    path.write_text(text, encoding="utf-8")


def patch_old_test() -> None:
    path = ROOT / "tests" / "test_v5_endpoint_catalog.py"
    text = path.read_text(encoding="utf-8")
    method = "    def test_governance_models_are_not_filtered_by_expert_zdr_policy"
    if method not in text:
        return
    start = text.index(method)
    end = text.index(
        "    def test_missing_zdr_inventory_fails_closed_before_endpoint_fetch",
        start,
    )
    path.write_text(text[:start] + text[end:], encoding="utf-8")


def write_regression_test() -> None:
    path = ROOT / "tests" / "test_v5_planned_catalog_scope.py"
    content = textwrap.dedent('''\
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


    if __name__ == "__main__":
        unittest.main()
    ''')
    path.write_text(content, encoding="utf-8")


def main() -> int:
    patch_catalog_view()
    patch_endpoint_catalog()
    patch_pipeline()
    patch_old_test()
    write_regression_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
