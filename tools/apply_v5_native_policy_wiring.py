#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_capability() -> None:
    path = MARKET / "v5_capability_calibration.py"
    text = path.read_text(encoding="utf-8")
    text = once(
        text,
        '''def generate_calibrated_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
) -> dict[str, Any]:
    """Generate candidates using rank-backed hard-capability proxy eligibility."""
''',
        '''def generate_calibrated_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
    candidate_factory: Any | None = None,
    pruner: Any | None = None,
) -> dict[str, Any]:
    """Generate candidates with explicitly supplied policy functions."""
    candidate_factory = candidate_factory or v5_planner._candidate_for
    pruner = pruner or v5_planner.pareto_prune
''',
        "capability signature",
    )
    if text.count("v5_planner._candidate_for(") != 2:
        raise RuntimeError("unexpected candidate factory call count")
    text = text.replace("v5_planner._candidate_for(", "candidate_factory(")
    text = once(
        text,
        "    pruned = v5_planner.pareto_prune(\n",
        "    pruned = pruner(\n",
        "capability pruner",
    )
    path.write_text(text, encoding="utf-8")


def patch_runtime() -> None:
    path = MARKET / "v5_runtime.py"
    text = path.read_text(encoding="utf-8")
    text = once(
        text,
        "from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json\n",
        "from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json\nfrom v5_planning_runtime import PlannerPolicy\n",
        "planner import",
    )
    text = once(
        text,
        '        if not 4 <= int(self.total_call_limit) <= 16:\n            raise ValueError("total_call_limit must be between 4 and 16")\n',
        '        if not 1 <= int(self.total_call_limit) <= 16:\n            raise ValueError("total_call_limit must be between 1 and 16")\n',
        "runtime call range",
    )
    text = once(
        text,
        '''                "quality_integrity_status": result.get("quality_integrity", {}).get("status"),
''',
        '''                "quality_integrity_status": result.get("quality_integrity", {}).get("status"),
                "degraded_synthesis_is_deterministic": bool(
                    result.get("degradation", {}).get("used")
                ),
''',
        "audit deterministic field",
    )
    text = once(
        text,
        '''                if category in {
                    FailureCategory.PROVIDER_RATE_LIMITED,
                    FailureCategory.PROVIDER_TIMEOUT,
                    FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    FailureCategory.PROVIDER_INVALID_RESPONSE,
                    FailureCategory.UNSUPPORTED_PARAMETER,
                    FailureCategory.CONTEXT_OVERFLOW,
                    FailureCategory.OUTPUT_TRUNCATED,
                }:
''',
        '''                if category in {
                    FailureCategory.PROVIDER_RATE_LIMITED,
                    FailureCategory.PROVIDER_TIMEOUT,
                    FailureCategory.PROVIDER_EMPTY_RESPONSE,
                    FailureCategory.UNSUPPORTED_PARAMETER,
                    FailureCategory.CONTEXT_OVERFLOW,
                }:
''',
        "provider circuit categories",
    )
    old = '''        content_work = self._content_work_ids(graph)
        best_by_work = self._best_outputs_by_work(graph, outputs)
        covered = set(best_by_work)
        missing = sorted(content_work - covered)
        coverage = len(covered) / max(1, len(content_work))
        complete_nodes = (
            len(outputs) == len(graph.nodes)
            and all(row.status.startswith("success") for row in outputs.values())
        )
        degradation_used = False
        final_answer = preferred_final
        if not final_answer and coverage >= MIN_DEGRADED_WORK_COVERAGE:
            final_answer = self._degraded_synthesis(best_by_work, missing)
            degradation_used = True
        elif preferred_final and (missing or not complete_nodes):
            degradation_used = True

        if final_answer and not degradation_used and complete_nodes and not missing:
            status = "success"
            completion_mode = "full"
            quality_status = "full_success"
            stop_reason = "all-quality-gates-passed"
        elif final_answer and coverage >= MIN_DEGRADED_WORK_COVERAGE:
            status = "success"
            completion_mode = "degraded"
            quality_status = "degraded_success"
            stop_reason = "partial-success-deterministic-synthesis"
        else:
            status = "failed"
            completion_mode = "none"
            quality_status = "failed"
            stop_reason = "insufficient-work-coverage-after-recovery"
'''
    new = '''        optional_work = {
            str(value)
            for value in graph.metadata.get("optional_work_ids", [])
        } if isinstance(graph.metadata, Mapping) else set()
        non_degradable_work = {
            str(value)
            for value in graph.metadata.get("non_degradable_work_ids", [])
        } if isinstance(graph.metadata, Mapping) else set()
        content_work = self._content_work_ids(graph) - optional_work
        best_by_work = {
            work_id: result
            for work_id, result in self._best_outputs_by_work(graph, outputs).items()
            if work_id in content_work
        }
        covered = set(best_by_work)
        missing = sorted(content_work - covered)
        coverage = len(covered) / max(1, len(content_work))
        successful_content_nodes = len({
            result.node_id for result in best_by_work.values()
        })
        complete_nodes = (
            len(outputs) == len(graph.nodes)
            and all(row.status.startswith("success") for row in outputs.values())
        )
        minimum_coverage = max(0.0, min(1.0, float(limits.min_required_work_coverage)))
        degradation_used = False
        final_answer = preferred_final
        if not final_answer and coverage >= minimum_coverage:
            final_answer = self._degraded_synthesis(best_by_work, missing)
            degradation_used = True
        elif preferred_final and (missing or not complete_nodes):
            degradation_used = True

        delivery_blockers: list[str] = []
        missing_non_degradable = sorted(non_degradable_work.intersection(missing))
        if missing_non_degradable:
            delivery_blockers.append("missing-non-degradable-work")
        if coverage + 1e-12 < minimum_coverage:
            delivery_blockers.append("insufficient-required-work-coverage")
        if successful_content_nodes < int(limits.min_successful_content_nodes):
            delivery_blockers.append("insufficient-successful-content-nodes")
        if degradation_used and not limits.allow_degraded_success:
            delivery_blockers.append("degraded-success-disabled")

        if final_answer and not degradation_used and complete_nodes and not missing:
            status = "success"
            completion_mode = "full"
            quality_status = "full_success"
            stop_reason = "all-quality-gates-passed"
        elif final_answer and not delivery_blockers:
            status = "success"
            completion_mode = "degraded"
            quality_status = "degraded_success"
            stop_reason = "partial-success-deterministic-synthesis"
        else:
            status = "failed"
            completion_mode = "none"
            quality_status = "failed"
            stop_reason = delivery_blockers[0] if delivery_blockers else "insufficient-work-coverage-after-recovery"
'''
    text = once(text, old, new, "delivery gate block")
    text = once(
        text,
        '''                "minimum_degraded_coverage": MIN_DEGRADED_WORK_COVERAGE,
            },
            "degradation": {
''',
        '''                "minimum_degraded_coverage": minimum_coverage,
                "successful_content_nodes": successful_content_nodes,
            },
            "delivery_policy": {
                "optional_work_ids": sorted(optional_work),
                "non_degradable_work_ids": sorted(non_degradable_work),
                "missing_non_degradable_work_ids": missing_non_degradable,
                "minimum_required_work_coverage": minimum_coverage,
                "minimum_successful_content_nodes": int(limits.min_successful_content_nodes),
                "allow_degraded_success": bool(limits.allow_degraded_success),
                "blockers": delivery_blockers,
            },
            "degradation": {
''',
        "delivery evidence",
    )
    text = once(
        text,
        '''        if status == "failed":
            raise RuntimeError("V5 execution could not reach the minimum audited work-coverage gate")
''',
        '''        if status == "failed":
            if stop_reason == "insufficient-successful-content-nodes":
                raise RuntimeError("insufficient-successful-content-nodes")
            if stop_reason in {"missing-non-degradable-work", "degraded-success-disabled"}:
                raise RuntimeError("V5 execution failed production delivery policy")
            raise RuntimeError("V5 execution could not reach the minimum audited work-coverage gate")
''',
        "delivery exception",
    )
    text = once(
        text,
        '''    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)

    def __post_init__(self) -> None:
        self.execution_engine = ExecutionEngine(
''',
        '''    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)
    planner_policy: Any | None = None

    def __post_init__(self) -> None:
        if self.planner_policy is None:
            self.planner_policy = PlannerPolicy(self.config)
        self.execution_engine = ExecutionEngine(
''',
        "runtime planner field",
    )
    text = once(
        text,
        '''                "audit": asdict(self.audit_policy),
            },
''',
        '''                "audit": asdict(self.audit_policy),
                "planner": {
                    "implementation": type(self.planner_policy).__name__,
                    "composition": "explicit-direct-call",
                },
            },
''',
        "planner description",
    )
    path.write_text(text, encoding="utf-8")


def patch_pipeline() -> None:
    path = MARKET / "v5_pipeline.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import v5_value_optimizer as value_optimizer\n", "")
    text = once(
        text,
        '''    compiled_market = value_optimizer.compile_model_endpoint_market(
''',
        '''    compiled_market = runtime.planner_policy.compile_market(
''',
        "compile market call",
    )
    text = once(
        text,
        '''        candidate_graph = value_optimizer.generate_candidate_graph(
''',
        '''        candidate_graph = runtime.planner_policy.generate_candidate_graph(
''',
        "candidate call",
    )
    text = once(
        text,
        '''        optimization = value_optimizer.optimize_execution_graph(
''',
        '''        optimization = runtime.planner_policy.optimize_execution_graph(
''',
        "optimizer call",
    )
    path.write_text(text, encoding="utf-8")


def patch_compatibility_wrapper() -> None:
    path = MARKET / "v5_production_hardening.py"
    text = path.read_text(encoding="utf-8")
    text = once(
        text,
        "        max_provider_failures=max(1, int(limits.max_provider_failures)),\n",
        "        max_provider_failures=max(2, int(limits.max_provider_failures)),\n",
        "compat provider circuit",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_capability()
    patch_runtime()
    patch_pipeline()
    patch_compatibility_wrapper()
    print("native policy wiring applied")


if __name__ == "__main__":
    main()
