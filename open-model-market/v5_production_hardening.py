"""Install all production hardening policies for the V5 expert graph."""
from __future__ import annotations

import v5_cost_reliability_hardening as cost_reliability
import v5_cutover_readiness as cutover_readiness
import v5_resilient_executor as resilient

MIN_PROVIDER_RELIABILITY = cost_reliability.MIN_PROVIDER_RELIABILITY
COST_UNCERTAINTY_MULTIPLIER = cost_reliability.COST_UNCERTAINTY_MULTIPLIER
MIN_DEGRADED_WORK_COVERAGE = resilient.MIN_DEGRADED_WORK_COVERAGE

_ORIGINAL_ESTIMATED_COST = cost_reliability._ORIGINAL_ESTIMATED_COST
conservative_estimated_cost = cost_reliability.conservative_estimated_cost
hardened_candidate_for = cost_reliability.hardened_candidate_for
hardened_build_node_payload = cost_reliability.hardened_build_node_payload
robust_extract_answer = cost_reliability.robust_extract_answer
resilient_execute_v5_graph = resilient.resilient_execute_v5_graph
full_success_for_cutover = cutover_readiness.full_success_for_cutover


def install() -> None:
    cost_reliability.install()
    resilient.install()
    cutover_readiness.install()
