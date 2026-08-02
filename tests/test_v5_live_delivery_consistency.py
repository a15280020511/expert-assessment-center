from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import (  # noqa: E402
    ExecutionGraph,
    GraphLimits,
    SelectedEdge,
    SelectedNode,
)
import v5_task_delivery_contract as contracts  # noqa: E402
from v5_constitutional_pipeline import _planning_limits  # noqa: E402
from v5_recovery_runtime import build_production_runtime  # noqa: E402
from v5_runtime import RuntimeConfig, RuntimeNodeResult  # noqa: E402
from v5_task_constraints import (  # noqa: E402
    fact_claim_supported,
    validate_answer_evidence,
)


HEADINGS = [
    "已知事实与未知",
    "风险与隔离",
    "立即行动",
    "门禁与沟通",
    "物资核对与记录",
    "条件式移交结论",
]


class V5LiveDeliveryConsistencyTests(unittest.TestCase):
    @staticmethod
    def _node(node_id: str, work_id: str, *, synthesis: bool = False) -> SelectedNode:
        return SelectedNode(
            node_id=node_id,
            assigned_work=(work_id,),
            professional_capabilities={"analysis": 0.8},
            functions=("synthesis",) if synthesis else ("analysis",),
            prompt_profile={},
            reasoning_profile={},
            parameter_profile={},
            model=f"company-{node_id}/model",
            provider_endpoint=f"company-{node_id}/model@provider-{node_id}",
            output_contract={
                "required_fields": [],
                "machine_readable_required": False,
            },
            estimated_quality=0.8,
            quality_uncertainty=0.05,
            estimated_cost=0.001,
            failure_probability=0.01,
            request_config={
                "provider": {
                    "only": [f"provider-{node_id}"],
                    "order": [f"provider-{node_id}"],
                    "allow_fallbacks": False,
                }
            },
        )

    @classmethod
    def _graph(cls, *, content_count: int = 2) -> ExecutionGraph:
        contents = [
            cls._node(f"content-{index}", f"work-{index}")
            for index in range(content_count)
        ]
        final = cls._node("final", "work-final", synthesis=True)
        return ExecutionGraph(
            nodes=tuple([*contents, final]),
            edges=tuple(
                SelectedEdge(
                    source=node.node_id,
                    target=final.node_id,
                    relation_type="synthesis",
                    payload_type="node-contract",
                    visibility_policy="final-only",
                )
                for node in contents
            ),
            execution_stages=(
                tuple(node.node_id for node in contents),
                (final.node_id,),
            ),
            entry_nodes=tuple(node.node_id for node in contents),
            final_nodes=(final.node_id,),
            required_work=tuple(
                [*(node.assigned_work[0] for node in contents), "work-final"]
            ),
            estimated_quality=0.8,
            quality_floor=0.7,
            estimated_total_cost=0.003,
            metadata={},
        )

    @staticmethod
    def _result(node: SelectedNode, status: str) -> RuntimeNodeResult:
        return RuntimeNodeResult(
            node_id=node.node_id,
            assigned_work=node.assigned_work,
            status=status,
            selected_model=node.model,
            resolved_model=node.model,
            provider_endpoint=node.provider_endpoint,
            answer=f"usable answer for {node.node_id}",
            quality_score=0.9,
            attempts=[],
            actual_cost_usd=0.0,
            contract={"required_fields_complete": True},
        )

    def test_cardinality_copula_is_supported_without_weakening_binding(self) -> None:
        task = (
            "仅依据题面，不得编造。某夜间社区服务站只有2名值守人员。"
            "纸质登记表显示4台设备已经交接，但现场只能确认3台。"
        )
        claim = "值守人员为 2 名。"
        self.assertTrue(fact_claim_supported(task, claim))
        answer = f"**事实｜来源：题面**：{claim}"
        self.assertEqual(validate_answer_evidence(task, answer), [])
        swapped = (
            "**事实｜来源：题面**：登记表显示3台设备已经交接，"
            "现场确认4台。"
        )
        self.assertTrue(validate_answer_evidence(task, swapped))

    def test_uncounted_strict_h2_list_is_extracted_exactly(self) -> None:
        task = (
            "最终输出必须且只能使用以下Markdown二级标题，并严格按此顺序，"
            "每节非空：已知事实与未知；风险与隔离；立即行动；门禁与沟通；"
            "物资核对与记录；条件式移交结论。不得出现任何其他Markdown二级标题。"
        )
        extracted = contracts.extract_explicit_markdown_contract(task)
        self.assertEqual(extracted["exact_markdown_headings"], HEADINGS)
        self.assertEqual(
            extracted["contract_extraction_policy"],
            "explicit-format-text-only-inline-inferred-count",
        )
        applied = contracts.apply_explicit_contract(
            task,
            {"synthesis": 1.0},
            {"required_fields": ["conclusions"]},
        )
        self.assertEqual(applied["required_fields"], HEADINGS)
        self.assertTrue(applied["explicit_markdown_contract"])

    def test_planning_minimum_is_clamped_to_content_call_capacity(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(4, 1, 0.35, "value")
        )
        profile = SimpleNamespace(high_stakes=True, complexity_score=7)
        limits = _planning_limits(
            total_calls=4,
            recovery_calls=1,
            planning_nodes=3,
            anomaly_budget=0.35,
            runtime=runtime,
            task="仅依据题面，最终输出必须使用明确结构。",
            profile=profile,
            resource_shape={
                "maximum_atomic_work": 8,
                "maximum_synthesis_work": 1,
                "explicit_output_contract": True,
            },
        )
        self.assertEqual(limits.max_nodes, 3)
        self.assertEqual(limits.min_successful_content_nodes, 2)

    def test_preflight_rejects_structurally_impossible_content_minimum(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(3, 1, 0.35, "value")
        )
        graph = self._graph(content_count=1)
        limits = GraphLimits(
            max_nodes=2,
            max_model_calls=3,
            max_retries=1,
            max_replacements=1,
            max_budget_usd=0.35,
            min_required_work_coverage=1.0,
            min_successful_content_nodes=2,
            allow_degraded_success=False,
        )
        preflight = runtime.execution_engine._preflight(graph, limits)
        self.assertEqual(preflight["status"], "rejected")
        self.assertIn(
            "planned-content-nodes-below-delivery-minimum",
            preflight["blockers"],
        )
        self.assertEqual(
            preflight["delivery_feasibility"]["planned_content_node_count"],
            1,
        )

    def test_degraded_node_never_becomes_full_success(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(4, 1, None, "value")
        )
        graph = self._graph(content_count=2)
        statuses = {
            "content-0": "success_degraded",
            "content-1": "success",
            "final": "success",
        }

        def fake_execute(node, *_args, **_kwargs):
            return self._result(node, statuses[node.node_id])

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(
                runtime.execution_engine,
                "execute_node",
                side_effect=fake_execute,
            ):
                with self.assertRaises(RuntimeError):
                    runtime.execute_graph(
                        graph,
                        SimpleNamespace(parallel_workers=3),
                        "仅依据题面，不得编造。",
                        output_dir=directory,
                        limits=GraphLimits(
                            max_nodes=3,
                            max_model_calls=4,
                            max_retries=1,
                            max_replacements=1,
                            min_required_work_coverage=1.0,
                            min_successful_content_nodes=2,
                            allow_degraded_success=False,
                        ),
                    )
            summary = json.loads(
                (Path(directory) / "v5-execution-summary.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["completion_mode"], "degraded")
        self.assertNotEqual(summary["quality_status"], "full_success")
        self.assertTrue(summary["degradation"]["used"])
        self.assertEqual(summary["work_coverage"]["usable_content_nodes"], 2)
        self.assertEqual(summary["work_coverage"]["successful_content_nodes"], 1)
        self.assertIn(
            "insufficient-successful-content-nodes",
            summary["delivery_policy"]["blockers"],
        )
        self.assertIn(
            "degraded-success-disabled",
            summary["delivery_policy"]["blockers"],
        )
        self.assertEqual(summary["execution_stages"][0]["status"], "degraded")

    def test_all_strict_nodes_can_reach_full_success(self) -> None:
        runtime = build_production_runtime(
            RuntimeConfig(4, 1, None, "value")
        )
        graph = self._graph(content_count=2)

        def fake_execute(node, *_args, **_kwargs):
            return self._result(node, "success")

        with patch.object(
            runtime.execution_engine,
            "execute_node",
            side_effect=fake_execute,
        ):
            result = runtime.execute_graph(
                graph,
                SimpleNamespace(parallel_workers=3),
                "仅依据题面，不得编造。",
                limits=GraphLimits(
                    max_nodes=3,
                    max_model_calls=4,
                    max_retries=1,
                    max_replacements=1,
                    min_required_work_coverage=1.0,
                    min_successful_content_nodes=2,
                    allow_degraded_success=False,
                ),
            )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["completion_mode"], "full")
        self.assertEqual(result["quality_status"], "full_success")
        self.assertFalse(result["degradation"]["used"])
        self.assertEqual(result["delivery_policy"]["blockers"], [])


if __name__ == "__main__":
    unittest.main()
