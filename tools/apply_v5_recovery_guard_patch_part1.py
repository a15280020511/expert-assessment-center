from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return updated


# 1. Deadline serviceability must respect the task-derived contract token estimate.
path = "open-model-market/v5_operational_resilience.py"
text = read(path)
marker = "\n\nclass OperationalResiliencePlannerPolicy(CrossEndpointPlannerPolicy):\n"
helper = '''\n\ndef contract_visible_token_floor(\n    candidate: Any,\n    task_expected_tokens: int,\n) -> tuple[int, bool, int]:\n    """Raise visible-token demand to the task-derived explicit contract estimate."""\n    profile = getattr(candidate, "parameter_profile", {})\n    profile = profile if isinstance(profile, Mapping) else {}\n    completion_tokens = max(\n        0,\n        int(_float(profile.get("estimated_completion_usage_tokens"), 0.0)),\n    )\n    explicit_contract = bool(\n        profile.get("explicit_output_contract_expected")\n        or str(profile.get("output_contract_kind") or "") == "exact-markdown"\n    )\n    task_tokens = max(0, int(task_expected_tokens))\n    if not explicit_contract or completion_tokens <= task_tokens:\n        return task_tokens, False, completion_tokens\n    return completion_tokens, True, completion_tokens\n\n\nclass OperationalResiliencePlannerPolicy(CrossEndpointPlannerPolicy):\n'''
text = replace_once(text, marker, helper, "insert contract token floor")
old = '''        throughput = _float(endpoint.get("throughput_p50_tps"), 0.0)\n'''
new = '''        task_expected_visible_tokens = expected_visible_tokens\n        (\n            expected_visible_tokens,\n            contract_token_floor_applied,\n            contract_completion_tokens,\n        ) = contract_visible_token_floor(candidate, expected_visible_tokens)\n        throughput = _float(endpoint.get("throughput_p50_tps"), 0.0)\n'''
text = replace_once(text, old, new, "apply contract token floor")
old = '''            "expected_visible_output_tokens": expected_visible_tokens,\n'''
new = '''            "expected_visible_output_tokens": expected_visible_tokens,\n            "task_expected_visible_output_tokens": task_expected_visible_tokens,\n            "contract_completion_token_floor": contract_completion_tokens,\n            "contract_token_floor_applied": contract_token_floor_applied,\n'''
text = replace_once(text, old, new, "record contract token floor")
old = '''                "deadline_serviceability": "expected-visible-output-over-current-throughput",\n'''
new = '''                "deadline_serviceability": "max-task-output-and-explicit-contract-estimate-over-current-throughput",\n'''
text = replace_once(text, old, new, "update deadline policy evidence")
write(path, text)


# 2. Recovery planning must enforce the risk-adjusted remaining guard and rank by value.
path = "open-model-market/v5_cross_endpoint_planner.py"
text = read(path)
pattern = r'''    @classmethod\n    def _recovery_sort_key\(\n.*?\n    def rebalance_recovery_pool\('''
replacement = '''    def _recovery_cost_risk_multiplier(\n        self,\n        row: Mapping[str, Any],\n    ) -> float:\n        profile = row.get("parameter_profile")\n        profile = profile if isinstance(profile, Mapping) else {}\n        p95 = max(1.0, float(profile.get("p95_token_usage_multiplier", 1.0) or 1.0))\n        structured = max(\n            1.0,\n            float(profile.get("structured_p95_token_usage_multiplier", 1.0) or 1.0),\n        )\n        uncertainty = max(\n            0.0,\n            min(1.0, float(row.get("quality_uncertainty", 0.0) or 0.0)),\n        )\n        serviceability = profile.get("operational_serviceability")\n        serviceability = serviceability if isinstance(serviceability, Mapping) else {}\n        try:\n            deadline_ratio = max(\n                0.0,\n                float(serviceability.get("estimated_deadline_ratio") or 0.0),\n            )\n        except (TypeError, ValueError):\n            deadline_ratio = 0.0\n        deadline_multiplier = 1.0 + max(0.0, deadline_ratio - 0.50)\n        return max(\n            1.0,\n            float(self.config.cost_risk_multiplier),\n            p95 * structured,\n            1.0 + uncertainty,\n            deadline_multiplier,\n        )\n\n    def _risk_adjusted_recovery_cost(\n        self,\n        row: Mapping[str, Any],\n    ) -> float:\n        cost = max(0.0, float(row.get("estimated_cost", 0.0) or 0.0))\n        return cost * self._recovery_cost_risk_multiplier(row)\n\n    def _recovery_sort_key(\n        self,\n        row: Mapping[str, Any],\n        selected_provider: str,\n        *,\n        critical_delivery: bool,\n    ) -> tuple[Any, ...]:\n        provider = self._provider(row)\n        cost = max(\n            0.0,\n            float(row.get("estimated_cost", 0.0) or 0.0),\n        )\n        failure = max(\n            0.0,\n            min(\n                1.0,\n                float(row.get("failure_probability", 1.0) or 1.0),\n            ),\n        )\n        quality = max(\n            0.01,\n            float(row.get("estimated_quality", 0.0) or 0.0),\n        )\n        uncertainty = max(\n            0.0,\n            min(\n                1.0,\n                float(row.get("quality_uncertainty", 1.0) or 1.0),\n            ),\n        )\n        delivery_utility = max(0.01, self._delivery_utility(row))\n        risk_adjusted_cost = self._risk_adjusted_recovery_cost(row)\n        effective_cost_per_delivery = risk_adjusted_cost / delivery_utility\n        profile = row.get("parameter_profile")\n        profile = profile if isinstance(profile, Mapping) else {}\n        serviceability = profile.get("operational_serviceability")\n        serviceability = serviceability if isinstance(serviceability, Mapping) else {}\n        try:\n            deadline_ratio = max(\n                0.0,\n                float(serviceability.get("estimated_deadline_ratio") or 0.0),\n            )\n        except (TypeError, ValueError):\n            deadline_ratio = 0.0\n        if critical_delivery:\n            return (\n                provider == selected_provider,\n                effective_cost_per_delivery,\n                failure,\n                uncertainty,\n                deadline_ratio,\n                cost,\n                -quality,\n                candidate_company(row),\n                str(row.get("candidate_id") or ""),\n            )\n        return (\n            provider == selected_provider,\n            effective_cost_per_delivery,\n            failure,\n            deadline_ratio,\n            cost,\n            -quality,\n            candidate_company(row),\n            str(row.get("candidate_id") or ""),\n        )\n\n    def rebalance_recovery_pool('''
text = regex_once(text, pattern, replacement, "replace recovery ranking")
old = '''            estimated_above_planning_budget_by_node[node_id] = (\n                0\n                if remaining_recovery_budget is None\n                else sum(\n                    1\n                    for row in alternatives\n                    if max(\n                        0.0,\n                        float(row.get("estimated_cost", 0.0) or 0.0),\n                    )\n                    > remaining_recovery_budget + 1e-12\n                )\n            )\n            # The absolute anomaly guard is a hard admission boundary, so a\n            # candidate whose own estimate exceeds it can never execute. The\n            # estimated remaining budget is different: initial calls reconcile\n            # against provider-billed actual cost, so candidates within the\n            # absolute cap remain available for the live ledger to admit.\n            budget_excluded_by_node[node_id] = 0\n'''
new = '''            estimated_above_planning_budget_by_node[node_id] = (\n                0\n                if remaining_recovery_budget is None\n                else sum(\n                    1\n                    for row in alternatives\n                    if max(\n                        0.0,\n                        float(row.get("estimated_cost", 0.0) or 0.0),\n                    )\n                    > remaining_recovery_budget + 1e-12\n                )\n            )\n            budget_excluded_by_node[node_id] = (\n                0\n                if remaining_recovery_budget is None\n                else sum(\n                    1\n                    for row in alternatives\n                    if self._risk_adjusted_recovery_cost(row)\n                    > remaining_recovery_budget + 1e-12\n                )\n            )\n            if remaining_recovery_budget is not None:\n                alternatives = [\n                    row\n                    for row in alternatives\n                    if self._risk_adjusted_recovery_cost(row)\n                    <= remaining_recovery_budget + 1e-12\n                ]\n'''
text = replace_once(text, old, new, "enforce risk-adjusted remaining budget")
old = '''                payload["planning_budget_advisory_only"] = True\n                payload["absolute_cost_cap_feasible"] = True\n                payload[\n                    "estimated_cost_above_planning_remaining_budget"\n                ] = bool(\n                    remaining_recovery_budget is not None\n                    and estimated_cost\n                    > remaining_recovery_budget + 1e-12\n                )\n'''
new = '''                risk_multiplier = self._recovery_cost_risk_multiplier(row)\n                risk_adjusted_cost = estimated_cost * risk_multiplier\n                payload["planning_budget_advisory_only"] = False\n                payload["absolute_cost_cap_feasible"] = True\n                payload["estimated_cost_above_planning_remaining_budget"] = False\n                payload["recovery_cost_risk_multiplier"] = round(\n                    risk_multiplier,\n                    8,\n                )\n                payload["recovery_risk_adjusted_cost_usd"] = round(\n                    risk_adjusted_cost,\n                    8,\n                )\n                parameter_profile = payload.get("parameter_profile")\n                parameter_profile = (\n                    dict(parameter_profile)\n                    if isinstance(parameter_profile, Mapping)\n                    else {}\n                )\n                parameter_profile["recovery_cost_risk_multiplier"] = round(\n                    risk_multiplier,\n                    8,\n                )\n                parameter_profile["recovery_risk_adjusted_cost_usd"] = round(\n                    risk_adjusted_cost,\n                    8,\n                )\n                payload["parameter_profile"] = parameter_profile\n'''
text = replace_once(text, old, new, "record recovery cost risk")
text = replace_once(
    text,
    '            "critical_delivery_utility_first": True,\n',
    '            "critical_delivery_cost_effectiveness_after_utility_floor": True,\n',
    "update critical recovery policy",
)
text = replace_once(
    text,
    '            "planning_estimated_budget_advisory_only": True,\n',
    '            "planning_estimated_budget_advisory_only": False,\n            "risk_adjusted_remaining_budget_enforced_at_planning": True,\n',
    "update planning budget policy",
)
text = replace_once(
    text,
    '            "recovery_candidates_retained_for_live_ledger_admission": True,\n',
    '            "recovery_candidates_retained_for_live_ledger_admission": False,\n            "runtime_ledger_revalidates_frozen_risk_multiplier": True,\n',
    "update live ledger policy",
)
write(path, text)
