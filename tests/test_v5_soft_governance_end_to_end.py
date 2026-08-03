from __future__ import annotations

import inspect
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from v5_claude_red_team_policy import build_claude_red_team_request  # noqa: E402
from v5_cost_reliability_hardening import hardened_build_node_payload  # noqa: E402
from v5_gpt_expert_selector import parse_proposal  # noqa: E402
from v5_independent_artifact_revalidation import _cost_audit  # noqa: E402
from v5_runtime import BudgetController, ExecutionEngine, RuntimeConfig  # noqa: E402


def _node() -> SelectedNode:
    return SelectedNode(
        node_id="n1",
        assigned_work=("w1",),
        professional_capabilities={},
        functions=("synthesis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning"]},
        model="company/model",
        provider_endpoint="company/model@provider-a",
        output_contract={},
        estimated_quality=0.9,
        quality_uncertainty=0.1,
        estimated_cost=1.0,
        failure_probability=0.01,
        request_config={
            "provider": {
                "only": ["provider-a"],
                "order": ["provider-a"],
                "allow_fallbacks": False,
            }
        },
    )


def _graph() -> ExecutionGraph:
    node = _node()
    return ExecutionGraph(
        nodes=(node,),
        edges=(),
        execution_stages=((node.node_id,),),
        entry_nodes=(node.node_id,),
        final_nodes=(node.node_id,),
        required_work=("w1",),
        estimated_quality=0.9,
        quality_floor=0.8,
        estimated_total_cost=1.0,
        metadata={},
    )


class SoftGovernanceEndToEndTests(unittest.TestCase):
    def test_base_budget_controller_never_cost_denies_or_invalidates(self) -> None:
        budget = BudgetController(RuntimeConfig(2, 1, 0.01), _graph())
        allowed, reason = budget.reserve("initial", 10.0, "n1")
        self.assertTrue(allowed)
        self.assertEqual("", reason)
        self.assertFalse(budget.reconcile(20.0))
        snapshot = budget.snapshot()
        self.assertTrue(snapshot["cost_advisory_exceeded"])
        self.assertFalse(snapshot["cost_limit_enforced"])
        self.assertEqual([], snapshot["denials"])

    def test_cost_advisory_does_not_force_serial_execution(self) -> None:
        source = inspect.getsource(ExecutionEngine._execute_stage)
        self.assertNotIn("workers = 1 if", source)
        self.assertIn("min(configured, len(stage))", source)

    def test_expert_upstream_answer_is_not_locally_truncated(self) -> None:
        answer = "证据" * 20_000
        payload = hardened_build_node_payload(
            _node(),
            "完成任务",
            [{"node_id": "upstream", "answer": answer}],
        )
        user = payload["messages"][1]["content"]
        self.assertIn(answer, user)
        self.assertNotIn("上游结果已确定性压缩", user)
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("max_completion_tokens", payload)

    def test_claude_request_has_no_local_output_ceiling(self) -> None:
        task = "完整任务"
        payload = {
            "task_digest": "a" * 64,
            "proposal_digest": "b" * 64,
            "approved_total_calls": 4,
            "governance_calls_reserved": 3,
            "approved_recovery_calls": 0,
            "cost_anomaly_usd": 0.01,
            "task_excerpt": task,
            "task_characters": len(task),
            "task_truncated": False,
            "task_constraints": {},
            "explicit_delivery_contract": {},
            "work_items": [{
                "work_id": "w1",
                "objective": "完成任务",
                "dependencies": [],
                "required_outputs": ["结论"],
            }],
            "nodes": [{
                "node_id": "n1",
                "candidate_id": "company/model@provider-a",
                "work_ids": ["w1"],
                "role": "专家",
                "functions": ["synthesis"],
                "model": "company/model",
                "company": "company",
                "provider": "provider-a",
                "estimated_cost_usd": 1.0,
                "contract_kind": "gpt-authored-expert-node",
                "recovery_candidates": [],
            }],
            "edges": [],
            "final_nodes": ["n1"],
        }
        request = build_claude_red_team_request(payload)
        self.assertNotIn("max_tokens", request)
        self.assertNotIn("max_completion_tokens", request)

    def test_gpt_parser_has_no_total_character_ceiling(self) -> None:
        proposal = {
            "work_items": [{
                "work_id": "w1",
                "objective": "完成任务",
                "dependencies": [],
                "required_outputs": ["结论"],
            }],
            "nodes": [{
                "node_id": "n1",
                "work_ids": ["w1"],
                "role": "专家",
                "functions": ["synthesis"],
                "model": "company/model",
                "provider": "provider-a",
                "reasoning_effort": "high",
                "max_output_tokens": 100_000,
                "recovery": [],
            }],
            "edges": [],
            "final_nodes": ["n1"],
        }
        rendered = json.dumps(proposal) + (" " * 100_000)
        parsed = parse_proposal(rendered)
        self.assertEqual(100_000, parsed["nodes"][0]["max_output_tokens"])

    def test_independent_revalidation_cost_is_advisory_only(self) -> None:
        nodes = [{"actual_cost_usd": 2.0}]
        summary = {"actual_cost_usd": 3.0, "expert_actual_cost_usd": 2.0}
        ledger = {"summary": {"provider_actual_cost_usd": 3.0}}
        expert, total, recorded, failures = _cost_audit(
            nodes, summary, ledger, 1.0, 0.01
        )
        self.assertEqual((2.0, 3.0, 3.0), (expert, total, recorded))
        self.assertEqual([], failures)

    def test_obsolete_hard_resource_workflow_is_absent(self) -> None:
        self.assertFalse(
            (ROOT / ".github/workflows/v5-final-paid-claude-acceptance-20260803.yml").exists()
        )
        free = (ROOT / ".github/workflows/v5-free-model-qualification.yml").read_text()
        self.assertNotIn('"max_tokens":', free)


if __name__ == "__main__":
    unittest.main()
