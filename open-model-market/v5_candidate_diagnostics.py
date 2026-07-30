"""Zero-call structural diagnostics for V5 candidate markets."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_live_benchmark as base
import v5_low_cost_pilot as pilot
import v5_low_cost_pilot_v2 as pilot_v2
import v5_planner

_INSTALLED = False


def _score(endpoint: Mapping[str, Any], label: str) -> float:
    capabilities = endpoint.get("capability_scores") if isinstance(endpoint.get("capability_scores"), Mapping) else {}
    try:
        return float(capabilities.get(label, 0.0))
    except (TypeError, ValueError):
        return 0.0


def _raw_score(endpoint: Mapping[str, Any], label: str) -> float:
    calibration = endpoint.get("task_domain_proxy_calibration") if isinstance(endpoint.get("task_domain_proxy_calibration"), Mapping) else {}
    row = calibration.get(label) if isinstance(calibration.get(label), Mapping) else {}
    try:
        return float(row.get("raw_score", _score(endpoint, label)))
    except (TypeError, ValueError):
        return _score(endpoint, label)


def analyze_hard_requirement_gaps(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
) -> dict[str, Any]:
    """Explain which hard capability/context gates reject every endpoint."""
    endpoints = [row for row in market.get("endpoints", []) if isinstance(row, Mapping)]
    matrices = resource_bundle.get("resource_matrices", {}).get("matrices", [])
    interpretation_rows: list[dict[str, Any]] = []
    for matrix in matrices if isinstance(matrices, list) else []:
        if not isinstance(matrix, Mapping):
            continue
        work_meta = {
            str(row.get("work_id")): row
            for row in matrix.get("work_index", [])
            if isinstance(row, Mapping) and row.get("work_id")
        }
        hard_by_work: dict[str, list[Mapping[str, Any]]] = {}
        for row in matrix.get("hard_requirements", []) if isinstance(matrix.get("hard_requirements"), list) else []:
            if isinstance(row, Mapping) and row.get("work_id") and row.get("capability"):
                hard_by_work.setdefault(str(row["work_id"]), []).append(row)
        work_rows: list[dict[str, Any]] = []
        for work_id, requirements in hard_by_work.items():
            meta = work_meta.get(work_id, {})
            required_context = int(meta.get("required_context_tokens", 0) or 0)
            required_output = int(meta.get("expected_output_tokens", 0) or 0)
            eligible_context = [
                endpoint for endpoint in endpoints
                if int(endpoint.get("context_length", 0) or 0) >= required_context
                and int(endpoint.get("max_completion_tokens", 0) or 0) >= required_output
            ]
            label_rows: list[dict[str, Any]] = []
            passing_all: list[Mapping[str, Any]] = []
            for endpoint in eligible_context:
                passed = True
                for requirement in requirements:
                    label = str(requirement["capability"])
                    minimum_demand = float(requirement.get("minimum_demand", 0.0) or 0.0)
                    threshold = max(0.48, 0.62 * minimum_demand)
                    if _score(endpoint, label) + 1e-12 < threshold:
                        passed = False
                        break
                if passed:
                    passing_all.append(endpoint)
            for requirement in requirements:
                label = str(requirement["capability"])
                minimum_demand = float(requirement.get("minimum_demand", 0.0) or 0.0)
                threshold = max(0.48, 0.62 * minimum_demand)
                ranked = sorted(
                    eligible_context,
                    key=lambda endpoint: (
                        -_score(endpoint, label),
                        str(endpoint.get("model_id") or ""),
                        str(endpoint.get("provider_slug") or ""),
                    ),
                )
                passing = [endpoint for endpoint in ranked if _score(endpoint, label) + 1e-12 >= threshold]
                top = ranked[:5]
                label_rows.append({
                    "capability": label,
                    "minimum_demand": round(minimum_demand, 6),
                    "planner_threshold": round(threshold, 6),
                    "maximum_calibrated_score": round(max((_score(endpoint, label) for endpoint in ranked), default=0.0), 6),
                    "maximum_raw_score": round(max((_raw_score(endpoint, label) for endpoint in ranked), default=0.0), 6),
                    "passing_endpoint_count": len(passing),
                    "passing_model_count": len({str(endpoint.get("model_id") or "") for endpoint in passing}),
                    "top_endpoints": [
                        {
                            "model_id": endpoint.get("model_id"),
                            "provider_slug": endpoint.get("provider_slug"),
                            "calibrated_score": round(_score(endpoint, label), 6),
                            "raw_score": round(_raw_score(endpoint, label), 6),
                        }
                        for endpoint in top
                    ],
                })
            work_rows.append({
                "work_id": work_id,
                "required_context_tokens": required_context,
                "expected_output_tokens": required_output,
                "context_output_eligible_endpoint_count": len(eligible_context),
                "hard_requirement_count": len(requirements),
                "all_hard_requirements_passing_endpoint_count": len(passing_all),
                "all_hard_requirements_passing_model_count": len({str(endpoint.get("model_id") or "") for endpoint in passing_all}),
                "hard_requirements": label_rows,
            })
        interpretation_rows.append({
            "interpretation_id": str(matrix.get("interpretation_id") or ""),
            "work_gaps": work_rows,
            "works_with_zero_passing_endpoints": [
                row["work_id"] for row in work_rows
                if row["all_hard_requirements_passing_endpoint_count"] == 0
            ],
        })
    calibration = market.get("task_domain_proxy_calibration") if isinstance(market.get("task_domain_proxy_calibration"), Mapping) else {}
    return {
        "interpretations": interpretation_rows,
        "domain_proxy_calibration": calibration,
        "hard_requirement_threshold_formula": "max(0.48, 0.62 * minimum_demand)",
        "hard_requirement_thresholds_changed": False,
        "model_calls": 0,
    }


def analyze_candidate_structure(
    market: Mapping[str, Any],
    candidate_bundle: Mapping[str, Any],
    resource_bundle: Mapping[str, Any] | None = None,
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
        for work_id, copies_raw in copies_by_work.items():
            copies = max(1, int(copies_raw))
            per_copy: list[dict[str, Any]] = []
            union_models: set[str] = set()
            union_endpoints: set[str] = set()
            for copy_index in range(copies):
                key = f"{work_id}#{copy_index}"
                matches = [row for row in scoped if key in {str(x) for x in row.get("coverage_keys", [])}]
                models = sorted({str(row.get("model") or "") for row in matches if row.get("model")})
                endpoints = sorted({str(row.get("provider_endpoint") or "") for row in matches if row.get("provider_endpoint")})
                union_models.update(models)
                union_endpoints.update(endpoints)
                per_copy.append({
                    "coverage_key": key,
                    "candidate_count": len(matches),
                    "distinct_model_count": len(models),
                    "distinct_endpoint_count": len(endpoints),
                    "models": models,
                })
                if not matches:
                    interpretation_blockers.append(f"{key}:no-candidate")
            if len(union_models) < copies:
                interpretation_blockers.append(
                    f"{work_id}:distinct-models={len(union_models)}<required-copies={copies}"
                )
            if len(union_endpoints) < copies:
                interpretation_blockers.append(
                    f"{work_id}:distinct-endpoints={len(union_endpoints)}<required-copies={copies}"
                )
            work_rows.append({
                "work_id": str(work_id),
                "required_copies": copies,
                "per_copy": per_copy,
                "union_distinct_models": sorted(union_models),
                "union_distinct_model_count": len(union_models),
                "union_distinct_endpoint_count": len(union_endpoints),
                "local_independence_feasible": len(union_models) >= copies and len(union_endpoints) >= copies and all(
                    row["candidate_count"] > 0 for row in per_copy
                ),
            })
        unique_blockers = sorted(set(interpretation_blockers))
        global_blockers.extend(f"{interpretation_id}:{item}" for item in unique_blockers)
        report_rows.append({
            "interpretation_id": str(interpretation_id),
            "candidate_count_after_pareto": len(scoped),
            "required_coverage_count": sum(max(1, int(value)) for value in copies_by_work.values()),
            "distinct_model_count": len({str(row.get("model") or "") for row in scoped if row.get("model")}),
            "distinct_endpoint_count": len({str(row.get("provider_endpoint") or "") for row in scoped if row.get("provider_endpoint")}),
            "work_coverage": work_rows,
            "local_structure_feasible": not unique_blockers,
            "blockers": unique_blockers,
        })

    endpoints = [row for row in market.get("endpoints", []) if isinstance(row, Mapping)]
    result = {
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
        "model_calls": 0,
    }
    if resource_bundle is not None:
        result["hard_requirement_gaps"] = analyze_hard_requirement_gaps(resource_bundle, market)
    return result


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
                    market, candidates, resource_bundle
                )
            except Exception as diagnostic_exc:  # noqa: BLE001
                pilot_v2._PLANNING_DIAGNOSTIC["candidate_structure"] = {
                    "diagnostic_error": str(diagnostic_exc),
                    "model_calls": 0,
                }
            raise

    base.compile_and_optimize_v5 = diagnostic_compile
