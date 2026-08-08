from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from execution_graph_validator import validate_execution_graph  # noqa: E402
from v5_execution_primitives import quality_gate, system_prompt  # noqa: E402
from v5_runtime import ExecutionEngine  # noqa: E402
from v5_task_envelope import work_output_contract  # noqa: E402


def node(*, node_id: str, work: str, final: bool) -> SelectedNode:
    return SelectedNode(
        node_id=node_id,
        assigned_work=(work,),
        professional_capabilities={},
        functions=(),
        prompt_profile={"modules": [], "role": "测试节点"},
        reasoning_profile={"reasoning_enabled": True, "effort": "low"},
        parameter_profile={},
        model="company/model",
        provider_endpoint="provider-a",
        output_contract=work_output_contract(
            "给出结论", ["结论"], final_node=final
        ),
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        request_config={
            "provider": {
                "only": ["provider-a"],
                "order": ["provider-a"],
                "allow_fallbacks": False,
            }
        },
    )


class OptionalFunctionsSemanticsTests(unittest.TestCase):
    def test_empty_functions_do_not_invalidate_graph(self) -> None:
        selected = node(node_id="node-final", work="work-final", final=True)
        graph = ExecutionGraph(
            nodes=(selected,),
            edges=(),
            execution_stages=((selected.node_id,),),
            entry_nodes=(selected.node_id,),
            final_nodes=(selected.node_id,),
            required_work=("work-final",),
            estimated_quality=0.8,
            quality_floor=0.7,
            estimated_total_cost=0.01,
        )
        issues = validate_execution_graph(graph)
        self.assertFalse([issue for issue in issues if issue.code == "missing_function"])

    def test_prompt_omits_empty_function_sentence(self) -> None:
        text = system_prompt(node(node_id="node-1", work="work-1", final=False))
        self.assertNotIn("本节点功能：。", text)
        self.assertIn("负责原子工作：work-1", text)

    def test_final_quality_gate_uses_observable_contract_not_fixed_length(self) -> None:
        selected = node(node_id="node-final", work="work-final", final=True)
        passed, score, reasons = quality_gate(
            selected, {"choices": [{"finish_reason": "stop"}]}, "结论"
        )
        self.assertTrue(passed)
        self.assertEqual([], reasons)
        self.assertGreater(score, 0.0)
        self.assertFalse(any(reason.startswith("answer-too-short") for reason in reasons))

    def test_content_work_excludes_graph_final_nodes(self) -> None:
        content = node(node_id="node-content", work="work-content", final=False)
        final = node(node_id="node-final", work="work-final", final=True)
        graph = ExecutionGraph(
            nodes=(content, final),
            edges=(),
            execution_stages=((content.node_id,), (final.node_id,)),
            entry_nodes=(content.node_id,),
            final_nodes=(final.node_id,),
            required_work=("work-content", "work-final"),
            estimated_quality=0.8,
            quality_floor=0.7,
            estimated_total_cost=0.02,
        )
        self.assertEqual({"work-content"}, ExecutionEngine._content_work_ids(graph))


if __name__ == "__main__":
    unittest.main()
