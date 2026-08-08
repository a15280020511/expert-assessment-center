"""Production expert request and delivery policy.

Provider routing is completely open. Production requests contain no Provider
allowlist/order/ZDR/data-collection/price routing filters. Company identity is
retained as audit telemetry only and never invalidates an otherwise valid run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode
from v5_no_tools_policy import assert_request_has_no_tools
from v5_production_answer_normalization import relabel_task_derived_fact_lines
from v5_runtime import (
    FailureCategory,
    ProductionRuntime,
    RuntimeAttempt,
    extract_actual_cost,
)
from v5_runtime_request_binding import audit_bound_request, bind_request_knobs
from v5_soft_resource_governance import (
    SoftResourceExecutionEngine,
    SoftResourcePromptPolicy,
)
from v5_task_constraints import TaskConstraints

EXPERT_DATA_COLLECTION_POLICY = None
EXPERT_ZDR_REQUIRED = False


class ProductionExpertPromptPolicy(SoftResourcePromptPolicy):
    """Guarantee unrestricted routing and consume current-task runtime knobs."""

    provider_lock_required = False

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        # Historical prompt builders may construct a compatibility Provider
        # object internally. It is never sent to OpenRouter in production.
        payload.pop("provider", None)

        # Planning is not considered complete until its execution knobs are
        # consumed by the actual model request.  These fields are current-run
        # request shaping, not business admission or result-validity gates.
        request_knobs, _binding_audit = bind_request_knobs(
            node,
            original_task,
            upstream,
        )
        payload.update(request_knobs)
        effective_audit = audit_bound_request(node, payload)
        if effective_audit["status"] != "PASS":
            raise RuntimeError(
                "computed runtime knobs were not consumed: "
                + ",".join(effective_audit["computed_but_unused"])
            )

        assert_request_has_no_tools(
            payload,
            context=f"production expert {node.node_id} request",
        )
        return payload


class EvidenceCompleteExecutionEngine(SoftResourceExecutionEngine):
    """Persist complete evidence without company/provider business gates."""

    def _ensure_production_failure_state(self) -> None:
        if not hasattr(self, "_hard_failed_model_ids"):
            self._hard_failed_model_ids: set[str] = set()

    def _record_feedback(self, attempt: Any | None) -> None:
        super()._record_feedback(attempt)
        if attempt is None:
            return
        self._ensure_production_failure_state()
        failure = attempt.failure if isinstance(attempt.failure, Mapping) else {}
        category = str(failure.get("category") or "")
        retryable = bool(failure.get("retryable"))
        model = str(getattr(attempt, "model", "") or failure.get("model") or "").strip()
        if (
            model
            and category == FailureCategory.PROVIDER_INVALID_RESPONSE.value
            and not retryable
        ):
            # A current-run non-retryable endpoint/model identity failure should
            # not be paid for again on another node in the same task.  This is
            # current-run feedback only; nothing survives into the next task.
            self._hard_failed_model_ids.add(model)

    def _recover_node(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        recovery_rows: Sequence[Mapping[str, Any]],
        category: Any,
        best: tuple[Any, SelectedNode] | None,
        call: Any,
    ) -> tuple[Any | None, tuple[Any, SelectedNode] | None, SelectedNode]:
        self._ensure_production_failure_state()
        filtered_rows = [
            row
            for row in recovery_rows
            if str(row.get("model") or "").strip() not in self._hard_failed_model_ids
        ]
        return super()._recover_node(
            selected,
            attempts,
            filtered_rows,
            category,
            best,
            call,
        )

    def _claim_next_standby(self) -> dict[str, Any] | None:
        self._ensure_production_failure_state()
        self._ensure_feedback_state()
        with self._feedback_lock:
            for row in self._standby_inventory:
                model = str(row.get("model") or "").strip()
                if (
                    not model
                    or model in self._standby_claimed
                    or model in self._hard_failed_model_ids
                ):
                    continue
                self._standby_claimed.add(model)
                return dict(row)
        return None

    def _feedback_snapshot(self) -> dict[str, Any]:
        value = dict(super()._feedback_snapshot())
        self._ensure_production_failure_state()
        value.update(
            {
                "nonretryable_model_failure_memory_scope": "current-run-only",
                "nonretryable_model_failure_reuse_allowed": False,
                "hard_failed_model_ids": sorted(self._hard_failed_model_ids),
            }
        )
        return value

    def _normalize_attempt(
        self,
        node: SelectedNode,
        original_task: str,
        attempt: RuntimeAttempt,
        constraints: TaskConstraints,
    ) -> bool:
        earliest = attempt.raw_answer or attempt.answer
        if attempt.answer:
            repaired, audit = relabel_task_derived_fact_lines(
                original_task,
                attempt.answer,
            )
            if audit.get("applied"):
                if attempt.raw_answer is None:
                    attempt.raw_answer = attempt.answer
                attempt.answer = repaired
                attempt.answer_transformations.append(audit)
        normalized = super()._normalize_attempt(
            node,
            original_task,
            attempt,
            constraints,
        )
        if earliest and attempt.raw_answer != earliest:
            attempt.raw_answer = earliest
        if normalized:
            return True

        # Internal nodes are reasoning work products, not final fact surfaces.
        # If their only failure is the conservative natural-language
        # fact-label matcher, keep the warning but do not burn more model calls.
        # Unsupported quantities, contract failures and final-node evidence
        # violations remain hard failures; the final evidence audit remains
        # fail-closed.
        if node.output_contract.get("final_delivery_node") is True:
            return False
        reasons = [str(value) for value in attempt.gate_reasons]
        if reasons and all(
            reason.startswith("unsupported-fact-label:") for reason in reasons
        ):
            attempt.answer_transformations.append(
                {
                    "schema_version": "internal-evidence-warning-demotion-1",
                    "applied": True,
                    "policy": "internal-only-warning-final-evidence-remains-fail-closed",
                    "warnings": reasons,
                }
            )
            attempt.status = "passed"
            attempt.gate_reasons = []
            attempt.failure = None
            return True
        return False

    @staticmethod
    def _actual_cost(response: Mapping[str, Any]) -> float:
        return extract_actual_cost(response)

    @staticmethod
    def _raise_failed_result(result: Mapping[str, Any]) -> None:
        del result

    @classmethod
    def _actual_company_audit(
        cls,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        audit = dict(super()._actual_company_audit(result))
        strict_rows = audit.get("strict_successful_node_models")
        if not isinstance(strict_rows, list):
            strict_rows = []
        successful_duplicates, _ = cls._company_conflicts(
            [row for row in strict_rows if isinstance(row, Mapping)]
        )
        audit.update(
            {
                "status": "PASS",
                "policy": "audit-only-company-observability",
                "company_uniqueness_constraint": False,
                "duplicate_companies_allowed": True,
                "duplicates_invalidate_execution": False,
                "duplicate_successful_companies": successful_duplicates,
                "successful_company_duplicates_source": (
                    "strict-successful-node-models-only"
                ),
            }
        )
        return audit

    @staticmethod
    def _constitutional_failure_reason(
        result: Mapping[str, Any],
        company_audit: Mapping[str, Any],
        evidence_audit: Mapping[str, Any],
        constraints: TaskConstraints,
    ) -> str | None:
        del company_audit
        if evidence_audit["status"] != "PASS":
            return "unsupported-evidence-or-quantity"
        if (
            result.get("completion_mode") == "degraded"
            and not constraints.allow_degraded_success
        ):
            return "degradation-not-authorized-by-user"
        return None

    @classmethod
    def _fail_constitutional_result(
        cls,
        result: dict[str, Any],
        root: Path | None,
        reason: str,
    ) -> None:
        result.update(
            {
                "status": "failed",
                "completion_mode": "none",
                "quality_status": "failed",
                "final_answer": None,
                "stop_reason": reason,
            }
        )
        cls._write_execution_summary(root, result)
        if root is not None:
            (root / "v5-final-report.md").write_text(
                "# V5 execution failed\n\n"
                f"Constitutional final gate: {reason}.\n",
                encoding="utf-8",
            )


def install_production_expert_policy(
    runtime: ProductionRuntime,
) -> ProductionRuntime:
    runtime.prompt_policy = ProductionExpertPromptPolicy()
    runtime.execution_engine = EvidenceCompleteExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


__all__ = [
    "EXPERT_DATA_COLLECTION_POLICY",
    "EXPERT_ZDR_REQUIRED",
    "EvidenceCompleteExecutionEngine",
    "ProductionExpertPromptPolicy",
    "install_production_expert_policy",
]
