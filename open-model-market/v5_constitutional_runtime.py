"""Constitutional execution policy layered on the explicit native V5 runtime."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import v5_cost_reliability_hardening as cost_hardening
import v5_dynamic_prompt_delivery as dynamic_prompt
import v5_task_delivery_contract as delivery_contract
from v5_deterministic_answer_normalization import normalize_answer
from execution_graph import GraphLimits, SelectedNode
from v5_model_company import canonical_model_company
from v5_no_tools_policy import assert_request_has_no_tools
from v5_runtime import (
    BudgetController,
    ExecutionEngine,
    ExecutionFailure,
    FailureCategory,
    RuntimeAttempt,
)
from v5_task_constraints import (
    TaskConstraints,
    closed_world_numeric_prompt,
    compile_task_constraints,
    validate_answer_evidence,
)

def validate_scope_boundaries(task: str, answer: str) -> list[str]:
    """Compatibility facade over the shared structured evidence validator."""
    return validate_answer_evidence(task, answer, compile_task_constraints(task))


class ConstitutionalPromptPolicy:
    """Build provider-locked requests without using the legacy executor module."""

    @staticmethod
    def _compact_upstream_contract(
        contract: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Remove only the duplicate raw mirror; preserve every substantive field."""
        return {
            str(key): value
            for key, value in contract.items()
            if str(key) != "raw_fields"
        }

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
                            self._compact_upstream_contract(contract),
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        ),
                    }
                )
            else:
                structured.append(dict(row))
        node_task = delivery_contract.project_task_for_node(
            original_task,
            node.output_contract,
        )
        payload = cost_hardening.hardened_build_node_payload(
            node,
            node_task,
            structured,
        )
        constraints = compile_task_constraints(original_task)
        numeric_policy = closed_world_numeric_prompt(original_task, constraints)
        delivery_discipline = ""
        if bool(node.output_contract.get("explicit_markdown_contract")):
            delivery_discipline = (
                "\n显式长篇合同交付纪律：先按顺序生成全部指定H2标题并确保每节非空，"
                "再补充细节。若输出空间紧张，压缩重复事实、表格和修饰语，"
                "不得遗漏标题、改变顺序、增加其他H2或用冗长复述耗尽输出。"
            )
        messages = payload.get("messages")
        if (
            isinstance(messages, list)
            and messages
            and isinstance(messages[0], Mapping)
        ):
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
                    "事实标签必须只承载题面事实；任何必须、禁止、建议、否决、"
                    "优先或行动要求必须另起结论或推断标签，不得与事实同句。"
                    + (("\n" + numeric_policy) if numeric_policy else "")
                    + delivery_discipline
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
        assert_request_has_no_tools(
            payload, context=f"constitutional node {node.node_id} request"
        )
        return payload


class ConstitutionalExecutionEngine(ExecutionEngine):
    """Make semantic, contract, evidence, and company validity success conditions."""

    def _normalize_attempt(
        self,
        node: SelectedNode,
        original_task: str,
        attempt: RuntimeAttempt,
        constraints: TaskConstraints,
    ) -> bool:
        if not attempt.answer:
            return False
        original_answer = attempt.answer
        normalized, audit = normalize_answer(
            original_task,
            original_answer,
            node.output_contract,
            constraints,
        )
        if not audit.get("applied"):
            return False

        attempt.raw_answer = original_answer
        attempt.answer = normalized
        attempt.answer_transformations.append(audit)

        failure_category = ""
        if isinstance(attempt.failure, Mapping):
            failure_category = str(attempt.failure.get("category") or "")
        repairable = attempt.status == "passed" or failure_category == (
            FailureCategory.QUALITY_GATE_FAILED.value
        )
        if not repairable:
            return False

        quality_passed, quality_score, quality_reasons = (
            self.quality_policy.evaluate(node, {}, normalized)
        )
        contract_violations = delivery_contract.validate_answer_contract(
            normalized,
            node.output_contract,
            node.parameter_profile,
        )
        evidence_violations = validate_answer_evidence(
            original_task,
            normalized,
            constraints,
        )
        violations = list(
            dict.fromkeys(
                [*quality_reasons, *contract_violations, *evidence_violations]
            )
        )
        if not quality_passed or violations:
            attempt.gate_reasons = violations
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
                    actual_cost_usd=self._actual_cost({"usage": attempt.usage}),
                    message=";".join(violations),
                ).to_dict()
            return False

        attempt.status = "passed"
        attempt.quality_score = quality_score
        attempt.gate_reasons = []
        attempt.failure = None
        return True

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
        if attempt is None or not attempt.answer:
            return attempt

        constraints = compile_task_constraints(original_task)
        if self._normalize_attempt(node, original_task, attempt, constraints):
            return attempt

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

        attempt.gate_reasons = list(
            dict.fromkeys([*attempt.gate_reasons, *violations])
        )
        if attempt.status != "passed":
            return attempt

        attempt.status = "quality_gate_failed"
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
    def _resolved_node_row(node: Mapping[str, Any]) -> tuple[dict[str, str], bool]:
        model = str(node.get("resolved_model") or node.get("selected_model") or "")
        status = str(node.get("status") or "")
        row = {
            "node_id": str(node.get("node_id") or ""),
            "model": model,
            "company": canonical_model_company(model),
            "status": status,
        }
        return row, status.startswith("success") and bool(model)

    @staticmethod
    def _attempt_model_rows(node: Mapping[str, Any]) -> list[dict[str, str]]:
        node_id = str(node.get("node_id") or "")
        attempts = node.get("attempts", [])
        if not isinstance(attempts, list):
            return []
        rows: list[dict[str, str]] = []
        for attempt in attempts:
            if not isinstance(attempt, Mapping):
                continue
            model = str(attempt.get("response_model") or attempt.get("model") or "")
            if not model:
                continue
            rows.append(
                {
                    "node_id": node_id,
                    "attempt_kind": str(attempt.get("attempt_kind") or ""),
                    "model": model,
                    "company": canonical_model_company(model),
                    "status": str(attempt.get("status") or ""),
                }
            )
        return rows

    @classmethod
    def _collect_company_rows(
        cls,
        result: Mapping[str, Any],
    ) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
        strict_successful: list[dict[str, str]] = []
        degraded: list[dict[str, str]] = []
        resolved_nodes: list[dict[str, str]] = []
        called: list[dict[str, str]] = []
        strict_statuses = {"success", "success_retried", "success_recovered"}
        for node in result.get("node_results", []):
            if not isinstance(node, Mapping):
                continue
            row, resolved = cls._resolved_node_row(node)
            if resolved:
                resolved_nodes.append(row)
            if row["status"] in strict_statuses and row["model"]:
                strict_successful.append(row)
            elif row["status"].startswith("success_degraded") and row["model"]:
                degraded.append(row)
            attempt_rows = cls._attempt_model_rows(node)
            called.extend(attempt_rows)
            if resolved and not attempt_rows:
                called.append(
                    {
                        **row,
                        "attempt_kind": "resolved-model-evidence-fallback",
                    }
                )
        return strict_successful, degraded, resolved_nodes, called

    @staticmethod
    def _company_conflicts(
        called: Sequence[Mapping[str, str]],
    ) -> tuple[dict[str, list[str]], list[Mapping[str, str]]]:
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
        return duplicates, unresolved

    @classmethod
    def _actual_company_audit(
        cls,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        strict, degraded, resolved, called = cls._collect_company_rows(result)
        duplicates, unresolved = cls._company_conflicts(called)
        return {
            "status": "FAIL" if duplicates or unresolved else "PASS",
            "policy": "recompute-from-all-actual-called-models",
            "successful_node_models": strict,
            "strict_successful_node_models": strict,
            "degraded_node_models": degraded,
            "resolved_node_models": resolved,
            "all_called_models": called,
            "duplicate_called_companies_across_nodes": duplicates,
            "duplicate_successful_companies": duplicates,
            "unresolved_called_companies": unresolved,
            "same_node_retry_is_not_a_second_expert": True,
            "failed_calls_are_included": True,
            "degraded_nodes_are_not_labeled_strict_success": True,
            "resolved_model_fallback_used_only_when_attempt_evidence_missing": True,
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

    @staticmethod
    def _execution_task(args: Sequence[Any], kwargs: Mapping[str, Any]) -> str:
        return str(kwargs.get("original_task") or (args[2] if len(args) > 2 else "") or "")

    @classmethod
    def _prepare_execution_root(
        cls,
        args: Sequence[Any],
        kwargs: dict[str, Any],
    ) -> tuple[str, TaskConstraints, Path | None]:
        original_task = cls._execution_task(args, kwargs)
        constraints = compile_task_constraints(original_task)
        if "limits" in kwargs:
            kwargs["limits"] = cls._strict_limits(kwargs.get("limits"), constraints)
        output_dir = kwargs.get("output_dir")
        root = Path(output_dir) if output_dir is not None else None
        if root is not None:
            root.mkdir(parents=True, exist_ok=True)
            cls._write_json(root / "task-constraints.json", constraints.to_dict())
        return original_task, constraints, root

    @staticmethod
    def _read_json_or_default(path: Path, default: Any) -> Any:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return default
        return value

    @staticmethod
    def _evidence_audit(
        original_task: str,
        answer: str,
        constraints: TaskConstraints,
        *,
        after_failure: bool = False,
    ) -> dict[str, Any]:
        violations = validate_answer_evidence(original_task, answer, constraints)
        payload = {
            "schema_version": "v5-evidence-integrity-1",
            "status": "FAIL" if violations else "PASS",
            "constraints": constraints.to_dict(),
            "violations": violations,
            "fact_truth_not_inferred_from_structure": True,
            "upstream_model_claims_are_not_promoted_to_user_facts": True,
        }
        if after_failure:
            payload["written_after_execution_failure"] = True
        return payload

    @classmethod
    def _write_failure_evidence(
        cls,
        root: Path | None,
        original_task: str,
        constraints: TaskConstraints,
    ) -> None:
        if root is None:
            return
        node_results = cls._read_json_or_default(root / "v5-node-results.json", [])
        if not isinstance(node_results, list):
            node_results = []
        summary = cls._read_json_or_default(root / "v5-execution-summary.json", {})
        if not isinstance(summary, Mapping):
            summary = {}
        cls._write_json(
            root / "actual-model-company-audit.json",
            cls._actual_company_audit({"node_results": node_results}),
        )
        cls._write_json(
            root / "evidence-integrity.json",
            cls._evidence_audit(
                original_task,
                str(summary.get("final_answer") or ""),
                constraints,
                after_failure=True,
            ),
        )

    @staticmethod
    def _constitutional_failure_reason(
        result: Mapping[str, Any],
        company_audit: Mapping[str, Any],
        evidence_audit: Mapping[str, Any],
        constraints: TaskConstraints,
    ) -> str | None:
        if company_audit["status"] != "PASS":
            return "actual-model-company-uniqueness-violation"
        if evidence_audit["status"] != "PASS":
            return "unsupported-evidence-or-quantity"
        if result.get("completion_mode") == "degraded" and not constraints.allow_degraded_success:
            return "degradation-not-authorized-by-user"
        return None

    @classmethod
    def _write_constitutional_audits(
        cls,
        root: Path | None,
        company_audit: Mapping[str, Any],
        evidence_audit: Mapping[str, Any],
    ) -> None:
        if root is None:
            return
        cls._write_json(root / "actual-model-company-audit.json", company_audit)
        cls._write_json(root / "evidence-integrity.json", evidence_audit)

    @classmethod
    def _write_execution_summary(
        cls,
        root: Path | None,
        result: Mapping[str, Any],
    ) -> None:
        if root is None:
            return
        cls._write_json(
            root / "v5-execution-summary.json",
            {key: value for key, value in result.items() if key != "node_results"},
        )

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
        raise RuntimeError(reason)

    def execute_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        original_task, constraints, root = self._prepare_execution_root(args, kwargs)
        try:
            result = super().execute_graph(*args, **kwargs)
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
        self._write_constitutional_audits(root, company_audit, evidence_audit)
        reason = self._constitutional_failure_reason(
            result, company_audit, evidence_audit, constraints
        )
        if reason:
            self._fail_constitutional_result(result, root, reason)
        self._write_execution_summary(root, result)
        return result
