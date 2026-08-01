#!/usr/bin/env python3
"""Actions-only transformer for live-ledger recovery admission.

This file exists only on the bootstrap branch. The qualified code branch is
created by GitHub Actions from the immutable main commit and does not contain
this transformer or its workflow.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANNER = ROOT / "open-model-market" / "v5_cross_endpoint_planner.py"
TEST = ROOT / "tests" / "test_v5_critical_delivery_reliability.py"
P0 = ROOT / "tools" / "run_v5_p0_regressions.py"
VALIDATE = ROOT / ".github" / "workflows" / "validate.yml"


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(
            f"expected one replacement marker in {path}, got {source.count(old)}"
        )
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def replace_test_method(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} in {path}, got {len(matches)}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    path.write_text("".join(lines), encoding="utf-8")


def patch_planner() -> None:
    replace_once(
        PLANNER,
        '''        eligible_by_node: dict[str, list[dict[str, Any]]] = {}
        budget_excluded_by_node: dict[str, int] = {}
''',
        '''        eligible_by_node: dict[str, list[dict[str, Any]]] = {}
        budget_excluded_by_node: dict[str, int] = {}
        estimated_above_planning_budget_by_node: dict[str, int] = {}
''',
    )
    replace_once(
        PLANNER,
        '''            before_budget = len(alternatives)
            if remaining_recovery_budget is not None:
                alternatives = [
                    row
                    for row in alternatives
                    if max(
                        0.0,
                        float(row.get("estimated_cost", 0.0) or 0.0),
                    )
                    <= remaining_recovery_budget + 1e-12
                ]
            budget_excluded_by_node[node_id] = before_budget - len(alternatives)
''',
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
    )
    replace_once(
        PLANNER,
        '''                payload["recovery_delivery_utility"] = round(
                    self._delivery_utility(row),
                    9,
                )
                unique_by_company.append(payload)
''',
        '''                payload["recovery_delivery_utility"] = round(
                    self._delivery_utility(row),
                    9,
                )
                estimated_cost = max(
                    0.0,
                    float(row.get("estimated_cost", 0.0) or 0.0),
                )
                payload["planning_budget_advisory_only"] = True
                payload[
                    "estimated_cost_above_planning_remaining_budget"
                ] = bool(
                    remaining_recovery_budget is not None
                    and estimated_cost
                    > remaining_recovery_budget + 1e-12
                )
                unique_by_company.append(payload)
''',
    )
    replace_once(
        PLANNER,
        '''            "budget_excluded_by_node": budget_excluded_by_node,
            "cross_task_history_used": False,
''',
        '''            "budget_excluded_by_node": budget_excluded_by_node,
            "estimated_above_planning_budget_by_node": (
                estimated_above_planning_budget_by_node
            ),
            "planning_estimated_budget_advisory_only": True,
            "runtime_budget_controller_authoritative": True,
            "recovery_candidates_retained_for_live_ledger_admission": True,
            "cross_task_history_used": False,
''',
    )


def patch_tests() -> None:
    replace_once(
        TEST,
        '''from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402
''',
        '''from execution_graph import ExecutionGraph  # noqa: E402
from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import BudgetController, RuntimeConfig  # noqa: E402
''',
    )
    replace_test_method(
        TEST,
        "test_critical_recovery_uses_best_budget_feasible_company_row",
        '''    def test_critical_recovery_retains_rows_for_live_budget_admission(self) -> None:
        policy = CrossEndpointPlannerPolicy(self.config())
        final = "work-final"
        selected = candidate(
            "node-selected",
            "openai/selected",
            "coreweave/fp4",
            work_id=final,
            functions=("synthesis",),
            cost=0.004,
            quality=0.60,
            failure=0.03,
        )
        cheap_alibaba = candidate(
            "node-qwen-small",
            "qwen/qwen-small",
            "venice/fp8",
            work_id=final,
            functions=("synthesis",),
            cost=0.0016,
            quality=0.56,
            failure=0.025,
        )
        reliable_alibaba = candidate(
            "node-qwen-plus",
            "qwen/qwen-plus",
            "alibaba/fp8",
            work_id=final,
            functions=("synthesis",),
            cost=0.018,
            quality=0.75,
            failure=0.02,
        )
        reliable_glm = candidate(
            "node-glm",
            "z-ai/glm",
            "decart/fp4",
            work_id=final,
            functions=("synthesis",),
            cost=0.017,
            quality=0.78,
            failure=0.019,
        )
        above_planning_advisory = candidate(
            "node-over-budget",
            "anthropic/opus",
            "anthropic",
            work_id=final,
            functions=("synthesis",),
            cost=0.04,
            quality=0.90,
            failure=0.01,
        )
        optimization = {
            "selected_initial_cost_usd": 0.005,
            "execution_graph": {
                "nodes": [
                    {
                        **selected,
                        "node_id": selected["candidate_id"],
                    }
                ],
                "final_nodes": [selected["candidate_id"]],
                "metadata": {
                    "interpretation_id": "interpretation-critical"
                },
            },
        }
        bundle = {
            "candidates": [
                selected,
                cheap_alibaba,
                reliable_alibaba,
                reliable_glm,
                above_planning_advisory,
            ]
        }

        result = policy.rebalance_recovery_pool(optimization, bundle)
        metadata = result["execution_graph"]["metadata"]
        rows = metadata["recovery_pool"]["node-selected"]
        models = [str(row["model"]) for row in rows]
        self.assertEqual("anthropic/opus", models[0])
        self.assertIn("z-ai/glm", models)
        self.assertIn("qwen/qwen-plus", models)
        self.assertNotIn("qwen/qwen-small", models)
        policy_evidence = metadata["recovery_pool_policy"]
        self.assertTrue(
            policy_evidence["planning_estimated_budget_advisory_only"]
        )
        self.assertTrue(
            policy_evidence["runtime_budget_controller_authoritative"]
        )
        self.assertTrue(
            policy_evidence[
                "recovery_candidates_retained_for_live_ledger_admission"
            ]
        )
        self.assertEqual(
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
        )''',
    )
    marker = '''    def test_global_recovery_company_allocation_prioritizes_final_node(self) -> None:
'''
    source = TEST.read_text(encoding="utf-8")
    if marker not in source:
        raise RuntimeError("global allocation test marker missing")
    new_method = '''    def test_v3_regression_live_ledger_can_admit_retained_recovery(self) -> None:
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=0.008,
            quality_tier="value",
            tools_allowed=False,
            provider_lock_required=True,
        )
        policy = CrossEndpointPlannerPolicy(config)
        selected_rows = [
            candidate(
                "node-qwen",
                "qwen/qwen3.5-9b",
                "siliconflow/fp8",
                work_id="work-qwen",
                functions=("analysis",),
                cost=0.0017,
                quality=0.70,
                failure=0.03,
            ),
            candidate(
                "node-deepseek",
                "deepseek/deepseek-v4-flash",
                "deepinfra/fp4",
                work_id="work-final",
                functions=("synthesis",),
                cost=0.0022,
                quality=0.76,
                failure=0.02,
            ),
            candidate(
                "node-openai",
                "openai/gpt-oss-120b",
                "groq/fp8",
                work_id="work-openai",
                functions=("analysis",),
                cost=0.001897,
                quality=0.74,
                failure=0.02,
            ),
        ]
        recovery = candidate(
            "node-mistral-recovery",
            "mistralai/mistral-small",
            "mistral",
            work_id="work-qwen",
            functions=("analysis",),
            cost=0.003,
            quality=0.72,
            failure=0.02,
        )
        optimization = {
            "selected_initial_cost_usd": 0.005797,
            "execution_graph": {
                "nodes": [
                    {**row, "node_id": row["candidate_id"]}
                    for row in selected_rows
                ],
                "final_nodes": ["node-deepseek"],
                "metadata": {
                    "interpretation_id": "interpretation-critical"
                },
            },
        }
        bundle = {"candidates": [*selected_rows, recovery]}

        result = policy.rebalance_recovery_pool(optimization, bundle)
        pool = result["execution_graph"]["metadata"]["recovery_pool"]
        self.assertEqual(
            "mistralai/mistral-small",
            pool["node-qwen"][0]["model"],
        )
        evidence = result["recovery_pool_policy"]
        self.assertEqual(
            1,
            evidence["estimated_above_planning_budget_by_node"][
                "node-qwen"
            ],
        )
        self.assertEqual(
            0,
            evidence["budget_excluded_by_node"]["node-qwen"],
        )

        empty_graph = ExecutionGraph(
            nodes=(),
            edges=(),
            execution_stages=(),
            entry_nodes=(),
            final_nodes=(),
            required_work=(),
            estimated_quality=0.0,
            quality_floor=0.0,
            estimated_total_cost=0.0,
            metadata={},
        )
        budget = BudgetController(config, empty_graph)
        for estimated, actual, node_id in (
            (0.0017, 0.0, "node-qwen"),
            (0.0022, 0.00047194, "node-deepseek"),
            (0.001897, 0.0002683, "node-openai"),
        ):
            allowed, reason = budget.reserve("initial", estimated, node_id)
            self.assertTrue(allowed, reason)
            self.assertFalse(budget.reconcile(actual))
        allowed, reason = budget.reserve(
            "replacement",
            recovery["estimated_cost"],
            "node-qwen",
        )
        self.assertTrue(allowed, reason)
        snapshot = budget.snapshot()
        self.assertEqual(4, snapshot["calls_reserved"])
        self.assertEqual(1, snapshot["recovery_calls_reserved"])
        self.assertEqual(0.00074024, snapshot["actual_cost_usd"])

'''
    TEST.write_text(source.replace(marker, new_method + marker, 1), encoding="utf-8")


def patch_p0() -> None:
    replace_once(
        P0,
        '''    (TESTS / "test_v5_independent_artifact_revalidation.py", "IndependentArtifactRevalidationTests", 3),
)''',
        '''    (TESTS / "test_v5_independent_artifact_revalidation.py", "IndependentArtifactRevalidationTests", 3),
    (TESTS / "test_v5_critical_delivery_reliability.py", "V5CriticalDeliveryReliabilityTests", 4),
)''',
    )
    source = VALIDATE.read_text(encoding="utf-8")
    source = source.replace(
        '''          grep -q "REGISTERED IndependentArtifactRevalidationTests: 3" validation-logs/p0-regressions.log
          grep -q "REGISTERED TOTAL: 34" validation-logs/p0-regressions.log
          grep -q "P0 REGRESSION RESULT: run=34, passed=34, failures=0, errors=0, skipped=0" \\
''',
        '''          grep -q "REGISTERED IndependentArtifactRevalidationTests: 3" validation-logs/p0-regressions.log
          grep -q "REGISTERED V5CriticalDeliveryReliabilityTests: 4" validation-logs/p0-regressions.log
          grep -q "REGISTERED TOTAL: 38" validation-logs/p0-regressions.log
          grep -q "P0 REGRESSION RESULT: run=38, passed=38, failures=0, errors=0, skipped=0" \\
''',
    )
    if "REGISTERED TOTAL: 34" in source:
        raise RuntimeError("stale 34-test P0 contract remains in validate.yml")
    VALIDATE.write_text(source, encoding="utf-8")


def normalize() -> None:
    for path in (PLANNER, TEST, P0, VALIDATE):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.rstrip() + "\n", encoding="utf-8")


def main() -> int:
    patch_planner()
    patch_tests()
    patch_p0()
    normalize()
    evidence = {
        "schema_version": "v5-live-recovery-budget-fix-1",
        "status": "PENDING_VALIDATION",
        "planning_estimated_budget_advisory_only": True,
        "runtime_budget_controller_authoritative": True,
        "recovery_candidates_retained_for_live_ledger_admission": True,
        "v3_regression_run_id": "30710187845",
        "v3_actual_initial_cost_usd": 0.00074024,
        "v3_planning_estimated_initial_cost_usd": 0.005797,
        "v3_cost_cap_usd": 0.008,
        "p0_expected_total": 38,
    }
    (ROOT / "open-model-market" / "live-recovery-budget-fix-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
