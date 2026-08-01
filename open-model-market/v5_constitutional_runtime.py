"""Constitutional execution policy layered on the explicit native V5 runtime."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import v5_cost_reliability_hardening as cost_hardening
import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_task_delivery_contract as delivery_contract
from execution_graph import GraphLimits, SelectedNode
from v5_model_company import canonical_model_company
from v5_runtime import (
    BudgetController,
    ExecutionEngine,
    ExecutionFailure,
    FailureCategory,
    ProductionRuntime,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeAttempt,
    RuntimeConfig,
)
from v5_task_constraints import (
    TaskConstraints,
    compile_task_constraints,
    validate_answer_evidence,
)

FORBIDDEN_REQUEST_FIELDS = {
    "tools",
    "tool_choice",
    "plugins",
    "web_search",
    "web_search_options",
    "file_search",
    "browser",
    "code_interpreter",
    "models",
}


def validate_scope_boundaries(task: str, answer: str) -> list[str]:
    """Compatibility facade over the shared structured evidence validator."""
    return validate_answer_evidence(task, answer, compile_task_constraints(task))


class ConstitutionalPromptPolicy:
    """Build provider-locked requests without using the legacy executor module."""

    def build_payload(
        self,
        node: SelectedNode,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        structured = []
        for row in upstream:
            contract = row.get("contract") if isinstance(row, Mapping) else None
            if isinstance(contract, Mapping):
                structured.append(
                    {
                        "node_id": row.get("node_id"),
                        "answer": json.dumps(
                            contract,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            else:
                structured.append(dict(row))
        payload = cost_hardening.hardened_build_node_payload(
            node, original_task, structured
        )
        constraints = compile_task_constraints(original_task)
        messages = payload.get("messages")
        if isinstance(messages, list) and messages and isinstance(messages[0], Mapping):
            system = dynamic_prompt.dynamic_system_prompt(node)
            constitutional = json.dumps(
                constraints.to_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            messages[0] = {
                **dict(messages[0]),
                "content": (
                    system
                    + "\n\n不可覆盖的任务约束："
                    + constitutional
                    + "\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                    "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
                ),
            }
            payload["messages"] = messages
        provider = payload.get("provider")
        if not isinstance(provider, Mapping):
            raise RuntimeError("provider lock missing from node request")
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            raise RuntimeError(
                "provider.only must contain exactly one endpoint provider"
            )
        if provider.get("allow_fallbacks") is not False:
            raise RuntimeError("provider fallbacks must be disabled")
        forbidden = sorted(FORBIDDEN_REQUEST_FIELDS.intersection(payload))
        if forbidden:
            raise RuntimeError(f"forbidden request fields: {forbidden}")
        return payload


class ConstitutionalExecutionEngine(ExecutionEngine):
    """Make semantic, contract, evidence, and company validity success conditions."""

    def _attempt(
        self,
        node: SelectedNode,
        selected_node_id: str,
        kind: str,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
        run: Any,
        call_fn: Callable[
            [Any, Mapping[str, Any]],
            tuple[Mapping[str, Any], float],
        ],
        budget: BudgetController,
        attempt_index: int,
    ) -> RuntimeAttempt | None:
        attempt = super()._attempt(
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
        if attempt is None or attempt.status != "passed" or not attempt.answer:
            return attempt

        constraints = compile_task_constraints(original_task)
        contract_violations = delivery_contract.validate_answer_contract(
            attempt.answer,
            node.output_contract,
            node.parameter_profile,
        )
        evidence_violations = validate_answer_evidence(
            original_task,
            attempt.answer,
            constraints,
        )
        violations = list(
            dict.fromkeys([*contract_violations, *evidence_violations])
        )
        if not violations:
            return attempt

        attempt.status = "quality_gate_failed"
        attempt.gate_reasons = list(
            dict.fromkeys([*attempt.gate_reasons, *violations])
        )
        attempt.failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request_sent=True,
            response_received=True,
            usage_received=bool(attempt.usage),
            actual_cost_usd=self._actual_cost({"usage": attempt.usage}),
            message=";".join(violations),
        ).to_dict()
        return attempt

    @staticmethod
    def _actual_company_audit(
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        successful: list[dict[str, str]] = []
        called: list[dict[str, str]] = []
        for node in result.get("node_results", []):
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or "")
            resolved = str(
                node.get("resolved_model")
                or node.get("selected_model")
                or ""
            )
            status = str(node.get("status") or "")
            if status.startswith("success") and resolved:
                successful.append(
                    {
                        "node_id": node_id,
                        "model": resolved,
                        "company": canonical_model_company(resolved),
                    }
                )
            for attempt in node.get("attempts", []):
                if not isinstance(attempt, Mapping):
                    continue
                model = str(
                    attempt.get("response_model")
                    or attempt.get("model")
                    or ""
                )
                if model:
                    called.append(
                        {
                            "node_id": node_id,
                            "attempt_kind": str(
                                attempt.get("attempt_kind") or ""
                            ),
                            "model": model,
                            "company": canonical_model_company(model),
                            "status": str(attempt.get("status") or ""),
                        }
                    )

        by_company: dict[str, set[str]] = {}
        for row in called:
            by_company.setdefault(row["company"], set()).add(row["node_id"])
        duplicates = {
            company: sorted(nodes)
            for company, nodes in by_company.items()
            if len(nodes) > 1
        }
        unresolved = [
            row for row in called if not row["company"] or row["company"] == "unknown"
        ]
        return {
            "status": "FAIL" if duplicates or unresolved else "PASS",
            "policy": "recompute-from-all-actual-called-models",
            "successful_node_models": successful,
            "all_called_models": called,
            "duplicate_called_companies_across_nodes": duplicates,
            "unresolved_called_companies": unresolved,
            "same_node_retry_is_not_a_second_expert": True,
            "failed_calls_are_included": True,
            "cross_task_history_used": False,
        }

    @staticmethod
    def _strict_limits(
        limits: GraphLimits | None,
        constraints: TaskConstraints,
    ) -> GraphLimits | None:
        if limits is None or constraints.allow_degraded_success:
            return limits
        return replace(
            limits,
            min_required_work_coverage=1.0,
            allow_degraded_success=False,
        )

    def execute_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        original_task = str(
            kwargs.get("original_task")
            or (args[2] if len(args) > 2 else "")
            or ""
        )
        constraints = compile_task_constraints(original_task)
        if "limits" in kwargs:
            kwargs["limits"] = self._strict_limits(kwargs.get("limits"), constraints)
        output_dir = kwargs.get("output_dir")
        root = Path(output_dir) if output_dir is not None else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            self._write_json(root / "task-constraints.json", constraints.to_dict())

        result = super().execute_graph(*args, **kwargs)
        company_audit = self._actual_company_audit(result)
        evidence_violations = validate_answer_evidence(
            original_task,
            str(result.get("final_answer") or ""),
            constraints,
        )
        evidence_audit = {
            "schema_version": "v5-evidence-integrity-1",
            "status": "FAIL" if evidence_violations else "PASS",
            "constraints": constraints.to_dict(),
            "violations": evidence_violations,
            "fact_truth_not_inferred_from_structure": True,
            "upstream_model_claims_are_not_promoted_to_user_facts": True,
        }
        result["actual_model_company_audit"] = company_audit
        result["evidence_integrity"] = evidence_audit
        result["task_constraints"] = constraints.to_dict()

        failed_reason = None
        if company_audit["status"] != "PASS":
            failed_reason = "actual-model-company-uniqueness-violation"
        elif evidence_audit["status"] != "PASS":
            failed_reason = "unsupported-evidence-or-quantity"
        elif (
            result.get("completion_mode") == "degraded"
            and not constraints.allow_degraded_success
        ):
            failed_reason = "degradation-not-authorized-by-user"

        if root is not None:
            self._write_json(root / "actual-model-company-audit.json", company_audit)
            self._write_json(root / "evidence-integrity.json", evidence_audit)

        if failed_reason:
            result["status"] = "failed"
            result["completion_mode"] = "none"
            result["quality_status"] = "failed"
            result["final_answer"] = None
            result["stop_reason"] = failed_reason
            if root is not None:
                self._write_json(
                    root / "v5-execution-summary.json",
                    {key: value for key, value in result.items() if key != "node_results"},
                )
                (root / "v5-final-report.md").write_text(
                    "# V5 execution failed\n\n"
                    f"Constitutional final gate: {failed_reason}.\n",
                    encoding="utf-8",
                )
            raise RuntimeError(failed_reason)

        if root is not None:
            self._write_json(
                root / "v5-execution-summary.json",
                {key: value for key, value in result.items() if key != "node_results"},
            )
        return result


def harden_runtime(runtime: ProductionRuntime) -> ProductionRuntime:
    """Replace owned policies and engine without modifying module globals."""
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
    runtime.prompt_policy = ConstitutionalPromptPolicy()
    runtime.execution_engine = ConstitutionalExecutionEngine(
        runtime.config,
        prompt_policy=runtime.prompt_policy,
        retry_policy=runtime.retry_policy,
        recovery_policy=runtime.recovery_policy,
        quality_policy=runtime.quality_policy,
        output_policy=runtime.output_policy,
    )
    return runtime


def build_runtime(
    config: RuntimeConfig,
    *,
    planner_policy: Any,
    retry_policy: RetryPolicy,
) -> ProductionRuntime:
    runtime = ProductionRuntime(
        config,
        retry_policy=retry_policy,
        recovery_policy=RecoveryPolicy(
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
        ),
        planner_policy=planner_policy,
    )
    return harden_runtime(runtime)
