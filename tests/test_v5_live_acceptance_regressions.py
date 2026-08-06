from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_production_answer_normalization import (  # noqa: E402
    relabel_task_derived_fact_lines,
)
from v5_production_governance_policy import (  # noqa: E402
    _proposal_policy_violations,
    build_proposal_request,
)
from v5_run_evidence import (  # noqa: E402
    ApprovedRun,
    EvidenceInputs,
    _validated_nodes,
)
from v5_task_constraints import (  # noqa: E402
    compile_task_constraints,
    normalized_quantities,
    validate_answer_evidence,
)

TASK = (
    "某机构只能选择一个实施方案。已知方案A成本100万元、交付30天、失败概率12%；"
    "方案B成本140万元、交付20天、失败概率7%；方案C成本220万元、交付10天、"
    "失败概率4%。仅依据这些题面数据，比较成本、时效和风险，给出条件化选择。"
    "不得引入题面外事实。"
)


def _inputs(
    materialization: dict[str, object],
    metadata: dict[str, object],
) -> EvidenceInputs:
    return EvidenceInputs(
        runtime_config={},
        catalog_snapshot={},
        execution_graph={"nodes": [{"node_id": "N1"}], "metadata": metadata},
        node_results=(),
        final_report="",
        execution_summary={},
        selection={"optimizer_used": False, "materialization": materialization},
        ticket={},
        request_audit={},
        governance_result={},
        governance_ledger={},
    )


def _clean_metadata() -> dict[str, object]:
    return {
        "local_scoring_used": False,
        "optimizer_used": False,
        "cp_sat_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
    }


class LiveAcceptanceRegressionTests(unittest.TestCase):
    def test_absent_optional_materialization_flags_are_not_legacy(self) -> None:
        nodes = _validated_nodes(
            _inputs(
                {"local_scoring_used": False, "optimizer_used": False},
                _clean_metadata(),
            ),
            ApprovedRun(
                total_calls=10,
                recovery_calls=3,
                cost_anomaly_usd=1.0,
            ),
        )
        self.assertEqual(1, len(nodes))

    def test_present_true_legacy_flag_still_fails_closed(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "cp_sat_used"):
            _validated_nodes(
                _inputs(
                    {
                        "local_scoring_used": False,
                        "optimizer_used": False,
                        "cp_sat_used": True,
                    },
                    _clean_metadata(),
                ),
                ApprovedRun(
                    total_calls=10,
                    recovery_calls=3,
                    cost_anomaly_usd=1.0,
                ),
            )

    def test_graph_metadata_must_explicitly_prove_algorithms_absent(self) -> None:
        metadata = _clean_metadata()
        metadata.pop("heuristic_ranking_used")
        with self.assertRaisesRegex(RuntimeError, "heuristic_ranking_used"):
            _validated_nodes(
                _inputs({}, metadata),
                ApprovedRun(
                    total_calls=10,
                    recovery_calls=3,
                    cost_anomaly_usd=1.0,
                ),
            )

    def test_scaled_currency_magnitude_is_preserved(self) -> None:
        task_values = normalized_quantities(TASK)
        self.assertIn(("1000000", "", "yuan"), task_values)
        self.assertIn(("1400000", "", "yuan"), task_values)
        self.assertIn(("2200000", "", "yuan"), task_values)
        introduced = normalized_quantities("推断：成本增加40万元。") - task_values
        self.assertIn(("400000", "", "yuan"), introduced)

    def test_paid_answer_derived_fact_labels_are_relabelled_only(self) -> None:
        answer = """## 已知条件

- 事实：可选对象仅为方案A、方案B、方案C，且机构只能选择一个实施方案。
- 事实：题面给出的可比指标只有成本、交付时间、失败概率三类。
- 事实：题面未给出指标权重、预算上限、最晚交付期限或最高可接受失败概率。

## 计算与比较

- 事实：交付时间比较为C“10天”优于B“20天”，B“20天”优于A“30天”。
- 事实：失败概率比较为C“4%”优于B“7%”，B“7%”优于A“12%”。
- 事实：成本大小关系为A低于B，B低于C；因此A是成本单项最低者。
"""
        normalized, audit = relabel_task_derived_fact_lines(TASK, answer)
        self.assertTrue(audit["applied"])
        self.assertGreaterEqual(len(audit["changes"]), 5)
        self.assertNotIn("- 事实：可选对象", normalized)
        self.assertIn("- 推断：可选对象", normalized)
        violations = validate_answer_evidence(
            TASK,
            normalized,
            compile_task_constraints(TASK),
        )
        self.assertFalse(
            any(value.startswith("unsupported-fact-label") for value in violations),
            violations,
        )

    def test_supported_source_fact_is_not_relabelled(self) -> None:
        answer = "- 事实：方案A成本100万元。\n"
        normalized, audit = relabel_task_derived_fact_lines(TASK, answer)
        self.assertEqual(answer, normalized)
        self.assertFalse(audit["applied"])

    def test_recovery_pool_must_use_complete_reserve(self) -> None:
        proposal = {
            "work_items": [
                {
                    "work_id": f"W{index}",
                    "objective": "work",
                    "dependencies": [],
                    "required_outputs": ["qualitative comparison"],
                }
                for index in range(1, 4)
            ],
            "nodes": [
                {
                    "node_id": f"N{index}",
                    "work_ids": [f"W{index}"],
                    "role": "analyst",
                    "functions": [],
                    "recovery": [],
                }
                for index in range(1, 4)
            ],
            "edges": [],
            "final_nodes": ["N3"],
        }
        envelope = {
            "task_constraints": {
                "unsupported_precise_quantities_allowed": False
            }
        }
        violations = _proposal_policy_violations(
            proposal,
            TASK,
            envelope,
            approved_recovery_calls=3,
        )
        self.assertTrue(any("candidate count" in value for value in violations))
        for index, node in enumerate(proposal["nodes"], start=1):
            node["recovery"] = [
                {
                    "model": f"company{index}/model",
                    "provider": f"provider{index}",
                }
            ]
        self.assertEqual(
            [],
            _proposal_policy_violations(
                proposal,
                TASK,
                envelope,
                approved_recovery_calls=3,
            ),
        )

    def test_closed_world_work_output_rejects_new_delta(self) -> None:
        proposal = {
            "work_items": [
                {
                    "work_id": "W1",
                    "objective": "compare",
                    "dependencies": [],
                    "required_outputs": ["成本增加40万元"],
                }
            ],
            "nodes": [
                {
                    "node_id": "N1",
                    "work_ids": ["W1"],
                    "role": "analyst",
                    "functions": [],
                    "recovery": [],
                }
            ],
            "edges": [],
            "final_nodes": ["N1"],
        }
        violations = _proposal_policy_violations(
            proposal,
            TASK,
            {
                "task_constraints": {
                    "unsupported_precise_quantities_allowed": False
                }
            },
            approved_recovery_calls=0,
        )
        self.assertTrue(any("unsupported quantity" in value for value in violations))

    def test_governance_request_states_recovery_contract(self) -> None:
        base = {
            "model": "~openai/gpt-latest",
            "messages": [
                {"role": "system", "content": "fixed"},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"execution_constraints": {}},
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        with patch(
            "v5_production_governance_policy._build_proposal_request",
            return_value=base,
        ):
            request = build_proposal_request(approved_recovery_calls=3)
        payload = json.loads(request["messages"][1]["content"])
        constraints = payload["execution_constraints"]
        self.assertEqual(3, constraints["recovery_candidate_count_required"])
        self.assertTrue(
            constraints["recovery_candidates_are_preselected_not_calls"]
        )

    def test_acceptance_workflow_uses_governance_ticket_budget(self) -> None:
        text = (
            ROOT / ".github/workflows/v5-price-ranked-paid-candidate-acceptance.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(".approved_budget.calls", text)
        self.assertIn(".approved_budget.maximum_recovery_calls", text)
        self.assertIn("--expected-calls \"$calls\"", text)
        self.assertIn("--expected-recovery-calls \"$recovery\"", text)
        self.assertIn("--maximum-total-calls", text)
        self.assertIn("--maximum-recovery-calls", text)
        self.assertNotIn('MAXIMUM_TOTAL_CALLS: "10"', text)
        self.assertNotIn('MAXIMUM_RECOVERY_CALLS: "3"', text)
        self.assertNotIn("allow_fallbacks: true", text)

    def test_machine_policy_locks_recovery_and_scaled_currency(self) -> None:
        policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )
        recovery = policy["recovery_planning"]
        self.assertTrue(
            recovery[
                "candidate_count_must_equal_approved_recovery_call_reserve"
            ]
        )
        self.assertFalse(recovery["provider_fallback_allowed"])
        self.assertFalse(
            recovery["hardcoded_model_or_provider_blacklist_allowed"]
        )
        quantity = policy["closed_world_quantity_normalization"]
        self.assertEqual(
            ["万元", "亿元"],
            quantity["scaled_chinese_currency_units_supported"],
        )
        self.assertTrue(quantity["magnitude_must_be_preserved"])
        self.assertFalse(quantity["unsupported_derived_precise_quantities_allowed"])


if __name__ == "__main__":
    unittest.main()
