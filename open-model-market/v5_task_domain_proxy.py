"""Task-derived domain capability calibration for V5 pilot markets.

Model marketing descriptions are sparse and frequently omit domain words even for
strong general models. Domain hard requirements must not be lowered, but their
measurement can be strengthened using the task matrix itself: when a domain demand
co-occurs with quantitative, evidence, risk, or other functional demands, those
functional capability scores provide task-specific transfer evidence.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import v5_planner

_INSTALLED = False


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def derive_domain_proxy_weights(resource_bundle: Mapping[str, Any], maximum_proxies: int = 6) -> dict[str, dict[str, float]]:
    """Derive domain-to-functional proxy weights from task demand co-occurrence."""
    accumulated: dict[str, dict[str, float]] = {}
    matrices = resource_bundle.get("resource_matrices", {}).get("matrices", [])
    for matrix in matrices if isinstance(matrices, list) else []:
        if not isinstance(matrix, Mapping):
            continue
        labels = [str(x) for x in matrix.get("capability_labels", [])]
        demands = matrix.get("task_resource_matrix", [])
        confidences = matrix.get("confidence_matrix", [])
        if not isinstance(demands, list):
            continue
        domain_indices = [index for index, label in enumerate(labels) if label.startswith("domain:")]
        functional_indices = [index for index, label in enumerate(labels) if not label.startswith("domain:")]
        for row_index, demand_row in enumerate(demands):
            if not isinstance(demand_row, list):
                continue
            confidence_row = confidences[row_index] if isinstance(confidences, list) and row_index < len(confidences) and isinstance(confidences[row_index], list) else []
            for domain_index in domain_indices:
                if domain_index >= len(demand_row):
                    continue
                domain_demand = max(0.0, _finite(demand_row[domain_index]))
                if domain_demand <= 0.0:
                    continue
                domain_confidence = max(0.25, _finite(confidence_row[domain_index], 0.75) if domain_index < len(confidence_row) else 0.75)
                domain_label = labels[domain_index]
                weights = accumulated.setdefault(domain_label, {})
                for capability_index in functional_indices:
                    if capability_index >= len(demand_row):
                        continue
                    functional_demand = max(0.0, _finite(demand_row[capability_index]))
                    if functional_demand <= 0.0:
                        continue
                    functional_confidence = max(0.25, _finite(confidence_row[capability_index], 0.75) if capability_index < len(confidence_row) else 0.75)
                    evidence = domain_demand * functional_demand * math.sqrt(domain_confidence * functional_confidence)
                    weights[labels[capability_index]] = weights.get(labels[capability_index], 0.0) + evidence

    normalized: dict[str, dict[str, float]] = {}
    for domain_label, raw in accumulated.items():
        ranked = sorted(
            ((label, weight) for label, weight in raw.items() if weight > 0.0),
            key=lambda item: (-item[1], item[0]),
        )[: max(1, int(maximum_proxies))]
        total = sum(weight for _, weight in ranked)
        if total <= 0.0:
            continue
        normalized[domain_label] = {
            label: round(weight / total, 8)
            for label, weight in ranked
        }
    return normalized


def calibrate_domain_market(market: Mapping[str, Any], resource_bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Raise domain measurements only when task-derived functional evidence supports it."""
    proxy_weights = derive_domain_proxy_weights(resource_bundle)
    result = dict(market)
    endpoints: list[dict[str, Any]] = []
    changed = 0
    calibration_rows: list[dict[str, Any]] = []
    for endpoint_raw in market.get("endpoints", []) if isinstance(market.get("endpoints"), list) else []:
        if not isinstance(endpoint_raw, Mapping):
            continue
        endpoint = dict(endpoint_raw)
        capabilities = dict(endpoint.get("capability_scores", {}) or {})
        confidence = max(0.0, min(1.0, _finite(endpoint.get("benchmark_confidence"), 0.75)))
        endpoint_changes: dict[str, Any] = {}
        for domain_label, weights in proxy_weights.items():
            raw_score = max(0.0, min(1.0, _finite(capabilities.get(domain_label), 0.0)))
            proxy_score = sum(float(weight) * max(0.0, min(1.0, _finite(capabilities.get(label), 0.0))) for label, weight in weights.items())
            confidence_adjusted = proxy_score * (0.90 + 0.10 * confidence)
            calibrated = max(raw_score, min(1.0, confidence_adjusted))
            if calibrated > raw_score + 1e-12:
                changed += 1
                endpoint_changes[domain_label] = {
                    "raw_score": round(raw_score, 6),
                    "proxy_score": round(proxy_score, 6),
                    "benchmark_confidence": round(confidence, 6),
                    "calibrated_score": round(calibrated, 6),
                    "proxy_weights": dict(weights),
                }
                capabilities[domain_label] = round(calibrated, 6)
        endpoint["capability_scores"] = capabilities
        if endpoint_changes:
            endpoint["task_domain_proxy_calibration"] = endpoint_changes
            calibration_rows.append({
                "endpoint_id": endpoint.get("endpoint_id"),
                "model_id": endpoint.get("model_id"),
                "provider_slug": endpoint.get("provider_slug"),
                "domains": endpoint_changes,
            })
        endpoints.append(endpoint)
    result["endpoints"] = endpoints
    result["task_domain_proxy_calibration"] = {
        "method": "task-matrix-domain-functional-cooccurrence",
        "hard_requirement_thresholds_changed": False,
        "functional_capability_scores_changed": False,
        "proxy_weights": proxy_weights,
        "endpoint_domain_scores_raised": changed,
        "calibrated_endpoint_count": len(calibration_rows),
        "calibrations": calibration_rows,
        "model_calls": 0,
    }
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = v5_planner.compile_model_endpoint_market

    def calibrated_compiler(
        ranked: Sequence[Any],
        resource_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        market = original(ranked, resource_bundle, **kwargs)
        return calibrate_domain_market(market, resource_bundle)

    v5_planner.compile_model_endpoint_market = calibrated_compiler
    _INSTALLED = True
