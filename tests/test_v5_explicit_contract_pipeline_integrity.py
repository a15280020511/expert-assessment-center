import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import task_semantic_compiler as compiler  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
import v5_planner  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


MARKDOWN_TASK = (
    "必须分别给出：1）事实；2）时间线；3）风险链；4）决策树；"
    "5）通信模板；6）门禁措施；7）升级条件；8）失败模式；"
    "9）替代方案；10）结束判定；11）整改清单；12）红队反证。"
    "每一项必须有独立Markdown二级标题，标题文字须与上述12项一致。"
)
JSON_TASK = (
    "请输出且只能输出一个合法JSON对象。JSON顶层必须严格包含且仅包含以下字段："
    "facts、options、day_by_day_plan、final_recommendation。每个字段均不得为空；"
    "options必须恰好包含continue_current、supplier_a、supplier_b、hybrid四个对象；"
    "day_by_day_plan必须覆盖day_0到day_3。"
)


def endpoint() -> dict:
    return {
        "endpoint_id": "vendor/model@provider/default",
        "model_id": "vendor/model",
        "provider_slug": "provider/default",
        "provider_endpoint": "vendor/model@provider/default",
        "context_length": 131_072,
        "max_completion_tokens": 32_768,
        "prompt_price_per_million": 0.1,
        "completion_price_per_million": 0.2,
        "supported_parameters": ["reasoning", "structured_outputs"],
        "capability_scores": {"synthesis": 0.9, "structured_output": 0.9},
        "benchmark_score": 0.8,
        "benchmark_confidence": 0.9,
        "reliability": 0.98,
    }


def work(contract: dict, work_id: str = "work-synthesis") -> dict:
    return {
        "work_id": work_id,
        "importance": 1.0,
        "operation_requirements": {"synthesis": 1.0},
        "prompt_requirements": {"structured_delivery": 1.0},
        "reasoning_requirements": {"reasoning_enabled": True, "depth": 0.8},
        "context_requirements": {
            "required_context_tokens": 8_000,
            "expected_output_tokens": 3_000,
            "expected_reasoning_tokens": 1_000,
            "system_prompt_tokens": 500,
            "original_task_tokens": 500,
            "visible_upstream_tokens": 1_000,
        },
        "output_contract": contract,
        "dependencies": [],
        "independence_requirements": {},
    }


def candidate_for(contract: dict):
    row = work(contract)
    return v5_planner._candidate_for(
        "interpretation-1",
        ["work-synthesis#0"],
        [row],
        [0],
        endpoint(),
        {"work-synthesis": {"synthesis": 1.0}},
        {"work-synthesis": set()},
        [],
    )


def selected_node(candidate) -> SelectedNode:
    return SelectedNode(
        node_id=candidate.candidate_id,
        assigned_work=candidate.assigned_work,
        professional_capabilities=candidate.professional_capabilities,
        functions=candidate.functions,
        prompt_profile=candidate.prompt_profile,
        reasoning_profile=candidate.reasoning_profile,
        parameter_profile=candidate.parameter_profile,
        model=candidate.model,
        provider_endpoint=candidate.provider_endpoint,
        output_contract=candidate.output_contract,
        estimated_quality=candidate.estimated_quality,
        quality_uncertainty=candidate.quality_uncertainty,
        estimated_cost=candidate.estimated_cost,
        failure_probability=candidate.failure_probability,
        request_config=candidate.request_config,
        independence_group=None,
    )


def graph_for(node: SelectedNode) -> ExecutionGraph:
    return ExecutionGraph(
        nodes=(node,),
        edges=(),
        execution_stages=((node.node_id,),),
        entry_nodes=(node.node_id,),
        final_nodes=(node.node_id,),
        required_work=("work-synthesis",),
        estimated_quality=node.estimated_quality,
        quality_floor=0.5,
        estimated_total_cost=node.estimated_cost,
        metadata={"recovery_pool": {}},
    )


class TestV5ExplicitContractPipelineIntegrity(unittest.TestCase):
    def test_markdown_contract_order_and_metadata_survive_candidate_and_graph(self):
        contract = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        expected = list(contract["exact_markdown_headings"])
        candidate = candidate_for(contract)
        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.output_contract["explicit_markdown_contract"])
        self.assertEqual(candidate.output_contract["required_fields"], expected)
        self.assertEqual(candidate.output_contract["exact_markdown_headings"], expected)
        self.assertTrue(candidate.output_contract["markdown_heading_order_required"])
        self.assertEqual(
            contract_policy.validate_contract_integrity(
                candidate.output_contract, candidate.parameter_profile
            ),
            [],
        )

        limits = GraphLimits(
            max_nodes=2,
            max_edges=2,
            max_stages=2,
            max_model_calls=2,
            max_retries=0,
            max_replacements=0,
            max_budget_usd=1.0,
        )
        selected_graph = v5_planner._selected_graph(
            [candidate],
            [0],
            {
                "interpretations": {
                    "interpretation-1": {
                        "work_ids": ["work-synthesis"],
                        "atomic_edges": [],
                    }
                }
            },
            "interpretation-1",
            0.5,
            candidate.estimated_quality,
            limits,
        )
        selected = selected_graph.nodes[0]
        self.assertEqual(selected.output_contract["required_fields"], expected)
        self.assertEqual(selected.output_contract["exact_markdown_headings"], expected)
        self.assertEqual(
            contract_policy.validate_contract_integrity(
                selected.output_contract, selected.parameter_profile
            ),
            [],
        )

    def test_exact_json_nested_schema_survives_candidate_pipeline(self):
        contract = compiler._output_contract(JSON_TASK, {"synthesis": 1.0}, True)
        candidate = candidate_for(contract)
        self.assertIsNotNone(candidate)
        output = candidate.output_contract
        self.assertTrue(output["explicit_user_contract"])
        self.assertEqual(output["required_fields"], output["exact_top_level_fields"])
        self.assertEqual(
            output["nested_exact_fields"]["options"],
            ["continue_current", "supplier_a", "supplier_b", "hybrid"],
        )
        self.assertEqual(
            output["nested_exact_fields"]["day_by_day_plan"],
            ["day_0", "day_1", "day_2", "day_3"],
        )
        self.assertTrue(output["forbid_extra_top_level_fields"])
        self.assertEqual(
            contract_policy.validate_contract_integrity(
                output, candidate.parameter_profile
            ),
            [],
        )

    def test_explicit_contract_overrides_generic_bundle_without_union_or_sorting(self):
        explicit = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        generic = {
            "required_fields": ["agreements", "conclusions"],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        }
        merged = v5_planner._merge_output_contract(
            [work(generic, "work-generic"), work(explicit)]
        )
        self.assertEqual(
            merged["required_fields"], explicit["exact_markdown_headings"]
        )
        self.assertNotIn("agreements", merged["required_fields"])
        self.assertTrue(merged["explicit_markdown_contract"])

    def test_conflicting_explicit_contracts_fail_closed(self):
        first = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        second = dict(first)
        second["exact_markdown_headings"] = list(reversed(first["exact_markdown_headings"]))
        second["required_fields"] = list(second["exact_markdown_headings"])
        with self.assertRaisesRegex(
            v5_planner.V5PlanningError,
            "Conflicting explicit user output contracts",
        ):
            v5_planner._merge_output_contract([work(first, "one"), work(second, "two")])

    def test_shuffled_markdown_answer_is_rejected_after_pipeline(self):
        contract = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        candidate = candidate_for(contract)
        headings = list(reversed(candidate.output_contract["exact_markdown_headings"]))
        answer = "\n\n".join(f"## {heading}\n\n正文" for heading in headings)
        passed, score, reasons = delivery.contract_aware_quality_gate(
            selected_node(candidate),
            {"choices": [{"finish_reason": "stop"}]},
            answer,
        )
        self.assertFalse(passed)
        self.assertLessEqual(score, 0.35)
        self.assertIn("exact-markdown-heading-order-mismatch", reasons)

    def test_preflight_rejects_stripped_contract_before_first_call(self):
        contract = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        candidate = candidate_for(contract)
        node = selected_node(candidate)
        stripped = replace(
            node,
            output_contract={
                "required_fields": sorted(contract["exact_markdown_headings"]),
                "machine_readable_required": False,
                "must_separate_fact_assumption_inference": True,
            },
        )
        runtime = ProductionRuntime(
            RuntimeConfig(
                total_call_limit=2,
                recovery_call_limit=0,
                cost_anomaly_usd=1.0,
                quality_tier="value",
            )
        )
        preflight = runtime.execution_engine._preflight(graph_for(stripped))
        self.assertEqual(preflight["status"], "rejected")
        self.assertIn(
            f"output-contract-integrity:{stripped.node_id}",
            preflight["blockers"],
        )
        violations = preflight["output_contract_integrity"][stripped.node_id]
        self.assertIn("explicit-output-contract-metadata-stripped", violations)
        self.assertIn("output-contract-integrity-sha256-mismatch", violations)

    def test_runtime_contract_evidence_rejects_hash_or_order_tampering(self):
        contract = compiler._output_contract(MARKDOWN_TASK, {"synthesis": 1.0}, False)
        candidate = candidate_for(contract)
        node = selected_node(candidate)
        tampered_contract = dict(node.output_contract)
        tampered_contract["required_fields"] = list(
            reversed(tampered_contract["required_fields"])
        )
        tampered = replace(node, output_contract=tampered_contract)
        answer = "\n\n".join(
            f"## {heading}\n\n正文"
            for heading in tampered_contract["required_fields"]
        )
        runtime = ProductionRuntime(
            RuntimeConfig(
                total_call_limit=2,
                recovery_call_limit=0,
                cost_anomaly_usd=1.0,
                quality_tier="value",
            )
        )
        evidence = runtime.execution_engine._contract(tampered, answer)
        self.assertFalse(evidence["required_fields_complete"])
        self.assertIn(
            "output-contract-integrity-sha256-mismatch",
            evidence["contract_violations"],
        )
        self.assertIn(
            "exact-markdown-required-heading-order-or-content-mismatch",
            evidence["contract_violations"],
        )


if __name__ == "__main__":
    unittest.main()
