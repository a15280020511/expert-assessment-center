"""Install the consolidated R8 production hardening policies for V5."""
from __future__ import annotations

import v5_budget_runtime_parity as budget_parity
import v5_cost_reliability_hardening as cost_reliability
import v5_dynamic_configuration as dynamic_configuration
import v5_r8_executor as resilient
import v5_r8_gate_wiring as gate_wiring
import v5_r8_policy as runtime_policy
import v5_r8_provider_policy as provider_policy
import v5_r8_retry_policy as retry_policy
import v5_rejection_audit_policy as rejection_audit
import v5_token_cost_policy as token_cost

MIN_PROVIDER_RELIABILITY = cost_reliability.MIN_PROVIDER_RELIABILITY
COST_UNCERTAINTY_MULTIPLIER = cost_reliability.COST_UNCERTAINTY_MULTIPLIER
MIN_DEGRADED_WORK_COVERAGE = resilient.MIN_DEGRADED_WORK_COVERAGE

_ORIGINAL_ESTIMATED_COST = cost_reliability._ORIGINAL_ESTIMATED_COST
conservative_estimated_cost = token_cost.p95_usage_estimated_cost
hardened_candidate_for = token_cost.usage_audited_candidate_for
hardened_build_node_payload = cost_reliability.hardened_build_node_payload
robust_extract_answer = cost_reliability.robust_extract_answer
resilient_execute_v5_graph = resilient.resilient_execute_v5_graph


def install() -> None:
    runtime_policy.install()
    cost_reliability.install()
    token_cost.install()
    budget_parity.install()
    dynamic_configuration.install()
    provider_policy.install()
    gate_wiring.install()
    retry_policy.install()
    rejection_audit.install()
    resilient.install()
