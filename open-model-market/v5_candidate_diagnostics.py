"""Zero-call structural diagnostics for V5 candidate markets."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_live_benchmark as base
import v5_low_cost_pilot as pilot
import v5_low_cost_pilot_v2 as pilot_v2
import v5_planner

_INSTALLED = False


def _provider_slug(row: Mapping[str, Any]) -> str:
    value = str(row.get("provider_slug") or "").strip()
    if value:
        return value
    endpoint = str(row.get("provider_endpoint") or "")
    return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint


def _work_policy(
    work_id: str,
    meta: Mapping[str, Any],
    scoped_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    explicit = meta.get("independence_policy_by_work")
    explicit_map = explicit if isinstance(explicit, Mapping) else {}
    raw = explicit_map.get(work_id)
    policy = raw if isinstance(raw, Mapping) else {}
    inferred_model_required = any(
        work_id in {str(value) for value in row.get("independence_groups", [])}
        for row in scoped_candidates
    )
    return {
        "different_model_required": bool(
            policy.get("different_model_required", inferred_model_required)
        ),
        "different_provider_required": bool(
            policy.get("different_provider_required", False)
        ),
        "provider_diversity_mode": (
            "hard" if bool(policy.get("different_provider_required", False))
            else "preferred-runtime-rebalancing"
        ),
    }


def analyze_candidate_structure(
    market: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
) -> dict[str, Any]:
    candidates = [row for row in candidate_bundle.get("candidates", []) if isinstance(row, Mapping)]
    interpretations = candidate_bundle.get("interpretations") if isinstance(candidate_bundle.get("interpretations"), Mapping) else {}
    report_rows: list[dict[str, Any]] = []
    global_blockers: list[str] = []

    for interpretation_id, meta_raw in interpretations.items():
        meta = meta_raw if isinstance(meta_raw, Mapping) else {}
        copies_by_work = meta.get("copies_by_work") if isinstance(meta.get("copies_by_work"), Mapping) else {}
        scoped = [row for row in candidates if str(row.get("interpretation_id")) == str(interpretation_id)]
        work_rows: list[dict[str, Any]] = []
        interpretation_blockers: list[str] = []
        for work_id_raw, copies_raw in copies_by_work.items():
            work_id = str(work_id_raw)
            copies = max(1, int(copies_raw))
            policy = _work_policy(work_id, meta, scoped)
            per_copy: list[dict[str, Any]] = []
            union_models: set[str] = set()
            union_providers: set[str] = set()
            union_endpoints: set[str] = set()
            for copy_index in range(copies):
                key = f"{work_id}#{copy_index}"
                matches = [row for row in scoped if key in {str(x) for x in row.get("coverage_keys", [])}]
                models = sorted({str(row.get("model") or "") for row in matches if row.get("model")})
                providers = sorted({_provider_slug(row) for row in matches if _provider_slug(row)})
                endpoints = sorted({str(row.get("provider_endpoint") or "") for row in matches if row.get("provider_endpoint")})
                union_models.update(models)
                union_providers.update(providers)
                union_endpoints.update(endpoints)
                per_copy.append({
                    "coverage_key": key,
                    "candidate_count": len(matches),
                    "distinct_model_count": len(models),
                    "distinct_provider_count": len(providers),
                    "distinct_endpoint_count": len(endpoints),
                    "models": models,
                })
                if not matches:
                    interpretation_blockers.append(f"{key}:no-candidate")
            if policy["different_model_required"] and len(union_models) < copies:
                interpretation_blockers.append(
                    f"{work_id}:distinct-models={len(union_models)}<required-copies={copies}"
                )
            if policy["different_provider_required"] and len(union_providers) < copies:
                interpretation_blockers.append(
                    f"{work_id}:distinct-providers={len(union_providers)}<required-copies={copies}"
                )
            copies_have_candidates = all(row["candidate_count"] > 0 for row in per_copy)
            model_policy_satisfied = (
                not policy["different_model_required"] or len(union_models) >= copies
            )
            provider_policy_satisfied = (
                not policy["different_provider_required"] or len(union_providers) >= copies
            )
            work_rows.append({
                "work_id": work_id,
                "required_copies": copies,
                "independence_policy": policy,
                "per_copy": per_copy,
                "union_distinct_models": sorted(union_models),
                "union_distinct_model_count": len(union_models),
                "union_distinct_providers": sorted(union_providers),
                "union_distinct_provider_count": len(union_providers),
                "union_distinct_endpoint_count": len(union_endpoints),
                "local_independence_feasible": bool(
                    copies_have_candidates
                    and model_policy_satisfied
                    and provider_policy_satisfied
                ),
            })
        unique_blockers = sorted(set(interpretation_blockers))
        global_blockers.extend(f"{interpretation_id}:{item}" for item in unique_blockers)
        report_rows.append({
            "interpretation_id": str(interpretation_id),
            "candidate_count_after_pareto": len(scoped),
            "required_coverage_count": sum(max(1, int(value)) for value in copies_by_work.values()),
            "distinct_model_count": len({str(row.get("model") or "") for row in scoped if row.get("model")}),
            "distinct_provider_count": len({_provider_slug(row) for row in scoped if _provider_slug(row)}),
            "distinct_endpoint_count": len({str(row.get("provider_endpoint") or "") for row in scoped if row.get("provider_endpoint")}),
            "work_coverage": work_rows,
            "local_structure_feasible": not unique_blockers,
            "blockers": unique_blockers,
        })

    endpoints = [row for row in market.get("endpoints", []) if isinstance(row, Mapping)]
    return {
        "price_tier": {
            "prompt_usd_per_million": float(pilot.MAX_PROMPT_PPM),
            "completion_usd_per_million": float(pilot.MAX_COMPLETION_PPM),
        },
        "market_endpoint_count": len(endpoints),
        "market_model_count": len({str(row.get("model_id") or "") for row in endpoints if row.get("model_id")}),
        "market_models": sorted({str(row.get("model_id") or "") for row in endpoints if row.get("model_id")}),
        "candidate_count_before_pareto": candidate_bundle.get("candidate_count_before_pareto"),
        "candidate_count_after_pareto": candidate_bundle.get("candidate_count_after_pareto"),
        "interpretations": report_rows,
        "local_structure_feasible_for_any_interpretation": any(row["local_structure_feasible"] for row in report_rows),
        "blockers": sorted(set(global_blockers)),
        "independence_policy_basis": (
            "explicit candidate independence_groups; provider diversity is preferred unless explicitly required"
        ),
        "model_calls": 0,
    }


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    original_compile = base.compile_and_optimize_v5

    def diagnostic_compile(
        ranked: Sequence[Any],
        resource_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> Mapping[str, Any]:
        try:
            return original_compile(ranked, resource_bundle, **kwargs)
        except v5_planner.V5PlanningError:
            try:
                market = v5_planner.compile_model_endpoint_market(
                    ranked,
                    resource_bundle,
                    endpoint_payloads=kwargs.get("endpoint_payloads"),
                    ranking_limit=int(kwargs.get("ranking_limit", 50)),
                    allow_synthetic_fixture=bool(kwargs.get("allow_synthetic_fixture", False)),
                )
                candidates = v5_planner.generate_candidate_graph(
                    resource_bundle,
                    market,
                    maximum_per_group=int(kwargs.get("maximum_per_group", 12)),
                )
                pilot_v2._PLANNING_DIAGNOSTIC["candidate_structure"] = analyze_candidate_structure(
                    market, candidates
                )
            except Exception as diagnostic_exc:  # noqa: BLE001
                pilot_v2._PLANNING_DIAGNOSTIC["candidate_structure"] = {
                    "diagnostic_error": str(diagnostic_exc),
                    "model_calls": 0,
                }
            raise

    base.compile_and_optimize_v5 = diagnostic_compile
