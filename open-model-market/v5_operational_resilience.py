"""Run-local endpoint calibration and statistically sufficient recovery planning.

All evidence comes from the current OpenRouter endpoint snapshot, current task
resource matrix, and current runtime configuration. No cross-task history or
provider blacklist is used.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import replace
from typing import Any, Mapping, Sequence

import v5_cost_reliability_hardening as cost_policy
from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy
from v5_model_company import canonical_model_company
from v5_planner import V5PlanningError

_PRIOR_RELIABILITY = 0.90
_UPTIME_WINDOWS = (
    ("uptime_last_1d", 0.55),
    ("uptime_last_30m", 0.30),
    ("uptime_last_5m", 0.15),
)
_RECOVERY_TAIL_LIMIT_BY_TIER = {
    "budget": 0.08,
    "value": 0.05,
    "quality": 0.025,
}
_DEADLINE_HEADROOM_BY_TIER = {
    "budget": 0.90,
    "value": 0.85,
    "quality": 0.80,
}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_uptime(value: Any, default: float) -> float:
    """Accept either a 0..1 ratio or OpenRouter's 0..100 percentage."""
    number = _float(value, default)
    if number > 1.0:
        number /= 100.0
    return _clamp(number)


def _provider_slug(row: Mapping[str, Any]) -> str:
    for key in ("tag", "provider_slug", "provider", "name", "provider_name"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _endpoint_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, Mapping)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(payload.get("endpoints"), list):
        return [row for row in payload["endpoints"] if isinstance(row, Mapping)]
    return []


def _metric(row: Mapping[str, Any], group: str, key: str) -> float | None:
    value = row.get(group)
    if not isinstance(value, Mapping) or value.get(key) is None:
        return None
    number = _float(value.get(key), -1.0)
    return number if number >= 0.0 else None


def calibrate_endpoint_operational_profile(
    endpoint: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a conservative current-snapshot operational profile."""
    row = endpoint if isinstance(endpoint, Mapping) else {}
    observed: list[tuple[str, float, float]] = []
    for key, weight in _UPTIME_WINDOWS:
        if row.get(key) is None:
            continue
        observed.append((key, weight, _normalize_uptime(row.get(key), _PRIOR_RELIABILITY)))

    if observed:
        weight_total = sum(weight for _, weight, _ in observed)
        weighted = sum(weight * value for _, weight, value in observed) / max(
            1e-12,
            weight_total,
        )
        values = [value for _, _, value in observed]
        volatility = max(values) - min(values)
        conservative_observation = _clamp(weighted - 0.35 * volatility)
        evidence_confidence = min(0.95, 0.45 + 0.18 * len(observed))
    else:
        weighted = _PRIOR_RELIABILITY
        volatility = 0.0
        conservative_observation = _PRIOR_RELIABILITY
        evidence_confidence = 0.0

    calibrated = _PRIOR_RELIABILITY + evidence_confidence * (
        conservative_observation - _PRIOR_RELIABILITY
    )
    status = row.get("status")
    if status not in (None, 0, "0"):
        calibrated = min(calibrated, 0.50)
    calibrated = max(0.0, min(0.995, calibrated))

    latency_p90_ms = _metric(row, "latency_last_30m", "p90")
    throughput_p50_tps = _metric(row, "throughput_last_30m", "p50")
    return {
        "operational_reliability": round(calibrated, 6),
        "reported_uptime_weighted": round(weighted, 6),
        "uptime_volatility": round(volatility, 6),
        "operational_evidence_confidence": round(evidence_confidence, 6),
        "latency_p90_ms": latency_p90_ms,
        "throughput_p50_tps": throughput_p50_tps,
        "uptime_windows_used": [key for key, _, _ in observed],
        "uptime_percentage_normalized": True,
        "cross_task_history_used": False,
    }


def poisson_binomial_tail(
    failure_probabilities: Sequence[float],
    recoverable_failures: int,
) -> float:
    """Return P(number of failures exceeds the available recovery calls)."""
    distribution = [1.0]
    for raw_probability in failure_probabilities:
        probability = _clamp(raw_probability)
        updated = [0.0] * (len(distribution) + 1)
        for failures, mass in enumerate(distribution):
            updated[failures] += mass * (1.0 - probability)
            updated[failures + 1] += mass * probability
        distribution = updated
    boundary = max(-1, int(recoverable_failures))
    return _clamp(sum(distribution[boundary + 1 :]))


def uniform_failure_cap(
    node_count: int,
    recoverable_failures: int,
    maximum_tail_probability: float,
) -> float:
    """Solve a uniform per-node failure cap for the requested tail bound."""
    count = max(0, int(node_count))
    recovery = max(0, int(recoverable_failures))
    target = _clamp(maximum_tail_probability)
    if count <= recovery:
        return 1.0
    low, high = 0.0, 1.0
    for _ in range(64):
        middle = (low + high) / 2.0
        tail = poisson_binomial_tail([middle] * count, recovery)
        if tail <= target:
            low = middle
        else:
            high = middle
    return low


def contract_visible_token_floor(
    candidate: Any,
    task_expected_tokens: int,
) -> tuple[int, bool, int]:
    """Raise visible-token demand to the task-derived explicit contract estimate."""
    profile = getattr(candidate, "parameter_profile", {})
    profile = profile if isinstance(profile, Mapping) else {}
    completion_tokens = max(
        0,
        int(_float(profile.get("estimated_completion_usage_tokens"), 0.0)),
    )
    explicit_contract = bool(
        profile.get("explicit_output_contract_expected")
        or str(profile.get("output_contract_kind") or "") == "exact-markdown"
    )
    task_tokens = max(0, int(task_expected_tokens))
    if not explicit_contract or completion_tokens <= task_tokens:
        return task_tokens, False, completion_tokens
    return completion_tokens, True, completion_tokens


class OperationalResiliencePlannerPolicy(CrossEndpointPlannerPolicy):
    """Calibrate live endpoints and require executable recovery sufficiency."""

    def compile_market(
        self,
        ranked: Sequence[Any],
        resource_bundle: Mapping[str, Any],
        *,
        endpoint_payloads: Mapping[str, Mapping[str, Any]],
        ranking_limit: int,
        allow_synthetic_fixture: bool,
    ) -> dict[str, Any]:
        market = super().compile_market(
            ranked,
            resource_bundle,
            endpoint_payloads=endpoint_payloads,
            ranking_limit=ranking_limit,
            allow_synthetic_fixture=allow_synthetic_fixture,
        )
        raw_by_endpoint: dict[tuple[str, str], Mapping[str, Any]] = {}
        for model_id, payload in endpoint_payloads.items():
            for endpoint in _endpoint_rows(payload):
                slug = _provider_slug(endpoint)
                if slug:
                    raw_by_endpoint[(str(model_id), slug)] = endpoint

        calibrated_rows: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for raw_row in market.get("endpoints", []):
            if not isinstance(raw_row, Mapping):
                continue
            row = dict(raw_row)
            key = (str(row.get("model_id") or ""), str(row.get("provider_slug") or ""))
            profile = calibrate_endpoint_operational_profile(raw_by_endpoint.get(key))
            row["reported_reliability"] = row.get("reliability")
            row["reliability"] = profile["operational_reliability"]
            row.update(profile)
            if float(row["reliability"]) + 1e-12 < cost_policy.MIN_PROVIDER_RELIABILITY:
                removed.append(
                    {
                        "model_id": key[0],
                        "provider_slug": key[1],
                        "operational_reliability": row["reliability"],
                        "reason": "below-calibrated-production-reliability-floor",
                    }
                )
                continue
            calibrated_rows.append(row)

        if not calibrated_rows:
            raise V5PlanningError(
                "No current endpoint satisfies the calibrated production reliability floor."
            )
        result = dict(market)
        result["endpoints"] = calibrated_rows
        result["endpoint_count"] = len(calibrated_rows)
        result["real_endpoint_count"] = sum(
            not bool(row.get("synthetic_fixture_only")) for row in calibrated_rows
        )
        result["synthetic_fixture_count"] = sum(
            bool(row.get("synthetic_fixture_only")) for row in calibrated_rows
        )
        result["model_company_count"] = len(
            {
                canonical_model_company(str(row.get("model_id") or ""))
                for row in calibrated_rows
            }
        )
        result["operational_reliability_calibration"] = {
            "schema_version": "v5-operational-reliability-1",
            "uptime_scale": "accept-0-to-1-or-0-to-100",
            "windows": [key for key, _ in _UPTIME_WINDOWS],
            "prior_reliability": _PRIOR_RELIABILITY,
            "provider_reliability_floor": cost_policy.MIN_PROVIDER_RELIABILITY,
            "removed_endpoint_count": len(removed),
            "removed_endpoints": removed,
            "latency_and_throughput_source": "current-endpoint-snapshot",
            "cross_task_history_used": False,
        }
        planning_policy = dict(result.get("planning_policy") or {})
        planning_policy.update(
            {
                "operational_reliability": "bayesian-shrinkage-current-snapshot",
                "uptime_percentage_normalized": True,
                "deadline_serviceability": "max-task-output-and-explicit-contract-estimate-over-current-throughput",
                "cross_task_history_used": False,
            }
        )
        result["planning_policy"] = planning_policy
        return result

    def candidate_factory(self, *args: Any, **kwargs: Any) -> Any:
        candidate = super().candidate_factory(*args, **kwargs)
        if candidate is None:
            return None
        endpoint = args[4] if len(args) > 4 and isinstance(args[4], Mapping) else {}
        works = args[2] if len(args) > 2 and isinstance(args[2], Sequence) else ()
        discount = max(0.1, _float(kwargs.get("bundle_discount"), 1.0))
        expected_visible_tokens = int(
            math.ceil(
                sum(
                    max(
                        0,
                        int(
                            (
                                work.get("context_requirements", {})
                                if isinstance(work, Mapping)
                                else {}
                            ).get("expected_output_tokens", 0)
                            or 0
                        ),
                    )
                    for work in works
                    if isinstance(work, Mapping)
                )
                * discount
            )
        )
        task_expected_visible_tokens = expected_visible_tokens
        (
            expected_visible_tokens,
            contract_token_floor_applied,
            contract_completion_tokens,
        ) = contract_visible_token_floor(candidate, expected_visible_tokens)
        throughput = _float(endpoint.get("throughput_p50_tps"), 0.0)
        latency_ms = _float(endpoint.get("latency_p90_ms"), 0.0)
        deadline_seconds = max(
            1.0,
            _float(os.environ.get("MODEL_TIMEOUT_SECONDS"), 240.0),
        )
        estimated_seconds: float | None = None
        if throughput > 0.0:
            estimated_seconds = latency_ms / 1_000.0 + expected_visible_tokens / throughput
        headroom = _DEADLINE_HEADROOM_BY_TIER.get(
            str(getattr(self.config, "quality_tier", "value")),
            _DEADLINE_HEADROOM_BY_TIER["value"],
        )
        ratio = None if estimated_seconds is None else estimated_seconds / deadline_seconds
        if ratio is not None and ratio > headroom + 1e-12:
            return None

        if ratio is None:
            timeout_risk = 0.05
        elif ratio <= 0.50:
            timeout_risk = 0.0
        else:
            timeout_risk = min(
                0.25,
                (ratio - 0.50) / max(0.05, headroom - 0.50) * 0.12,
            )
        failure = 1.0 - (
            (1.0 - _clamp(candidate.failure_probability))
            * (1.0 - _clamp(timeout_risk))
        )
        profile = dict(candidate.parameter_profile)
        profile["operational_serviceability"] = {
            "schema_version": "v5-operational-serviceability-1",
            "expected_visible_output_tokens": expected_visible_tokens,
            "task_expected_visible_output_tokens": task_expected_visible_tokens,
            "contract_completion_token_floor": contract_completion_tokens,
            "contract_token_floor_applied": contract_token_floor_applied,
            "throughput_p50_tps": None if throughput <= 0.0 else round(throughput, 6),
            "latency_p90_ms": None if latency_ms <= 0.0 else round(latency_ms, 6),
            "estimated_visible_delivery_seconds": (
                None if estimated_seconds is None else round(estimated_seconds, 6)
            ),
            "request_deadline_seconds": round(deadline_seconds, 6),
            "maximum_deadline_ratio": headroom,
            "estimated_deadline_ratio": None if ratio is None else round(ratio, 6),
            "timeout_failure_probability": round(timeout_risk, 6),
            "deadline_feasible": True,
            "cross_task_history_used": False,
        }
        return replace(
            candidate,
            failure_probability=round(_clamp(failure), 6),
            estimated_cost=round(
                candidate.estimated_cost * (1.0 + timeout_risk * 0.50),
                8,
            ),
            parameter_profile=profile,
        )

    def _assess_recovery_sufficiency(
        self,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        graph = result.get("execution_graph")
        graph = graph if isinstance(graph, Mapping) else {}
        nodes = [row for row in graph.get("nodes", []) if isinstance(row, Mapping)]
        metadata = graph.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        pool = metadata.get("recovery_pool")
        pool = pool if isinstance(pool, Mapping) else {}
        recovery_calls = max(0, int(getattr(self.config, "recovery_call_limit", 0)))
        probabilities = [
            _clamp(_float(row.get("failure_probability"), 1.0)) for row in nodes
        ]
        tail_limit = _RECOVERY_TAIL_LIMIT_BY_TIER.get(
            str(getattr(self.config, "quality_tier", "value")),
            _RECOVERY_TAIL_LIMIT_BY_TIER["value"],
        )
        tail = poisson_binomial_tail(probabilities, recovery_calls)
        missing = [
            str(row.get("node_id") or row.get("candidate_id") or "")
            for row in nodes
            if recovery_calls > 0
            and not list(pool.get(str(row.get("node_id") or row.get("candidate_id") or ""), []) or [])
        ]
        enforced = recovery_calls > 0
        status = "PASS"
        blockers: list[str] = []
        if enforced and tail > tail_limit + 1e-12:
            blockers.append("unrecoverable-failure-tail-above-limit")
        if enforced and missing:
            blockers.append("selected-node-without-executable-recovery")
        if blockers:
            status = "FAIL"
        return {
            "schema_version": "v5-recovery-sufficiency-1",
            "status": status,
            "enforced": enforced,
            "selected_node_count": len(nodes),
            "recovery_call_limit": recovery_calls,
            "selected_failure_probabilities": [round(value, 6) for value in probabilities],
            "unrecoverable_failure_tail_probability": round(tail, 9),
            "maximum_unrecoverable_failure_tail_probability": tail_limit,
            "uniform_candidate_failure_cap": round(
                uniform_failure_cap(len(nodes), recovery_calls, tail_limit),
                9,
            ),
            "nodes_without_executable_recovery": missing,
            "blockers": blockers,
            "policy": "poisson-binomial-current-snapshot-no-history",
            "cross_task_history_used": False,
        }

    @staticmethod
    def _attach_recovery_sufficiency(
        result: Mapping[str, Any],
        assessment: Mapping[str, Any],
        *,
        reoptimized: bool,
    ) -> dict[str, Any]:
        updated = dict(result)
        graph = dict(updated.get("execution_graph") or {})
        metadata = dict(graph.get("metadata") or {})
        evidence = dict(assessment)
        evidence["reoptimized_for_recovery_sufficiency"] = bool(reoptimized)
        metadata["recovery_sufficiency"] = evidence
        graph["metadata"] = metadata
        updated["execution_graph"] = graph
        updated["recovery_sufficiency"] = evidence
        return updated

    def optimize_execution_graph(
        self,
        candidate_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        original = json.loads(json.dumps(candidate_bundle, ensure_ascii=False, default=str))
        first = super().optimize_execution_graph(
            json.loads(json.dumps(original, ensure_ascii=False)),
            **kwargs,
        )
        assessment = self._assess_recovery_sufficiency(first)
        if assessment["status"] == "PASS":
            return self._attach_recovery_sufficiency(
                first,
                assessment,
                reoptimized=False,
            )

        cap = float(assessment["uniform_candidate_failure_cap"])
        filtered = dict(original)
        filtered["candidates"] = [
            dict(row)
            for row in original.get("candidates", [])
            if isinstance(row, Mapping)
            and _float(row.get("failure_probability"), 1.0) <= cap + 1e-12
        ]
        if len(filtered["candidates"]) < len(original.get("candidates", [])):
            try:
                second = super().optimize_execution_graph(filtered, **kwargs)
            except V5PlanningError:
                second = None
            if second is not None:
                second_assessment = self._assess_recovery_sufficiency(second)
                if second_assessment["status"] == "PASS":
                    return self._attach_recovery_sufficiency(
                        second,
                        second_assessment,
                        reoptimized=True,
                    )
                assessment = second_assessment

        raise V5PlanningError(
            "Recovery reserve is statistically or structurally insufficient: "
            + ", ".join(str(value) for value in assessment.get("blockers", []))
        )


__all__ = [
    "OperationalResiliencePlannerPolicy",
    "calibrate_endpoint_operational_profile",
    "poisson_binomial_tail",
    "uniform_failure_cap",
]
