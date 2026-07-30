"""Install the consolidated R8 production hardening policies for V5."""
from __future__ import annotations

import v5_cost_reliability_hardening as cost_reliability
import v5_r8_executor as resilient
import v5_r8_policy as runtime_policy
import v5_r8_provider_policy as provider_policy

MIN_PROVIDER_RELIABILITY = cost_reliability.MIN_PROVIDER_RELIABILITY
COST_UNCERTAINTY_MULTIPLIER = cost_reliability.COST_UNCERTAINTY_MULTIPLIER
MIN_DEGRADED_WORK_COVERAGE = resilient.MIN_DEGRADED_WORK_COVERAGE

_ORIGINAL_ESTIMATED_COST = cost_reliability._ORIGINAL_ESTIMATED_COST
conservative_estimated_cost = cost_reliability.conservative_estimated_cost
hardened_candidate_for = cost_reliability.hardened_candidate_for
hardened_build_node_payload = cost_reliability.hardened_build_node_payload
robust_extract_answer = cost_reliability.robust_extract_answer
resilient_execute_v5_graph = resilient.resilient_execute_v5_graph


def install() -> None:
    runtime_policy.install()
    cost_reliability.install()
    provider_policy.install()
    resilient.install()
