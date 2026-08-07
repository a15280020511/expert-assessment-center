import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode  # noqa: E402
from v5_dynamic_parameter_graph import build_dynamic_planning_context  # noqa: E402
from v5_governance_model_plan import plan_sha256  # noqa: E402
from v5_governed_plan_orchestrator import build_governed_proposal  # noqa: E402
from v5_production_expert_policy import EvidenceCompleteExecutionEngine  # noqa: E402
from v5_runtime import (  # noqa: E402
    OutputPolicy,
    QualityGatePolicy,
    RecoveryPolicy,
    RetryPolicy,
    RuntimeConfig,
    RuntimeNodeResult,
)
from v5_soft_resource_governance import SoftResourcePromptPolicy  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"


def candidates(count: int = 8) -> list[dict]:
    return [
        {
            "model": f"vendor-{index}/model-{index}",
            "popularity_rank": index,
            "official_intelligence_rank": index,
            "prompt_usd_per_million": float(index),
            "completion_usd_per_million": float(index * 2),
            "context_length": 100000 + index,
            "max_completion_tokens": 8000,
        }
        for index in range(1, count + 1)
    ]


class TaskDerivedParameterGraphTests(unittest.TestCase):
    def test_parameter_instances_change_with_current_task_graph(self) -> None:
        simple = {
            "task": {"question": "给出一个简短判断"},
        }
        branched = {
            "task": {
                "question": "比较多个方案并给出最终结论",
                "work_graph": {
                    "work_units": [
                        {"id": "a", "kind": "baseline", "text": "建立基线"},
                        {"id": "b", "kind": "branch-left", "text": "分析左分支", "depends_on": ["a"]},
                        {"id": "c", "kind": "branch-right", "text": "分析右分支", "depends_on": ["a"]},
                        {"id": "d", "kind": "challenge", "text": "交叉挑战", "depends_on": ["b", "c"]},
                    ]
                },
            }
        }
        simple_plan = build_dynamic_planning_context(simple, candidates())
        branched_plan = build_dynamic_planning_context(branched, candidates())
        simple_ids = set(simple_plan["parameter_requirements"]["required_parameter_ids"])
        branched_ids = set(branched_plan["parameter_requirements"]["required_parameter_ids"])
        self.assertNotIn("dependency_weight", simple_ids)
        self.assertNotIn("parallelism_bias", simple_ids)
        self.assertIn("dependency_weight", branched_ids)
        self.assertIn("parallelism_bias", branched_ids)
        self.assertNotEqual(simple_ids, branched_ids)
        self.assertFalse(branched_plan["fixed_parameter_template_used"])
        self.assertFalse(branched_plan["fixed_role_grammar_used"])
        audit = branched_plan["resolved_parameters"]["parameter_coverage_audit"]
        self.assertEqual("PASS", audit["status"])
        self.assertEqual(0, audit["fixed_business_parameter_count"])

    def test_role_dependencies_are_derived_from_work_dag_not_three_role_grammar(self) -> None:
        packet = {
            "task": {
                "work_graph": {
                    "work_units": [
                        {"id": "a", "kind": "baseline", "text": "A"},
                        {"id": "b", "kind": "left-specialist", "text": "B", "depends_on": ["a"]},
                        {"id": "c", "kind": "right-specialist", "text": "C", "depends_on": ["a"]},
                        {"id": "d", "kind": "adversarial-check", "text": "D", "depends_on": ["b", "c"]},
                    ]
                }
            }
        }
        plan = build_dynamic_planning_context(packet, candidates(12))
        roles = plan["role_plan"]
        self.assertGreaterEqual(len(roles), 3)
        self.assertTrue(all(str(row["role_kind"]).startswith("dynamic:") for row in roles))
        self.assertTrue(any(len(row["depends_on_role_ids"]) >= 2 for row in roles))
        self.assertFalse({row["role_kind"] for row in roles}.issubset({"independent", "review", "synthesis"}))

    def test_orchestrator_preserves_explicit_arbitrary_role_dag(self) -> None:
        ticket = json.loads(FIXTURE.read_text(encoding="utf-8"))
        source = ticket["governance_model_plan"]["selected_models"]
        rows = [copy.deepcopy(source[index % len(source)]) for index in range(4)]
        role_rows = [
            ("baseline", "dynamic:baseline", []),
            ("left", "dynamic:left-specialist", ["baseline"]),
            ("right", "dynamic:right-specialist", ["baseline"]),
            ("arbiter", "dynamic:arbiter", ["left", "right"]),
        ]
        for index, (role_id, kind, deps) in enumerate(role_rows):
            rows[index]["model"] = f"vendor-{index}/model-{index}"
            rows[index]["role_id"] = role_id
            rows[index]["role_kind"] = kind
            rows[index]["depends_on_role_ids"] = deps
            rows[index]["assigned_work_units"] = [f"unit-{index}"]
            rows[index]["functions"] = [f"function-{index}"]
            rows[index]["final_role"] = role_id == "arbiter"
        plan = ticket["governance_model_plan"]
        plan["selected_models"] = rows
        plan["expert_count"] = 4
        plan["recovery_models"] = []
        plan["recovery_count"] = 0
        plan["plan_sha256"] = plan_sha256(plan)
        proposal, audit = build_governed_proposal(ticket=ticket, catalog={}, task_envelope={})
        self.assertEqual(
            [row["role_kind"] for row in proposal["nodes"]],
            [row[1] for row in role_rows],
        )
        self.assertEqual(4, len(proposal["edges"]))
        self.assertEqual(1, len(proposal["final_nodes"]))
        self.assertFalse(audit["fixed_role_topology_used"])
        self.assertFalse(audit["fixed_role_grammar_used"])
        self.assertFalse(audit["role_dependencies_recomputed_from_role_kind"])


class ProductionEntryRegressionTests(unittest.TestCase):
    @staticmethod
    def _node(node_id: str, model: str, work: str) -> SelectedNode:
        return SelectedNode(
            node_id=node_id,
            assigned_work=(work,),
            professional_capabilities={},
            functions=("analysis",),
            prompt_profile={},
            reasoning_profile={},
            parameter_profile={},
            model=model,
            provider_endpoint=f"{model}@openrouter-auto",
            output_contract={},
            estimated_quality=0.0,
            quality_uncertainty=0.0,
            estimated_cost=0.0,
            request_config={},
        )

    def test_production_soft_entry_initializes_standby_and_uses_graph_call_capacity(self) -> None:
        nodes = (
            self._node("n1", "vendor/a", "w1"),
            self._node("n2", "vendor/b", "w2"),
        )
        graph = ExecutionGraph(
            nodes=nodes,
            edges=(SelectedEdge("n1", "n2", "dependency", "structured", "declared-edge-only"),),
            execution_stages=(("n1",), ("n2",)),
            entry_nodes=("n1",),
            final_nodes=("n2",),
            required_work=("w1", "w2"),
            estimated_quality=0.0,
            quality_floor=0.0,
            estimated_total_cost=0.0,
            metadata={
                "recovery_pool": {},
                "standby_inventory": [
                    {"model": "vendor/c", "provider_endpoint": "vendor/c@openrouter-auto"},
                    {"model": "vendor/d", "provider_endpoint": "vendor/d@openrouter-auto"},
                ],
            },
        )

        class Probe(EvidenceCompleteExecutionEngine):
            def __init__(self) -> None:
                super().__init__(
                    RuntimeConfig(1, 0, None),
                    prompt_policy=SoftResourcePromptPolicy(),
                    retry_policy=RetryPolicy(),
                    recovery_policy=RecoveryPolicy(),
                    quality_policy=QualityGatePolicy(),
                    output_policy=OutputPolicy(),
                )
                self.executed = []
                self.active_limits_seen = []

            def _preflight(self, graph, limits=None):
                return {"blockers": []}

            def execute_node(self, selected, original_task, upstream, run, call_fn, recovery_rows, budget):
                self.executed.append(selected.node_id)
                self.active_limits_seen.append(self.config.total_call_limit)
                budget.calls_reserved += 1
                return RuntimeNodeResult(
                    node_id=selected.node_id,
                    assigned_work=selected.assigned_work,
                    status="success",
                    selected_model=selected.model,
                    resolved_model=selected.model,
                    provider_endpoint=selected.provider_endpoint,
                    answer="usable answer",
                    quality_score=1.0,
                    attempts=[],
                    actual_cost_usd=0.0,
                    contract={},
                )

            def _delivery_state(self, graph, outputs, limits):
                return {}

            def _delivery_blockers(self, state, limits):
                return [], []

            def _execution_result(self, graph, outputs, records, budget, preflight, limits, state, blockers, missing):
                return {
                    "status": "success",
                    "completion_mode": "full",
                    "quality_status": "full_success",
                    "final_answer": "ok",
                    "execution_budget": budget.snapshot(),
                    "runtime_feedback_replanning": self._feedback_snapshot(),
                }

            def _write_artifacts(self, root, result, outputs):
                return None

            def _raise_failed_result(self, result):
                return None

        engine = Probe()
        result = engine._execute_graph_soft(graph, object(), "task", limits=GraphLimits())
        self.assertEqual(["n1", "n2"], engine.executed)
        self.assertTrue(all(value == 4 for value in engine.active_limits_seen))
        feedback = result["runtime_feedback_replanning"]
        self.assertTrue(feedback["enabled"])
        self.assertEqual(2, feedback["standby_total"])
        self.assertFalse(result["resource_governance"]["requested_call_ceiling_can_stop_execution"])
        self.assertEqual(1, engine.config.total_call_limit)


if __name__ == "__main__":
    unittest.main()
