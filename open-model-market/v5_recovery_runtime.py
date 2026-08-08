"""Construct the execution-only production runtime.

Expert composition and recovery order are supplied by the signed candidate pool
and OR-Tools assignment before execution. Token and cost resources are governed
through dynamic soft optimization and audit telemetry rather than local rejection
gates. The final request is measured after prompt assembly, while current-run
feedback continuously recomputes output/timeout/recovery decisions.
"""
from __future__ import annotations

from v5_compound_fact_provenance import install_compound_fact_provenance
from v5_continuous_spatiotemporal_replanning import (
    install_continuous_spatiotemporal_replanning,
)
from v5_cost_effectiveness_request_policy import (
    install_cost_effective_final_payload_policy,
)
from v5_cost_effectiveness_runtime import (
    install_cost_effective_continuous_runtime,
)
from v5_final_semantic_gate import install_final_semantic_gate
from v5_priority_preserving_heterogeneity import (
    install_priority_preserving_heterogeneity,
)
from v5_production_expert_policy import install_production_expert_policy
from v5_replacement_truncation_rebind import (
    install_replacement_truncation_rebind,
)
from v5_run387_hardening import install_run387_hardening
from v5_soft_resource_governance import build_runtime
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeConfig


def build_production_runtime(config: RuntimeConfig) -> ProductionRuntime:
    """Build the production executor with unrestricted Provider routing.

    Final request-audit hardening is imported only after this module has fully
    initialized. That module legitimately references the compatibility facade,
    which in turn imports this public factory. Keeping the compatibility-only
    import at call time removes the cold-start cycle without changing behavior.
    """
    from v5_final_audit_hardening import install_final_request_audit_hardening

    retry_policy = RetryPolicy(
        retry_same_endpoint_categories=(),
        maximum_same_endpoint_retries_per_node=0,
    )
    runtime = build_runtime(config, retry_policy=retry_policy)
    runtime = install_production_expert_policy(runtime)
    runtime = install_run387_hardening(runtime)
    install_final_semantic_gate()
    install_compound_fact_provenance()
    runtime = install_priority_preserving_heterogeneity(runtime)
    runtime = install_continuous_spatiotemporal_replanning(runtime)
    runtime = install_replacement_truncation_rebind(runtime)
    runtime = install_cost_effective_continuous_runtime(runtime)
    runtime = install_cost_effective_final_payload_policy(runtime)
    install_final_request_audit_hardening()
    return runtime


__all__ = ["build_production_runtime"]
