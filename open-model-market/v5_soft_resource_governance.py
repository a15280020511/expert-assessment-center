"""Constitutional soft governance for token, cost and recovery resources.

Token and cost estimates are observable telemetry, never business-level stop
conditions. Recovery identities and companies may be reused when the current
finite execution graph selects them. Structural DAG safety, provider failure
handling, evidence contracts and no-tools isolation remain enforced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import v5_quality_status_integrity as quality_integrity
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode
from v5_constitutional_runtime import (
    ConstitutionalExecutionEngine,
    ConstitutionalPromptPolicy,
)
from v5_runtime import (
    BudgetController,
    FailureCategory,
    ProductionRuntime,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeConfig,
)

SOFT_RESOURCE_INSTRUCTION = (
    "\n\n资源使用宪法：先完整满足任务合同、证据要求和全部必需字段，再以结论优先、"
    "高信息密度、去重和最少无关展开的方式完成。不得为了节省 Token 省略必要内容，"
    "也不得用重复叙述、无关背景或冗长修饰浪费 Token。费用和 Token 仅用于审计、"
    "告警与改进，不是拒绝任务或提前停止的理由。"
    "\nResource constitution: complete every required contract and evidence item first; "
    "then be concise, non-repetitive and information-dense. Do not omit required content "
    "to save tokens. Token and cost estimates are advisory telemetry, not stop conditions."
)


def _without_local_token_caps(
    payload: Mapping[str, Any],
    node: SelectedNode,
) -> dict[str, Any]:
    softened = dict(payload)
    softened.pop("max_tokens", None)
    softened.pop("max_completion_tokens", None)

    reasoning = softened.get("reasoning")
    if isinstance(reasoning, Mapping):
        soft_reasoning = dict(reasoning)
        for key in (
            "max_tokens",
            "max_completion_tokens",
            "budget_tokens",
            "token_budget",
        ):
            soft_reasoning.pop(key, None)
        effort = str(node.reasoning_profile.get("effort") or "").strip()
        if effort:
            soft_reasoning.setdefault("effort", effort)
        if soft_reasoning:
            softened["reasoning"] = soft_reasoning
        else:
            softened.pop("reasoning", None)

    messages = softened.get("messages")
    if (
        isinstance(messages, list)
        and messages
        and isinstance(messages[0], Mapping)
    ):
        updated = list(messages)
        first = dict(updated[0])
        first["content"] = str(first.get("content") or "") + SOFT_RESOURCE_INSTRUCTION
        updated[0] = first
        softened["messages"] = updated
    return softened


class SoftResourcePromptPolicy(ConstitutionalPromptPolicy):
    """Use prompt discipline rather than local token ceilings."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = super().build_payload(node, original_task, upstream)
        return _without_local_token_caps(payload, node)


class SoftResourceBudgetController(BudgetController):
    """Graph-derived budget ledger without model/company recovery uniqueness."""

    def __init__(self, config: RuntimeConfig, graph: ExecutionGraph) -> None:
        super().__init__(config, graph)
        self.replacement_reservations: list[dict[str, str]] = []

    def reserve_replacement_identity(
        self,
        model: str,
        provider_endpoint: str,
        node_id: str,
    ) -> tuple[bool, str]:
        identity = (str(model).strip(), str(provider_endpoint).strip())
        if not identity[0]:
            self.denials.append(
                {
                    "node_id": node_id,
                    "kind": "replacement-identity",
                    "model": identity[0],
                    "provider_endpoint": identity[1],
                    "reason": "invalid-recovery-candidate-model",
                }
            )
            return False, "invalid-recovery-candidate-model"
        self.replacement_reservations.append(
            {
                "node_id": str(node_id),
                "model": identity[0],
                "provider_endpoint": identity[1],
            }
        )
        return True, ""

    def snapshot(self) -> dict[str, Any]:
        value = dict(super().snapshot())
        value["recovery_identity_policy"] = {
            "status": "PASS",
            "policy": "task-graph-selected-recovery-without-company-uniqueness",
            "reservations": list(self.replacement_reservations),
            "duplicate_model_calls_allowed": True,
            "duplicate_company_calls_allowed": True,
            "company_uniqueness_constraint": False,
        }
        return value


class SoftResourceExecutionEngine(ConstitutionalExecutionEngine):
    """Run the constitutional engine without token/cost/company business gates."""

    def _recorded_call(
        self,
        selected: SelectedNode,
        attempts: list[Any],
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[
            [Any, Mapping[str, Any]],
            tuple[Mapping[str, Any], float],
        ],
        budget: BudgetController,
        node: SelectedNode,
        kind: str,
    ) -> Any:
        if kind == "replacement" and isinstance(
            budget, SoftResourceBudgetController
        ):
            if not budget.endpoint_available(node.provider_endpoint):
                return None
            allowed, _ = budget.reserve_replacement_identity(
                node.model,
                node.provider_endpoint,
                selected.node_id,
            )
            if not allowed:
                return None
        return super()._recorded_call(
            selected,
            attempts,
            original_task,
            upstream,
            run,
            call_fn,
            budget,
            node,
            kind,
        )

    def _preflight(
        self,
        graph: ExecutionGraph,
        limits: GraphLimits | None = None,
    ) -> dict[str, Any]:
        value = dict(super()._preflight(graph, limits))
        blockers = [
            str(item)
            for item in value.get("blockers", [])
            if str(item) != "preflight-risk-adjusted-cost-above-anomaly-limit"
        ]
        value.update(
            {
                "status": "rejected" if blockers else "pass",
                "blockers": blockers,
                "cost_limit_enforced": False,
                "cost_threshold_role": "advisory-telemetry-only",
                "token_limit_enforced_by_runtime": False,
                "resource_governance_mode": "prompt-led-soft-governance",
                "recovery_company_uniqueness_required": False,
            }
        )
        return value

    def _execute_graph_soft(
        self,
        graph: ExecutionGraph | Mapping[str, Any],
        run: Any,
        original_task: str,
        *,
        call_fn: Callable[
            [Any, Mapping[str, Any]],
            tuple[Mapping[str, Any], float],
        ]
        | None = None,
        output_dir: str | Path | None = None,
        limits: GraphLimits | None = None,
    ) -> dict[str, Any]:
        graph, limits = self._validated_graph(graph, limits)
        preflight = self._preflight(graph, limits)
        root = Path(output_dir) if output_dir is not None else None
        if preflight["blockers"]:
            self._reject_preflight(root, preflight)
        budget = SoftResourceBudgetController(self.config, graph)
        outputs, records = self._execute_stages(
            graph,
            run,
            original_task,
            call_fn or self._default_call,
            budget,
        )
        state = self._delivery_state(graph, outputs, limits)
        blockers, missing_non_degradable = self._delivery_blockers(
            state, limits
        )
        result = self._execution_result(
            graph,
            outputs,
            records,
            budget,
            preflight,
            limits,
            state,
            blockers,
            missing_non_degradable,
        )
        result["resource_governance"] = {
            "mode": "prompt-led-soft-governance",
            "local_token_ceiling_enforced": False,
            "cost_threshold_can_stop_execution": False,
            "cost_and_token_usage_audited": True,
            "recovery_company_uniqueness_required": False,
            "recovery_identity_reuse_allowed": True,
        }
        result = quality_integrity.enforce_result_integrity(result)
        if root is not None:
            self._write_artifacts(root, result, outputs)
        self._raise_failed_result(result)
        return result

    def execute_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        runtime_kwargs = dict(kwargs)
        original_task, constraints, root = self._prepare_execution_root(
            args, runtime_kwargs
        )
        try:
            result = self._execute_graph_soft(*args, **runtime_kwargs)
        except Exception:
            self._write_failure_evidence(root, original_task, constraints)
            raise
        company_audit = self._actual_company_audit(result)
        evidence_audit = self._evidence_audit(
            original_task,
            str(result.get("final_answer") or ""),
            constraints,
        )
        result["actual_model_company_audit"] = company_audit
        result["evidence_integrity"] = evidence_audit
        result["task_constraints"] = constraints.to_dict()
        self._write_constitutional_audits(
            root, company_audit, evidence_audit
        )
        reason = self._constitutional_failure_reason(
            result, company_audit, evidence_audit, constraints
        )
        if reason:
            self._fail_constitutional_result(result, root, reason)
        self._write_execution_summary(root, result)
        return result


def harden_runtime(runtime: ProductionRuntime) -> ProductionRuntime:
    """Install soft resource policies without global monkey patching."""
    runtime.recovery_policy = RecoveryPolicy(
        replace_categories=tuple(
            dict.fromkeys(
                [
                    *runtime.recovery_policy.replace_categories,
                    FailureCategory.QUALITY_GATE_FAILED,
                ]
            )
        )
    )
    runtime.prompt_policy = SoftResourcePromptPolicy()
    runtime.execution_engine = SoftResourceExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


def _soft_recovery_policy() -> RecoveryPolicy:
    return RecoveryPolicy(
        replace_categories=(
            FailureCategory.UNSUPPORTED_PARAMETER,
            FailureCategory.CONTEXT_OVERFLOW,
            FailureCategory.PROVIDER_INVALID_RESPONSE,
            FailureCategory.OUTPUT_TRUNCATED,
            FailureCategory.PROVIDER_RATE_LIMITED,
            FailureCategory.PROVIDER_TIMEOUT,
            FailureCategory.PROVIDER_EMPTY_RESPONSE,
            FailureCategory.QUALITY_GATE_FAILED,
        )
    )


def build_runtime(
    config: RuntimeConfig,
    *,
    retry_policy: RetryPolicy,
) -> ProductionRuntime:
    return harden_runtime(
        ProductionRuntime(
            config,
            retry_policy=retry_policy,
            recovery_policy=_soft_recovery_policy(),
        )
    )


__all__ = [
    "SOFT_RESOURCE_INSTRUCTION",
    "SoftResourceBudgetController",
    "SoftResourceExecutionEngine",
    "SoftResourcePromptPolicy",
    "build_runtime",
    "harden_runtime",
]
