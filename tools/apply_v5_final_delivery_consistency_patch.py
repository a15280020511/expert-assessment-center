#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "open-model-market/v5_task_constraints.py",
    '''def _quantity_skeleton(value: str) -> str:
    """Remove only explicit quantities while preserving semantic anchors."""
    without_quantities = _QUANTITY_RE.sub("", str(value or ""))
    return _normalize_claim(without_quantities)
''',
    '''_CARDINALITY_CONTEXT_MARKERS = (
    "只有",
    "仅有",
    "共有",
    "共计",
    "总计",
    "合计",
)
_CARDINALITY_LINK_SUFFIX_RE = re.compile(r"(?:为|是|有)$")


def _quantity_skeleton(value: str) -> str:
    """Normalize only cardinality syntax while preserving semantic anchors."""
    without_quantities = _QUANTITY_RE.sub("", str(value or ""))
    normalized = _normalize_claim(without_quantities)
    for marker in _CARDINALITY_CONTEXT_MARKERS:
        normalized = normalized.replace(marker, "")
    return _CARDINALITY_LINK_SUFFIX_RE.sub("", normalized)
''',
)

replace_once(
    "open-model-market/v5_task_delivery_contract.py",
    '''_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:最终(?:输出|报告|交付)\\s*)?"
    r"(?:必须|务必|应当|请)?\\s*(?:严格\\s*)?"
    r"(?:依次|严格依次|按照顺序|按顺序)?\\s*"
    r"(?:使用|采用|按照|保留)\\s*(?:以下|下列|following)?\\s*"
    r"(?P<count>\\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\\s*个?\\s*"
    r"(?:Markdown\\s*)?(?:二级标题|H2|level[- ]2\\s+headings?)"
    r"[^：:\\n]{0,100}[：:]\\s*(?P<headings>[^\\n]+)",
    re.IGNORECASE,
)
_FINAL_FORMAT_LINE_RE = re.compile(
''',
    '''_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:最终(?:输出|报告|交付)\\s*)?"
    r"(?:必须|务必|应当|请)?\\s*(?:严格\\s*)?"
    r"(?:依次|严格依次|按照顺序|按顺序)?\\s*"
    r"(?:使用|采用|按照|保留)\\s*(?:以下|下列|following)?\\s*"
    r"(?P<count>\\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\\s*个?\\s*"
    r"(?:Markdown\\s*)?(?:二级标题|H2|level[- ]2\\s+headings?)"
    r"[^：:\\n]{0,100}[：:]\\s*(?P<headings>[^\\n]+)",
    re.IGNORECASE,
)
_INLINE_INFERRED_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:最终(?:输出|报告|交付)\\s*)"
    r"(?=[^。\\n]{0,220}(?:必须|务必|应当))"
    r"(?=[^。\\n]{0,220}(?:且只能|只能|仅能))"
    r"[^。\\n]{0,220}?(?:Markdown\\s*)?"
    r"(?:二级标题|H2|level[- ]2\\s+headings?)"
    r"[^：:\\n]{0,120}[：:]\\s*(?P<headings>[^\\n]+)",
    re.IGNORECASE,
)
_FINAL_FORMAT_LINE_RE = re.compile(
''',
)

replace_once(
    "open-model-market/v5_task_delivery_contract.py",
    '''def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
    headings = _inline_delimited_markdown_headings(task)
    if not headings:
        return _extract_explicit_markdown_contract_legacy(task)
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": (
            "explicit-format-text-only-inline-delimited"
        ),
    }
''',
    '''def _inline_inferred_markdown_headings(task: str) -> list[str]:
    match = _INLINE_INFERRED_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    values = [
        value.strip()
        for value in re.split(r"[；;、，,]", match.group("headings"))
        if value.strip()
    ]
    if not 2 <= len(values) <= 128:
        return []
    return _valid_heading_sequence(values, len(values))


def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
    headings = _inline_delimited_markdown_headings(task)
    policy = "explicit-format-text-only-inline-delimited"
    if not headings:
        headings = _inline_inferred_markdown_headings(task)
        policy = "explicit-format-text-only-inline-inferred-count"
    if not headings:
        return _extract_explicit_markdown_contract_legacy(task)
    return {
        "explicit_markdown_contract": True,
        "exact_markdown_headings": headings,
        "markdown_heading_level": 2,
        "markdown_headings_must_be_nonempty": True,
        "markdown_heading_order_required": True,
        "task_explicit_delivery_section_count": len(headings),
        "task_explicit_long_form_required": len(headings) >= 8,
        "contract_extraction_policy": policy,
    }
''',
)

replace_once(
    "open-model-market/v5_pipeline.py",
    '''    work_counts = [
        len(
            [
                item
                for item in row.get("atomic_work", [])
                if isinstance(item, Mapping)
            ]
        )
        for row in interpretations
    ]
    signals = resources.get("task_signals", {})
''',
    '''    work_counts = [
        len(
            [
                item
                for item in row.get("atomic_work", [])
                if isinstance(item, Mapping)
            ]
        )
        for row in interpretations
    ]
    synthesis_counts = [
        sum(
            1
            for item in row.get("atomic_work", [])
            if isinstance(item, Mapping)
            and isinstance(item.get("operation_requirements"), Mapping)
            and float(
                item.get("operation_requirements", {}).get("synthesis", 0.0)
                or 0.0
            )
            > 0.0
        )
        for row in interpretations
    ]
    signals = resources.get("task_signals", {})
''',
)
replace_once(
    "open-model-market/v5_pipeline.py",
    '''        "maximum_atomic_work": max(work_counts or [1]),
        "interpretation_count": max(1, len(interpretations)),
''',
    '''        "maximum_atomic_work": max(work_counts or [1]),
        "maximum_synthesis_work": max(synthesis_counts or [0]),
        "interpretation_count": max(1, len(interpretations)),
''',
)
replace_once(
    "open-model-market/v5_pipeline.py",
    '''    coverage, min_nodes, allow, _ = _delivery_limits(
        task,
        profile or fallback_profile,
        shape,
    )
    return GraphLimits(
        max_nodes=max(
            1,
            min(planning_nodes, total_calls - recovery_calls),
        ),
''',
    '''    coverage, min_nodes, allow, _ = _delivery_limits(
        task,
        profile or fallback_profile,
        shape,
    )
    max_nodes = max(
        1,
        min(planning_nodes, total_calls - recovery_calls),
    )
    synthesis_slots = min(
        max(0, max_nodes - 1),
        1 if int(shape.get("maximum_synthesis_work", 0) or 0) > 0 else 0,
    )
    maximum_content_nodes = max(1, max_nodes - synthesis_slots)
    effective_min_nodes = min(int(min_nodes), maximum_content_nodes)
    return GraphLimits(
        max_nodes=max_nodes,
''',
)
replace_once(
    "open-model-market/v5_pipeline.py",
    '''        min_successful_content_nodes=min_nodes,
        allow_degraded_success=allow,
''',
    '''        min_successful_content_nodes=effective_min_nodes,
        allow_degraded_success=allow,
''',
)

replace_once(
    "open-model-market/v5_constitutional_pipeline.py",
    '''    max_nodes = max(1, min(planning_nodes, total_calls - recovery_calls))
    max_edges = min(64, max_nodes * max(0, max_nodes - 1) // 2)
''',
    '''    max_nodes = max(1, min(planning_nodes, total_calls - recovery_calls))
    synthesis_slots = min(
        max(0, max_nodes - 1),
        1
        if int(resource_shape.get("maximum_synthesis_work", 0) or 0) > 0
        else 0,
    )
    maximum_content_nodes = max(1, max_nodes - synthesis_slots)
    effective_min_nodes = min(int(min_nodes), maximum_content_nodes)
    max_edges = min(64, max_nodes * max(0, max_nodes - 1) // 2)
''',
)
replace_once(
    "open-model-market/v5_constitutional_pipeline.py",
    '''        min_successful_content_nodes=min_nodes,
        allow_degraded_success=allow,
''',
    '''        min_successful_content_nodes=effective_min_nodes,
        allow_degraded_success=allow,
''',
)
replace_once(
    "open-model-market/v5_constitutional_pipeline.py",
    '''    _, _, _, delivery_decision = _delivery_limits(
        run.task,
        profile,
        shape,
    )
''',
    '''    _, requested_minimum_content_nodes, _, delivery_decision = (
        _delivery_limits(
            run.task,
            profile,
            shape,
        )
    )
    reserved_synthesis_slots = min(
        max(0, int(limits.max_nodes) - 1),
        1 if int(shape.get("maximum_synthesis_work", 0) or 0) > 0 else 0,
    )
    maximum_plannable_content_nodes = max(
        1,
        int(limits.max_nodes) - reserved_synthesis_slots,
    )
    delivery_decision = {
        **dict(delivery_decision),
        "requested_minimum_successful_content_nodes": int(
            requested_minimum_content_nodes
        ),
        "reserved_synthesis_slots": reserved_synthesis_slots,
        "maximum_plannable_content_nodes": maximum_plannable_content_nodes,
        "minimum_successful_content_nodes": int(
            limits.min_successful_content_nodes
        ),
        "minimum_node_policy": (
            "task-derived-clamped-to-initial-call-content-capacity"
        ),
    }
''',
)

replace_once(
    "open-model-market/v5_runtime.py",
    '''    def _preflight(self, graph: ExecutionGraph) -> dict[str, Any]:
''',
    '''    def _preflight(
        self,
        graph: ExecutionGraph,
        limits: GraphLimits,
    ) -> dict[str, Any]:
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        if (
            self.config.cost_anomaly_usd is not None
            and risk_cost > self.config.cost_anomaly_usd + 1e-12
        ):
            blockers.append("preflight-risk-adjusted-cost-above-anomaly-limit")
        return {
''',
    '''        content_work = self._content_work_ids(graph)
        planned_content_node_ids = sorted(
            {
                node.node_id
                for node in graph.nodes
                if set(node.assigned_work).intersection(content_work)
            }
        )
        minimum_content_nodes = max(
            1,
            int(limits.min_successful_content_nodes),
        )
        if len(planned_content_node_ids) < minimum_content_nodes:
            blockers.append("planned-content-nodes-below-delivery-minimum")
        if (
            self.config.cost_anomaly_usd is not None
            and risk_cost > self.config.cost_anomaly_usd + 1e-12
        ):
            blockers.append("preflight-risk-adjusted-cost-above-anomaly-limit")
        return {
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''            "provider_counts": providers,
            "output_contract_integrity": contract_integrity,
''',
    '''            "provider_counts": providers,
            "delivery_feasibility": {
                "planned_content_node_ids": planned_content_node_ids,
                "planned_content_node_count": len(planned_content_node_ids),
                "minimum_successful_content_nodes": minimum_content_nodes,
                "status": (
                    "PASS"
                    if len(planned_content_node_ids) >= minimum_content_nodes
                    else "FAIL"
                ),
            },
            "output_contract_integrity": contract_integrity,
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        preflight = self._preflight(graph)
''',
    '''        preflight = self._preflight(graph, limits)
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''            failed = [row.node_id for row in stage_results if not row.status.startswith("success")]
''',
    '''            failed = [
                row.node_id
                for row in stage_results
                if row.status not in STRICT_SUCCESS_STATUSES
            ]
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        successful_content_nodes = len({
            result.node_id for result in best_by_work.values()
        })
        complete_nodes = (
            len(outputs) == len(graph.nodes)
            and all(row.status.startswith("success") for row in outputs.values())
        )
        minimum_coverage = max(0.0, min(1.0, float(limits.min_required_work_coverage)))
        degradation_used = False
''',
    '''        usable_content_nodes = len(
            {result.node_id for result in best_by_work.values()}
        )
        successful_content_nodes = len(
            {
                result.node_id
                for result in best_by_work.values()
                if result.status in STRICT_SUCCESS_STATUSES
            }
        )
        complete_nodes = (
            len(outputs) == len(graph.nodes)
            and all(
                row.status in STRICT_SUCCESS_STATUSES
                for row in outputs.values()
            )
        )
        minimum_coverage = max(0.0, min(1.0, float(limits.min_required_work_coverage)))
        degradation_used = any(
            row.status.startswith("success")
            and row.status not in STRICT_SUCCESS_STATUSES
            for row in outputs.values()
        )
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        if final_answer and not degradation_used and complete_nodes and not missing:
''',
    '''        if (
            final_answer
            and not degradation_used
            and complete_nodes
            and not missing
            and not delivery_blockers
        ):
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''                "successful_content_nodes": successful_content_nodes,
            },
''',
    '''                "usable_content_nodes": usable_content_nodes,
                "successful_content_nodes": successful_content_nodes,
            },
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''                "minimum_successful_content_nodes": int(limits.min_successful_content_nodes),
                "allow_degraded_success": bool(limits.allow_degraded_success),
''',
    '''                "minimum_successful_content_nodes": int(limits.min_successful_content_nodes),
                "planned_content_node_ids": list(
                    preflight["delivery_feasibility"]["planned_content_node_ids"]
                ),
                "planned_content_node_count": int(
                    preflight["delivery_feasibility"]["planned_content_node_count"]
                ),
                "allow_degraded_success": bool(limits.allow_degraded_success),
''',
)

test = ROOT / "tests/test_v5_live_delivery_consistency.py"
test.write_text(
    '''from __future__ import annotations

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
                    relation_type="information",
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
        profile = SimpleNamespace(high_stakes=True)
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
        by_id = {node.node_id: node for node in graph.nodes}
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
        self.assertEqual(summary["completion_mode"], "none")
        self.assertEqual(summary["quality_status"], "failed")
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
''',
    encoding="utf-8",
)
