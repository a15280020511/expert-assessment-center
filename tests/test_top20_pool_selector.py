from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_selector():
    path = Path(__file__).resolve().parents[1] / "open-model-market" / "v5_top20_pool_selector.py"
    return _load_module(path, "v5_top20_pool_selector_test")


def _load_validator():
    path = Path(__file__).resolve().parents[1] / "open-model-market" / "v5_governance_model_plan.py"
    return _load_module(path, "v5_governance_model_plan_top20_test")


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


def _company(index: int) -> str:
    return "company1" if index == 2 else f"company{index}"


def _candidate(index: int) -> dict:
    company = _company(index)
    return {
        "slot": index,
        "candidate_price_rank": index,
        "model": f"{company}/model-{index}",
        "company": company,
        "estimated_task_cost_usd": float(index),
        "price_rank_usd_per_million": float(index),
        "prompt_usd_per_million": float(index) / 3,
        "completion_usd_per_million": float(index) * 2 / 3,
        "official_intelligence_rank": 100 - index,
        "qualified_provider_count": 1,
        "endpoint_inventory_sha256": f"{index:064x}"[-64:],
        "selection_evidence": (
            "openrouter-top-weekly-reasoning+live-exact-endpoint-qualified+"
            "authenticated-zdr-endpoint-qualified"
        ),
        "popularity_rank": index,
        "reasoning_rank_verified": True,
        "reasoning_supported": True,
        "ranking_basis": "openrouter-most-popular-last-week-token-volume",
        "source_pool_schema_version": "governance-openrouter-top20-reasoning-pool-v1",
        "source_pool": "openrouter-most-popular-last-week-token-volume",
        "expert_center_selectable": True,
    }


def _packet() -> dict:
    task = {"question": "fixture"}
    raw = [
        {
            "popularity_rank": index,
            "source_rank": index,
            "model": f"{_company(index)}/model-{index}",
            "company": _company(index),
            "reasoning_supported": True,
        }
        for index in range(1, 21)
    ]
    eligible = [_candidate(index) for index in range(1, 11)]
    distinct_companies = len({row["company"] for row in eligible})
    plan = {
        "schema_version": "governance-expert-model-plan-v1",
        "selection_authority": "decision-system-governance",
        "model_substitution_allowed": False,
        "expert_center_reranking_allowed": False,
        "task_sha256": _sha(task),
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
        "expert_selectable_distinct_company_count": distinct_companies,
        "candidate_pool_authority": "decision-system-governance",
        "model_assignment_authority": "expert-assessment-center",
        "expert_center_pool_selection_allowed": True,
        "old_flagship_filter_applied_to_top20_pool": False,
        "endpoint_qualification_performed_by_governance": True,
        "model_calls": 0,
        "selected_models": [],
        "recovery_models": [],
        "price_ranked_models": [],
        "expert_count": 4,
        "recovery_count": 4,
    }
    material = dict(plan)
    plan["plan_sha256"] = _sha(material)
    return {
        "task": task,
        "approved_budget": {"calls": 8, "maximum_recovery_calls": 4},
        "governance_model_plan": plan,
    }


def test_materializer_skips_cheaper_duplicate_company_rows() -> None:
    module = _load_selector()
    packet, receipt = module.materialize_top20_selection(_packet())
    plan = packet["governance_model_plan"]

    assert plan["expert_center_pool_selection_completed"] is True
    assert plan["selected_from_top20_reasoning_pool_only"] is True
    assert plan["expert_count"] == 4
    assert plan["recovery_count"] == 4
    expected = [
        "company1/model-1",
        "company3/model-3",
        "company4/model-4",
        "company5/model-5",
        "company6/model-6",
        "company7/model-7",
        "company8/model-8",
        "company9/model-9",
    ]
    assert [row["model"] for row in plan["price_ranked_models"]] == expected
    assert {row["model"] for row in plan["selected_models"]} == set(expected[:4])
    assert [row["model"] for row in plan["recovery_models"]] == expected[4:]
    assert [row["price_rank"] for row in plan["price_ranked_models"]] == list(
        range(1, 9)
    )
    assert len({row["company"] for row in plan["price_ranked_models"]}) == 8
    assert {row["role_kind"] for row in plan["selected_models"]} == {
        "independent",
        "review",
        "synthesis",
    }
    assert receipt["model_calls"] == 0
    assert plan["plan_sha256"] == module._plan_digest(plan)


def test_materialized_plan_passes_direct_top20_execution_contract() -> None:
    selector = _load_selector()
    validator = _load_validator()
    packet, _ = selector.materialize_top20_selection(_packet())
    validated = validator.validate_governance_model_plan(packet)
    assert validated["selected_from_top20_reasoning_pool_only"] is True
    assert all(
        "flagship_basis" not in row
        for row in validated["price_ranked_models"]
    )


def test_materializer_rejects_fewer_than_eight_distinct_companies() -> None:
    module = _load_selector()
    packet = _packet()
    plan = packet["governance_model_plan"]
    eligible = plan["expert_selectable_candidates"]
    for index, row in enumerate(eligible):
        row["company"] = f"company{index % 7}"
    plan["expert_selectable_distinct_company_count"] = 7
    plan["expert_selectable_candidates_sha256"] = _sha(eligible)
    material = dict(plan)
    material.pop("plan_sha256", None)
    plan["plan_sha256"] = _sha(material)

    try:
        module.materialize_top20_selection(packet)
    except module.Top20PoolSelectionError as exc:
        assert "fewer than eight distinct-company" in str(exc)
    else:
        raise AssertionError("seven-company pool must fail closed")
