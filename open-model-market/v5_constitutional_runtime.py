"""Constitutional execution policy layered on the explicit native V5 runtime."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import v5_task_delivery_contract as delivery_contract
from execution_graph import SelectedNode
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

_CLOSED_WORLD_RE = re.compile(
    r"(?:仅限|仅依据|只能依据|不得编造|禁止编造|不联网|不调用工具|"
    r"closed[- ]book|self[- ]contained|only\s+the\s+provided|no\s+external)",
    re.IGNORECASE,
)
_QUANTITY_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*"
    r"(秒|分钟|小时|天|周|月|年|米|公里|千米|公斤|克|人|次|%|％|"
    r"seconds?|minutes?|hours?|days?|weeks?|months?|years?|meters?|"
    r"kilometers?|kg|people|times?)\b?",
    re.IGNORECASE,
)


def _normalized_quantities(text: str) -> set[tuple[str, str]]:
    aliases = {
        "％": "%",
        "秒": "second",
        "seconds": "second",
        "second": "second",
        "分钟": "minute",
        "minutes": "minute",
        "minute": "minute",
        "小时": "hour",
        "hours": "hour",
        "hour": "hour",
        "天": "day",
        "days": "day",
        "day": "day",
        "周": "week",
        "weeks": "week",
        "week": "week",
        "月": "month",
        "months": "month",
        "month": "month",
        "年": "year",
        "years": "year",
        "year": "year",
        "米": "meter",
        "meters": "meter",
        "meter": "meter",
        "公里": "kilometer",
        "千米": "kilometer",
        "kilometers": "kilometer",
        "kilometer": "kilometer",
        "公斤": "kg",
        "克": "gram",
        "人": "people",
        "people": "people",
        "次": "times",
        "times": "times",
    }
    values: set[tuple[str, str]] = set()
    for number, unit in _QUANTITY_RE.findall(str(text or "")):
        normalized_number = str(float(number)).rstrip("0").rstrip(".")
        normalized_unit = aliases.get(unit.casefold(), unit.casefold())
        values.add((normalized_number, normalized_unit))
    return values


def validate_scope_boundaries(task: str, answer: str) -> list[str]:
    """Reject new precise quantities when the user imposes a closed world."""
    if not _CLOSED_WORLD_RE.search(str(task or "")):
        return []
    allowed = _normalized_quantities(task)
    introduced = sorted(_normalized_quantities(answer) - allowed)
    if not introduced:
        return []
    rendered = ",".join(
        f"{number}:{unit}" for number, unit in introduced[:12]
    )
    return ["closed-world-unsupported-quantity:" + rendered]


class ConstitutionalExecutionEngine(ExecutionEngine):
    """Make contract and scope validity part of each attempt's success state."""

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

        contract_violations = delivery_contract.validate_answer_contract(
            attempt.answer,
            node.output_contract,
            node.parameter_profile,
        )
        scope_violations = validate_scope_boundaries(
            original_task,
            attempt.answer,
        )
        violations = list(
            dict.fromkeys([*contract_violations, *scope_violations])
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

        by_company: dict[str, list[str]] = {}
        for row in successful:
            by_company.setdefault(row["company"], []).append(row["node_id"])
        duplicates = {
            company: sorted(set(nodes))
            for company, nodes in by_company.items()
            if len(set(nodes)) > 1
        }
        return {
            "status": "FAIL" if duplicates else "PASS",
            "policy": "recompute-from-actual-successful-node-models",
            "successful_node_models": successful,
            "all_called_models": called,
            "duplicate_successful_companies": duplicates,
            "same-node-retry_is_not_a_second_expert": True,
            "cross_task_history_used": False,
        }

    def execute_graph(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().execute_graph(*args, **kwargs)
        audit = self._actual_company_audit(result)
        result["actual_model_company_audit"] = audit

        output_dir = kwargs.get("output_dir")
        if output_dir is not None:
            root = Path(output_dir)
            self._write_json(root / "actual-model-company-audit.json", audit)
            self._write_json(
                root / "v5-execution-summary.json",
                {
                    key: value
                    for key, value in result.items()
                    if key != "node_results"
                },
            )

        if audit["status"] != "PASS":
            result["status"] = "failed"
            result["completion_mode"] = "none"
            result["quality_status"] = "failed"
            result["final_answer"] = None
            result["stop_reason"] = (
                "actual-model-company-uniqueness-violation"
            )
            if output_dir is not None:
                root = Path(output_dir)
                self._write_json(
                    root / "v5-execution-summary.json",
                    {
                        key: value
                        for key, value in result.items()
                        if key != "node_results"
                    },
                )
                (root / "v5-final-report.md").write_text(
                    "# V5 execution failed\n\n"
                    "Actual successful model companies were not unique.\n",
                    encoding="utf-8",
                )
            raise RuntimeError(
                "actual successful model companies are not unique"
            )
        return result


def harden_runtime(runtime: ProductionRuntime) -> ProductionRuntime:
    """Replace the owned engine without patching module globals."""
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
