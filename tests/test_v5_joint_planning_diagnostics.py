import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
from v5_execution_auditor_integrity import audit  # noqa: E402
from v5_planning_diagnostics import build_infeasibility_report  # noqa: E402


def candidate(
    candidate_id: str,
    interpretation_id: str,
    coverage_key: str,
    *,
    estimated_cost: float,
) -> dict:
    company = f"vendor-{candidate_id}"
    return {
        "candidate_id": candidate_id,
        "interpretation_id": interpretation_id,
        "coverage_keys": [coverage_key],
        "assigned_work": [coverage_key.split("#", 1)[0]],
        "copy_indices": [0],
        "professional_capabilities": {"analysis": 0.8},
        "functions": ["analysis"],
        "prompt_profile": {},
        "reasoning_profile": {"enabled": True},
        "parameter_profile": {},
        "model": f"{company}/model",
        "provider_endpoint": f"{company}/model@provider/default",
        "provider_slug": "provider/default",
        "output_contract": {"required_fields": ["conclusions"]},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.1,
        "estimated_cost": estimated_cost,
        "failure_probability": 0.0,
        "request_config": {},
        "independence_groups": [],
    }


def interpretation(interpretation_id: str, work_count: int) -> dict:
    return {
        "metrics": {"interpretation_score": 0.8},
        "work_ids": [f"{interpretation_id}-work-{index}" for index in range(work_count)],
        "copies_by_work": {
            f"{interpretation_id}-work-{index}": 1
            for index in range(work_count)
        },
        "atomic_edges": [],
    }


class TestV5JointPlanningDiagnostics(unittest.TestCase):
    def bundle(self) -> dict:
        five_id = "interpretation-five-cheap"
        three_id = "interpretation-three-expensive"
        rows = []
        for index in range(5):
            work = f"{five_id}-work-{index}"
            rows.append(
                candidate(
                    f"five-{index}",
                    five_id,
                    f"{work}#0",
                    estimated_cost=0.1019,
                )
            )
        for index in range(3):
            work = f"{three_id}-work-{index}"
            rows.append(
                candidate(
                    f"three-{index}",
                    three_id,
                    f"{work}#0",
                    estimated_cost=0.3246,
                )
            )
        return {
            "version": 5,
            "candidates": rows,
            "candidate_count_before_pareto": len(rows),
            "candidate_count_after_pareto": len(rows),
            "pareto_pruned_count": 0,
            "interpretations": {
                five_id: interpretation(five_id, 5),
                three_id: interpretation(three_id, 3),
            },
        }

    def test_independent_node_and_cost_minima_are_never_combined(self):
        report = build_infeasibility_report(
            self.bundle(),
            GraphLimits(
                max_nodes=3,
                max_edges=16,
                max_stages=8,
                max_model_calls=4,
                max_retries=0,
                max_replacements=1,
                max_budget_usd=0.65,
                cost_risk_multiplier=1.18,
            ),
            message="No feasible V5 execution graph",
        )

        self.assertEqual(report["code"], "BUDGET_INSUFFICIENT_COST")
        self.assertEqual(report["minimum_required_nodes"], 3)
        # The backward-compatible top-level cost must belong to an
        # interpretation that fits max_nodes=3, not the cheaper 5-node graph.
        self.assertGreater(report["minimum_effective_expected_cost_usd"], 0.97)

        joint = report["joint_limit_diagnostics"]
        self.assertLess(
            joint["minimum_effective_expected_cost_usd_any_interpretation"],
            0.52,
        )
        self.assertGreater(
            joint["minimum_effective_expected_cost_usd_within_node_limit"],
            0.97,
        )
        self.assertGreater(
            joint["minimum_hard_runtime_budget_usd_within_node_limit"],
            1.14,
        )
        self.assertEqual(joint["minimum_required_nodes_within_planning_budget"], 5)
        self.assertEqual(joint["jointly_feasible_interpretation_ids"], [])
        self.assertTrue(joint["independent_minima_must_not_be_combined"])

        options = {
            option["interpretation_id"]: option
            for option in report["feasible_remediation_options"]
        }
        cheap = options["interpretation-five-cheap"]
        expensive = options["interpretation-three-expensive"]
        self.assertEqual(cheap["required_initial_nodes"], 5)
        self.assertEqual(
            cheap["required_total_calls_with_current_recovery_reserve"],
            6,
        )
        self.assertLess(cheap["minimum_hard_runtime_budget_usd"], 0.61)
        self.assertFalse(cheap["fits_current_node_limit"])
        self.assertTrue(cheap["fits_current_raw_budget"])
        self.assertTrue(expensive["fits_current_node_limit"])
        self.assertFalse(expensive["fits_current_raw_budget"])
        self.assertEqual(len(cheap["minimum_node_solution_candidate_ids"]), 5)
        self.assertEqual(len(expensive["minimum_node_solution_candidate_ids"]), 3)

    def test_zero_call_planning_failure_has_one_truthful_root_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "report-comments").mkdir()
            (root / "ticket-status.json").write_text(
                json.dumps(
                    {
                        "accepted": True,
                        "calls": 4,
                        "maximum_recovery_calls": 1,
                        "maximum_initial_calls": 3,
                        "cost_policy": "unbounded_with_anomaly_guard",
                        "cost_anomaly_usd": 0.65,
                    }
                ),
                encoding="utf-8",
            )
            (root / "expert-team-result.json").write_text(
                json.dumps(
                    {
                        "runtime_version": "v5-native-runtime-1",
                        "status": "failed",
                        "fallback_used": False,
                        "legacy_runtime_present": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "production-runtime.json").write_text(
                json.dumps(
                    {
                        "runtime_version": "v5-native-runtime-1",
                        "fallback_policy": "fail-closed-no-alternate-runtime",
                        "legacy_runtime_present": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "expert-team-error.json").write_text(
                json.dumps(
                    {
                        "error_code": "BUDGET_INSUFFICIENT_COST",
                        "stage": "planning",
                        "message": "joint node/cost limits are infeasible",
                        "retryable": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "v5-planning-infeasibility.json").write_text(
                json.dumps(
                    {
                        "code": "BUDGET_INSUFFICIENT_COST",
                        "status": "INFEASIBLE",
                        "model_calls_performed": 0,
                        "fallback_used": False,
                    }
                ),
                encoding="utf-8",
            )
            (root / "report-comments" / "report-comments-manifest.json").write_text(
                json.dumps(
                    {
                        "publication_status": "skipped_failed_execution",
                        "comment_count": 0,
                        "files": [],
                    }
                ),
                encoding="utf-8",
            )

            result = audit(
                root,
                execute_outcome="failure",
                publish_outcome="success",
            )

        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["primary_failure"]["code"], "BUDGET_INSUFFICIENT_COST")
        self.assertEqual(result["primary_failure"]["stage"], "planning")
        self.assertEqual(len(result["failures"]), 1)
        self.assertTrue(result["failures"][0].startswith("planning failed before model calls:"))
        joined = "\n".join(result["failures"])
        self.assertNotIn("0 > 3", joined)
        self.assertNotIn("request audit", joined.casefold())
        self.assertNotIn("Provider evidence", joined)
        self.assertNotIn("executor evidence", joined)
        self.assertEqual(result["stage_status"]["requests"], "NOT_APPLICABLE")
        self.assertEqual(result["stage_status"]["graph"], "INFEASIBLE")
        self.assertEqual(
            result["stage_status"]["report"],
            "SKIPPED_FAILED_EXECUTION",
        )
        self.assertTrue(result["checks"]["planning_failure_evidence_valid"])
        self.assertFalse(result["checks"]["downstream_execution_stages_applicable"])


if __name__ == "__main__":
    unittest.main()
