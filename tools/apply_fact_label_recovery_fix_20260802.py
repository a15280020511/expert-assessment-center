#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one replacement in {path}: {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_normalization() -> None:
    path = ROOT / "open-model-market" / "v5_deterministic_answer_normalization.py"
    replace_once(
        path,
        '''_H2_RE = re.compile(r"^\\s{0,3}##\\s+(.+?)\\s*#*\\s*$")
''',
        '''_H2_RE = re.compile(r"^\\s{0,3}##\\s+(.+?)\\s*#*\\s*$")
_FACT_LABEL_LINE_RE = re.compile(
    r"^(?P<prefix>\\s*(?:[-*+]\\s*)?)"
    r"(?P<label>(?:事实|已知事实|fact)(?:[（(][^）)]*[）)])?\\s*[:：])"
    r"\\s*(?P<body>.+?)\\s*$",
    re.IGNORECASE,
)
_NORMATIVE_TAIL_RE = re.compile(
    r"^(?P<fact>.+?)(?P<separator>[，,；;。]\\s*)"
    r"(?P<norm>(?:必须|务必|应当|应该|禁止|不得|不可|不能|"
    r"需(?:要)?|建议|优先|否决|拒绝|应|可(?:以)?|"
    r"must\\b|should\\b|must\\s+not\\b|do\\s+not\\b|"
    r"recommend\\b|reject\\b|deny\\b).+)$",
    re.IGNORECASE,
)
''',
    )
    replace_once(
        path,
        '''def normalize_answer(
''',
        '''def _split_mixed_fact_labels(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """Separate factual propositions from normative tails without rewriting text."""
    rows: list[str] = []
    evidence: list[dict[str, Any]] = []
    for line_number, line in enumerate(str(answer or "").splitlines(), start=1):
        match = _FACT_LABEL_LINE_RE.match(line)
        if not match:
            rows.append(line)
            continue
        body = match.group("body").strip()
        tail = _NORMATIVE_TAIL_RE.match(body)
        if not tail:
            rows.append(line)
            continue
        fact = tail.group("fact").strip().rstrip("，,；;。 ")
        normative = tail.group("norm").strip()
        if not fact or not normative:
            rows.append(line)
            continue
        prefix = match.group("prefix")
        label = match.group("label")
        fact_line = f"{prefix}{label}{fact}。"
        conclusion_line = f"{prefix}结论：{normative}"
        rows.extend((fact_line, conclusion_line))
        evidence.append(
            {
                "line_number": line_number,
                "original": line,
                "fact_line": fact_line,
                "conclusion_line": conclusion_line,
            }
        )
    suffix = "\\n" if str(answer or "").endswith("\\n") else ""
    return "\\n".join(rows) + suffix, evidence


def normalize_answer(
''',
    )
    replace_once(
        path,
        '''    """Remove unsupported numeric lines and canonically reorder complete H2 blocks.

    This function never invents text. It may delete the smallest physical lines
    containing unsupported exact quantities and may move already complete,
    uniquely named H2 sections into the compiled contract order.
    """
''',
        '''    """Deterministically normalize label purity, quantities, and H2 order.

    This function never invents substantive text. It may insert an audited
    structural ``结论：`` label when a fact-labelled line already contains a
    normative tail, delete the smallest physical lines containing unsupported
    exact quantities, and move complete uniquely named H2 sections into the
    compiled contract order.
    """
''',
    )
    replace_once(
        path,
        '''        "schema_version": "v5-deterministic-answer-normalization-1",
        "policy": "delete-unsupported-quantity-lines-and-reorder-complete-h2-only",
''',
        '''        "schema_version": "v5-deterministic-answer-normalization-2",
        "policy": (
            "split-mixed-fact-normative-labels-delete-unsupported-quantity-lines-"
            "and-reorder-complete-h2-only"
        ),
''',
    )
    replace_once(
        path,
        '''        "unsupported_quantities_removed": [],
        "h2_reordered": False,
''',
        '''        "unsupported_quantities_removed": [],
        "mixed_fact_labels_split": [],
        "structural_labels_inserted": 0,
        "substantive_text_invented": False,
        "h2_reordered": False,
''',
    )
    replace_once(
        path,
        '''    working = original
    if not constraints.unsupported_precise_quantities_allowed:
''',
        '''    working, mixed_fact_labels = _split_mixed_fact_labels(original)
    audit["mixed_fact_labels_split"] = mixed_fact_labels
    audit["structural_labels_inserted"] = len(mixed_fact_labels)
    if not constraints.unsupported_precise_quantities_allowed:
''',
    )


def patch_prompt() -> None:
    path = ROOT / "open-model-market" / "v5_constitutional_runtime.py"
    replace_once(
        path,
        '''                    + "\\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                    "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
''',
        '''                    + "\\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                    "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
                    "事实标签必须只承载题面事实；任何必须、禁止、建议、否决、"
                    "优先或行动要求必须另起结论或推断标签，不得与事实同句。"
''',
    )


def patch_recovery_planner() -> None:
    path = ROOT / "open-model-market" / "v5_cross_endpoint_planner.py"
    replace_once(
        path,
        '''        budget_excluded_by_node: dict[str, int] = {}
        estimated_above_planning_budget_by_node: dict[str, int] = {}
''',
        '''        budget_excluded_by_node: dict[str, int] = {}
        absolute_cost_cap_excluded_by_node: dict[str, int] = {}
        estimated_above_planning_budget_by_node: dict[str, int] = {}
''',
    )
    replace_once(
        path,
        '''            estimated_above_planning_budget_by_node[node_id] = (
                0
                if remaining_recovery_budget is None
                else sum(
                    1
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    > remaining_recovery_budget + 1e-12
                )
            )
            # Planning-time estimated remaining budget is advisory only. Initial
            # calls are reconciled against provider-billed actual cost, so
            # permanently deleting candidates here can strand a failed node even
            # when the live ledger has ample budget. Runtime BudgetController is
            # the authoritative admission gate for every retry/replacement.
            budget_excluded_by_node[node_id] = 0
''',
        '''            absolute_cost_cap_excluded_by_node[node_id] = (
                0
                if cost_cap <= 0.0
                else sum(
                    1
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    > cost_cap + 1e-12
                )
            )
            if cost_cap > 0.0:
                alternatives = [
                    row
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    <= cost_cap + 1e-12
                ]
            estimated_above_planning_budget_by_node[node_id] = (
                0
                if remaining_recovery_budget is None
                else sum(
                    1
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    > remaining_recovery_budget + 1e-12
                )
            )
            # The absolute anomaly guard is a hard admission boundary, so a
            # candidate whose own estimate exceeds it can never execute. The
            # estimated remaining budget is different: initial calls reconcile
            # against provider-billed actual cost, so candidates within the
            # absolute cap remain available for the live ledger to admit.
            budget_excluded_by_node[node_id] = 0
''',
    )
    replace_once(
        path,
        '''                payload["planning_budget_advisory_only"] = True
''',
        '''                payload["planning_budget_advisory_only"] = True
                payload["absolute_cost_cap_feasible"] = True
''',
    )
    replace_once(
        path,
        '''        metadata = dict(graph.get("metadata") or {})
''',
        '''        total_recovery_options = sum(
            len(rows) for rows in recovery_pool.values()
        )
        if int(self.config.recovery_call_limit) > 0 and total_recovery_options <= 0:
            raise V5PlanningError(
                "Recovery reserve is not executable under the absolute cost "
                "anomaly guard."
            )

        metadata = dict(graph.get("metadata") or {})
''',
    )
    replace_once(
        path,
        '''            "budget_excluded_by_node": budget_excluded_by_node,
            "estimated_above_planning_budget_by_node": (
''',
        '''            "budget_excluded_by_node": budget_excluded_by_node,
            "absolute_cost_cap_excluded_by_node": (
                absolute_cost_cap_excluded_by_node
            ),
            "absolute_cost_cap_enforced_at_planning": True,
            "total_executable_recovery_options": total_recovery_options,
            "estimated_above_planning_budget_by_node": (
''',
    )


def patch_existing_recovery_test() -> None:
    path = ROOT / "tests" / "test_v5_critical_delivery_reliability.py"
    replace_once(
        path,
        '''        self.assertEqual("anthropic/opus", models[0])
        self.assertIn("z-ai/glm", models)
        self.assertIn("qwen/qwen-plus", models)
        self.assertNotIn("qwen/qwen-small", models)
''',
        '''        self.assertEqual("z-ai/glm", models[0])
        self.assertIn("qwen/qwen-plus", models)
        self.assertNotIn("qwen/qwen-small", models)
        self.assertNotIn("anthropic/opus", models)
''',
    )
    replace_once(
        path,
        '''        self.assertEqual(
            0,
            policy_evidence["budget_excluded_by_node"]["node-selected"],
        )
        self.assertEqual(
            1,
            policy_evidence[
                "estimated_above_planning_budget_by_node"
            ]["node-selected"],
        )
        retained = next(
            row for row in rows if row["model"] == "anthropic/opus"
        )
        self.assertTrue(
            retained["estimated_cost_above_planning_remaining_budget"]
        )
''',
        '''        self.assertEqual(
            0,
            policy_evidence["budget_excluded_by_node"]["node-selected"],
        )
        self.assertEqual(
            1,
            policy_evidence["absolute_cost_cap_excluded_by_node"][
                "node-selected"
            ],
        )
        self.assertEqual(
            0,
            policy_evidence[
                "estimated_above_planning_budget_by_node"
            ]["node-selected"],
        )
        self.assertTrue(
            policy_evidence["absolute_cost_cap_enforced_at_planning"]
        )
        self.assertEqual(2, policy_evidence["total_executable_recovery_options"])
''',
    )


def write_extended_tests() -> None:
    normalization = ROOT / "tests" / "test_v5_fact_label_purity_normalization.py"
    normalization.write_text(
        '''from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_deterministic_answer_normalization import normalize_answer  # noqa: E402
from v5_runtime import (  # noqa: E402
    ExecutionFailure,
    FailureCategory,
    RuntimeAttempt,
    RuntimeConfig,
)
from v5_task_constraints import compile_task_constraints, validate_answer_evidence  # noqa: E402

TASK = (
    "仅依据题面：西门存在不明液体且电气风险未知；"
    "东门地面干燥但门锁卡滞；来访者身份无法核验。"
    "禁止接触不明液体、强行开门或放行无法核验身份的来访者。"
)
ANSWER = (
    "## conclusions\\n"
    "- 事实：西门存在不明液体且电气风险未知，必须否决该路线。\\n"
    "- 事实：东门地面干燥但门锁卡滞，禁止强行开门，需采取非破坏性替代方案。\\n"
    "- 事实：来访者身份无法核验，禁止放行。\\n"
)
CONTRACT = {
    "required_fields": ["conclusions"],
    "exact_markdown_headings": ["conclusions"],
    "machine_readable_required": False,
}


def node() -> SelectedNode:
    return SelectedNode(
        node_id="node-fact-purity",
        assigned_work=("work-fact-purity",),
        professional_capabilities={"analysis": 0.8},
        functions=("analysis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning"]},
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        output_contract=dict(CONTRACT),
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.001,
        failure_probability=0.02,
        request_config={"provider": {"order": ["provider-a"], "only": ["provider-a"], "allow_fallbacks": False, "require_parameters": True}},
    )


class V5FactLabelPurityNormalizationTests(unittest.TestCase):
    def test_splits_production_mixed_fact_and_normative_lines(self) -> None:
        value, audit = normalize_answer(
            TASK,
            ANSWER,
            CONTRACT,
            compile_task_constraints(TASK),
        )
        self.assertEqual(3, audit["structural_labels_inserted"])
        self.assertEqual(3, len(audit["mixed_fact_labels_split"]))
        self.assertIn("事实：西门存在不明液体且电气风险未知。", value)
        self.assertIn("结论：必须否决该路线。", value)
        self.assertIn("事实：东门地面干燥但门锁卡滞。", value)
        self.assertIn("结论：禁止强行开门，需采取非破坏性替代方案。", value)
        self.assertIn("事实：来访者身份无法核验。", value)
        self.assertIn("结论：禁止放行。", value)
        self.assertEqual([], validate_answer_evidence(TASK, value))
        self.assertFalse(audit["substantive_text_invented"])

    def test_pure_fact_line_is_unchanged(self) -> None:
        answer = "事实：来访者身份无法核验。\\n"
        value, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertEqual(answer, value)
        self.assertEqual(0, audit["structural_labels_inserted"])

    def test_engine_promotes_only_after_full_revalidation(self) -> None:
        engine = ConstitutionalExecutionEngine(
            RuntimeConfig(5, 1, 0.35, "value"),
            prompt_policy=SimpleNamespace(),
            retry_policy=SimpleNamespace(),
            recovery_policy=SimpleNamespace(),
            quality_policy=SimpleNamespace(evaluate=lambda *_: (True, 0.93, [])),
            output_policy=SimpleNamespace(schema_version="v5-node-result-1"),
        )
        failure = ExecutionFailure(
            category=FailureCategory.QUALITY_GATE_FAILED,
            retryable=False,
            model="openai/test-model",
            provider_endpoint="openai/test-model@provider-a",
            request_sent=True,
            response_received=True,
            message="unsupported-fact-label",
        ).to_dict()
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="node-fact-purity",
            model="openai/test-model",
            provider_endpoint="openai/test-model@provider-a",
            request={},
            status="quality_gate_failed",
            answer=ANSWER,
            quality_score=1.0,
            gate_reasons=["unsupported-fact-label"],
            latency_seconds=0.1,
            usage={},
            response_id="response-test",
            response_model="openai/test-model",
            response_provider="provider-a",
            failure=failure,
        )
        self.assertTrue(
            engine._normalize_attempt(
                node(), TASK, attempt, compile_task_constraints(TASK)
            )
        )
        self.assertEqual("passed", attempt.status)
        self.assertIsNone(attempt.failure)
        self.assertEqual([], validate_answer_evidence(TASK, attempt.answer or ""))


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )

    recovery = ROOT / "tests" / "test_v5_recovery_absolute_budget_feasibility.py"
    recovery.write_text(
        '''from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_planner import V5PlanningError  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402


def candidate(candidate_id: str, model: str, company: str, cost: float) -> dict:
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "interpretation-budget",
        "coverage_keys": ["work-a#0"],
        "assigned_work": ["work-a"],
        "copy_indices": [0],
        "professional_capabilities": {},
        "functions": ["analysis"],
        "prompt_profile": {},
        "reasoning_profile": {},
        "parameter_profile": {"model_company": company},
        "model_company": company,
        "model": model,
        "provider_endpoint": f"{model}@provider-{company}",
        "provider_slug": f"provider-{company}",
        "output_contract": {},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.1,
        "estimated_cost": cost,
        "failure_probability": 0.02,
        "request_config": {},
        "independence_groups": [],
    }


class V5RecoveryAbsoluteBudgetFeasibilityTests(unittest.TestCase):
    def test_planning_fails_when_every_recovery_candidate_exceeds_absolute_cap(self) -> None:
        selected = candidate("node-selected", "google/selected", "google", 0.05)
        openai = candidate("node-openai", "openai/recovery", "openai", 0.25986871)
        anthropic = candidate("node-anthropic", "anthropic/recovery", "anthropic", 0.27162259)
        optimization = {
            "selected_initial_cost_usd": 0.05,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "node-selected"}],
                "final_nodes": [],
                "metadata": {"interpretation_id": "interpretation-budget"},
            },
        }
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(2, 1, 0.25, "value")
        )
        with self.assertRaisesRegex(
            V5PlanningError,
            "Recovery reserve is not executable",
        ):
            policy.rebalance_recovery_pool(
                optimization,
                {"candidates": [selected, openai, anthropic]},
            )

    def test_candidate_within_absolute_cap_remains_available_for_live_ledger(self) -> None:
        selected = candidate("node-selected", "google/selected", "google", 0.12)
        recovery = candidate("node-openai", "openai/recovery", "openai", 0.25986871)
        optimization = {
            "selected_initial_cost_usd": 0.12,
            "execution_graph": {
                "nodes": [{**selected, "node_id": "node-selected"}],
                "final_nodes": [],
                "metadata": {"interpretation_id": "interpretation-budget"},
            },
        }
        policy = CrossEndpointPlannerPolicy(
            RuntimeConfig(2, 1, 0.35, "value")
        )
        result = policy.rebalance_recovery_pool(
            optimization,
            {"candidates": [selected, recovery]},
        )
        rows = result["execution_graph"]["metadata"]["recovery_pool"][
            "node-selected"
        ]
        self.assertEqual("openai/recovery", rows[0]["model"])
        evidence = result["recovery_pool_policy"]
        self.assertEqual(
            0,
            evidence["absolute_cost_cap_excluded_by_node"]["node-selected"],
        )
        self.assertEqual(1, evidence["total_executable_recovery_options"])
        self.assertTrue(rows[0]["estimated_cost_above_planning_remaining_budget"])


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_normalization()
    patch_prompt()
    patch_recovery_planner()
    patch_existing_recovery_test()
    write_extended_tests()


if __name__ == "__main__":
    main()
