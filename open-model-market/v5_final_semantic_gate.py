"""Scope task-explicit semantic completeness to final delivery surfaces.

Arithmetic consistency is safe to validate on every expert work product because it
only checks equalities the model explicitly emitted. Whole-task obligation coverage
belongs only on final delivery nodes / the final report; applying it to internal
analysis nodes would create unnecessary recovery calls.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import v5_constitutional_runtime_legacy as constitutional_legacy
import v5_task_constraints as base_constraints
from execution_graph import SelectedNode
from v5_run387_hardening import (
    HeterogeneousEvidenceExecutionEngine,
    arithmetic_consistency_violations,
    task_obligation_violations,
)
from v5_runtime import BudgetController, ExecutionFailure, FailureCategory, RuntimeAttempt


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value)))


def work_product_evidence_validator(
    task: str,
    answer: str,
    constraints: base_constraints.TaskConstraints | None = None,
) -> list[str]:
    """Evidence + explicit math consistency for every node, no whole-task coverage."""
    active = constraints or base_constraints.compile_task_constraints(task)
    violations = list(base_constraints.validate_answer_evidence(task, answer, active))
    violations.extend(arithmetic_consistency_violations(answer))
    return _dedupe(violations)


def _final_attempt_obligation_failure(
    engine: HeterogeneousEvidenceExecutionEngine,
    node: SelectedNode,
    original_task: str,
    attempt: RuntimeAttempt | None,
) -> RuntimeAttempt | None:
    if (
        attempt is None
        or not attempt.answer
        or node.output_contract.get("final_delivery_node") is not True
    ):
        return attempt
    obligations = task_obligation_violations(original_task, attempt.answer)
    if not obligations:
        return attempt
    attempt.gate_reasons = _dedupe([*attempt.gate_reasons, *obligations])
    if attempt.status == "passed":
        attempt.status = "quality_gate_failed"
        attempt.failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request_sent=True,
            response_received=True,
            usage_received=bool(attempt.usage),
            actual_cost_usd=engine._actual_cost({"usage": attempt.usage}),
            message=";".join(obligations),
        ).to_dict()
    return attempt


def install_final_semantic_gate() -> None:
    """Install final-only semantic obligations after run-387 base hardening."""
    constitutional_legacy.validate_answer_evidence = work_product_evidence_validator

    cls = HeterogeneousEvidenceExecutionEngine
    if getattr(cls, "_final_semantic_gate_installed", False):
        return

    original_attempt = cls._attempt
    original_evidence_audit = cls._evidence_audit

    def final_aware_attempt(
        self: HeterogeneousEvidenceExecutionEngine,
        node: SelectedNode,
        selected_node_id: str,
        kind: str,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Any,
        budget: BudgetController,
        attempt_index: int,
    ) -> RuntimeAttempt | None:
        attempt = original_attempt(
            self,
            node,
            selected_node_id,
            kind,
            original_task,
            upstream,
            run,
            call_fn,
            budget,
            attempt_index,
        )
        return _final_attempt_obligation_failure(
            self,
            node,
            original_task,
            attempt,
        )

    @staticmethod
    def final_aware_evidence_audit(
        original_task: str,
        answer: str,
        constraints: base_constraints.TaskConstraints,
        *,
        after_failure: bool = False,
    ) -> dict[str, Any]:
        payload = dict(
            original_evidence_audit(
                original_task,
                answer,
                constraints,
                after_failure=after_failure,
            )
        )
        obligations = task_obligation_violations(original_task, answer)
        violations = _dedupe([*payload.get("violations", []), *obligations])
        payload["violations"] = violations
        payload["status"] = "FAIL" if violations else "PASS"
        payload["task_explicit_obligation_gate"] = {
            "status": "FAIL" if obligations else "PASS",
            "scope": "final-delivery-only",
            "violations": obligations,
            "internal_nodes_required_to_answer_full_task": False,
        }
        return payload

    cls._attempt = final_aware_attempt
    cls._evidence_audit = final_aware_evidence_audit
    cls._final_semantic_gate_installed = True


__all__ = [
    "install_final_semantic_gate",
    "work_product_evidence_validator",
]
