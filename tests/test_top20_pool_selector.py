from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "open-model-market" / "v5_top20_pool_selector.py"
    spec = importlib.util.spec_from_file_location("v5_top20_pool_selector_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _candidate(index: int) -> dict:
    return {
        "slot": index,
        "candidate_price_rank": index,
        "model": f"company{index}/model-{index}",
        "company": f"company{index}",
        "estimated_task_cost_usd": float(index),
        "price_rank_usd_per_million": float(index),
        "prompt_usd_per_million": float(index) / 3,
        "completion_usd_per_million": float(index) * 2 / 3,
        "official_intelligence_rank": 100 - index,
        "qualified_provider_count": 1,
        "endpoint_inventory_sha256": f"{index:064x}"[-64:],
        "flagship_verified": True,
        "flagship_basis": "strict-product-tier",
        "company_flagship_method": "fixture",
        "benchmark_source": "artificial-analysis-via-openrouter",
        "intelligence_index": float(index),
        "coding_index": float(index),
        "agentic_index": float(index),
        "balanced_score": float(index),
        "benchmark_evidence_sha256": f"{index + 100:064x}"[-64:],
        "selection_evidence": (
            "non-search+verified-company-flagship-reasoning+strict-product-tier+"
            "price-order+live-exact-endpoint-qualified+"
            "authenticated-zdr-endpoint-qualified+minimum-one-zdr-provider-route"
        ),
        "popularity_rank": index,
        "source_pool_schema_version": "governance-openrouter-top20-reasoning-pool-v1",
        "source_pool": "openrouter-most-popular-last-week-token-volume",
        "expert_center_selectable": True,
    }


def _packet() -> dict:
    raw = [
        {
            "popularity_rank": index,
            "source_rank": index,
            "model": f"company{index}/model-{index}",
            "company": f"company{index}",
            "reasoning_supported": True,
        }
        for index in range(1, 21)
    ]
    eligible = [_candidate(index) for index in range(1, 11)]
    plan = {
        "schema_version": "governance-expert-model-plan-v1",
        "selection_authority": "decision-system-governance",
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": False,
        "task_sha256": "0" * 64,
        "top20_reasoning_pool_schema_version": (
            "governance-openrouter-top20-reasoning-pool-v1"
        ),
        "top20_reasoning_pool_source": (
            "openrouter-most-popular-last-week-token-volume"
        ),
        "top20_reasoning_pool_size": 20,
        "top20_reasoning_models": raw,
        "top20_reasoning_pool_sha256": _sha(raw),
        "expert_selectable_candidates": eligible,
        "expert_selectable_candidates_sha256": _sha(eligible),
        "expert_selectable_candidate_count": len(eligible),
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center",
        "expert_center_pool_selection_allowed": True,
        "selected_models": [],
        "recovery_models": [],
        "price_ranked_models": [],
        "expert_count": 4,
        "recovery_count": 4,
    }
    material = dict(plan)
    plan["plan_sha256"] = _sha(material)
    return {"task": {"question": "fixture"}, "governance_model_plan": plan}


def test_materializer_selects_only_eight_cheapest_distinct_pool_models() -> None:
    module = _load_module()
    packet, receipt = module.materialize_top20_selection(_packet())
    plan = packet["governance_model_plan"]

    assert plan["expert_center_pool_selection_completed"] is True
    assert plan["selected_from_top20_reasoning_pool_only"] is True
    assert plan["expert_count"] == 4
    assert plan["recovery_count"] == 4
    assert {row["model"] for row in plan["selected_models"]} == {
        f"company{index}/model-{index}" for index in range(1, 5)
    }
    assert [row["model"] for row in plan["recovery_models"]] == [
        f"company{index}/model-{index}" for index in range(5, 9)
    ]
    assert [row["model"] for row in plan["price_ranked_models"]] == [
        f"company{index}/model-{index}" for index in range(1, 9)
    ]
    assert [row["price_rank"] for row in plan["price_ranked_models"]] == list(
        range(1, 9)
    )
    assert {row["role_kind"] for row in plan["selected_models"]} == {
        "independent",
        "review",
        "synthesis",
    }
    assert receipt["model_calls"] == 0
    assert plan["plan_sha256"] == module._plan_digest(plan)
