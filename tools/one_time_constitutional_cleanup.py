#!/usr/bin/env python3
"""One-time Actions-only cleanup for the constitutional V5 runtime.

This script is intentionally not committed to the final remediation branch. It
is fetched by the qualification workflow, applied to the Actions-authored branch,
and the resulting tree is committed only after the full zero-cost gate passes.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
TESTS = ROOT / "tests"
WORKFLOWS = ROOT / ".github" / "workflows"

PRODUCTION_ROOTS = {
    "v5_production_ticket",
    "v5_constitutional_pipeline",
    "v5_constitutional_runtime",
    "v5_recovery_runtime",
    "v5_execution_auditor_integrity",
    "v5_independent_artifact_revalidation",
    "artifact_manifest",
    "v5_final_status",
    "v5_final_attestation",
    "v5_issue_ticket",
    "v5_admission_lock",
}

EXPLICIT_LEGACY_MODULES = {
    "v5_executor",
    "v5_empty_output_recovery",
    "v5_total_call_cap",
    "v5_production_hardening",
    "v5_resilient_executor",
    "v5_stage_d_provider_compat",
    "v5_r8_executor",
    "v5_r8_gate_wiring",
    "v5_r8_policy",
    "v5_r8_provider_policy",
    "v5_r8_retry_policy",
}

PRIMITIVES = '''"""Native request, response and semantic quality primitives for V5."""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from execution_graph import SelectedNode

FORBIDDEN_FIELDS = {
    "tools", "tool_choice", "plugins", "web_search", "web_search_options",
    "file_search", "browser", "code_interpreter", "models",
}
PROMPT_MODULES: Mapping[str, str] = {
    "scope_control": "严格限定任务边界，不扩展到题目未提供的事实。",
    "uncertainty_calibration": "明确区分事实、假设、推断、不确定性与证据缺口。",
    "structured_delivery": "按输出契约组织结果，避免重复和空泛表述。",
    "evidence_discipline": "逐项检查论据是否由输入支持，不得假装联网或引用未提供资料。",
    "quantitative_rigor": "列出变量、计算关系、单位、边界与敏感性，不伪造数据。",
    "scenario_analysis": "给出情景、触发条件、时间范围和可观察指标。",
    "decision_comparison": "按同一组标准比较方案并说明权衡、排序与否决条件。",
    "adversarial_challenge": "主动寻找反例、失败路径、脆弱假设和不可接受风险。",
    "implementation_contract": "输出依赖、步骤、验收标准、故障条件和回滚方式。",
    "divergent_generation": "生成有差异的候选，不用同义改写充数。",
    "synthesis_discipline": "合并共识，保留分歧，按证据强度裁决，不以多数代替正确。",
}


class V5ExecutionPrimitiveError(RuntimeError):
    pass


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def system_prompt(node: SelectedNode) -> str:
    modules = list(node.prompt_profile.get("modules", []))
    rules = "".join(
        PROMPT_MODULES.get(str(name), f"执行提示模块：{name}。")
        for name in modules
    )
    contract = json.dumps(dict(node.output_contract), ensure_ascii=False, sort_keys=True)
    functions = "、".join(node.functions)
    return (
        "你是V5动态专家执行图中的一个严格隔离节点。"
        f"本节点功能：{functions}。负责原子工作：{', '.join(node.assigned_work)}。"
        "禁止调用、请求或假装使用网页、搜索、插件、文件、代码执行、数据库、API、浏览器、工具或其他模型。"
        "只能依据原始任务和系统显式传入的上游节点结果。不得读取未声明节点，不得与同独立组节点交换结果。"
        f"{rules}输出契约：{contract}。输出完整可交付正文；不要展示隐藏思维过程。"
    )


def build_node_payload(
    node: SelectedNode,
    original_task: str,
    upstream: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    upstream_text = "\\n\\n".join(
        f"### 上游节点 {row.get('node_id')}\\n{row.get('answer')}"
        for row in upstream
        if row.get("answer")
    ) or "[无上游结果；请独立处理。]"
    payload: dict[str, Any] = {
        "model": node.model,
        "messages": [
            {"role": "system", "content": system_prompt(node)},
            {
                "role": "user",
                "content": (
                    f"原始任务：\\n{original_task}\\n\\n"
                    f"本节点工作ID：{', '.join(node.assigned_work)}\\n\\n"
                    f"允许读取的上游结果：\\n{upstream_text}"
                ),
            },
        ],
        "stream": False,
    }
    payload.update(_json_copy(dict(node.request_config)))
    forbidden = sorted(FORBIDDEN_FIELDS.intersection(payload))
    if forbidden:
        raise V5ExecutionPrimitiveError(
            f"Forbidden request fields for node {node.node_id}: {forbidden}"
        )
    model = str(payload.get("model") or "").casefold()
    if model.startswith("openrouter/") or ":online" in model or ":batch" in model:
        raise V5ExecutionPrimitiveError(
            f"Forbidden routed model for node {node.node_id}: {payload.get('model')}"
        )
    if "max_tokens" in payload or "max_completion_tokens" in payload:
        raise V5ExecutionPrimitiveError(
            "Artificial output token ceilings are forbidden in the base payload."
        )
    return payload


def extract_answer(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0] if isinstance(choices[0], Mapping) else {}
    message = first.get("message") if isinstance(first.get("message"), Mapping) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\\n".join(
            str(row["text"])
            for row in content
            if isinstance(row, Mapping) and isinstance(row.get("text"), str)
        ).strip()
    return ""


def finish_reason(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return str(choices[0].get("finish_reason") or "")
    return ""


def actual_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return max(0.0, float(usage[key]))
        except (TypeError, ValueError):
            continue
    return 0.0


def quality_gate(
    node: SelectedNode,
    response: Mapping[str, Any],
    answer: str,
) -> tuple[bool, float, list[str]]:
    reasons: list[str] = []
    finish = finish_reason(response).casefold()
    if finish in {"length", "max_tokens"}:
        reasons.append("truncated-output")
    required_fields = [str(value) for value in node.output_contract.get("required_fields", [])]
    function_pressure = len({str(value) for value in node.functions})
    minimum_chars = max(96, 72 * max(1, len(required_fields)), 48 * function_pressure)
    if "synthesis" in node.functions:
        minimum_chars = max(minimum_chars, 256)
    if len(answer) < minimum_chars:
        reasons.append(f"answer-too-short<{minimum_chars}")
    folded = answer.casefold()
    if any(
        term in folded
        for term in (
            "i cannot access",
            "无法访问互联网",
            "作为ai无法",
            "没有提供任何答案",
        )
    ):
        reasons.append("non-delivery-or-tool-dependency")
    field_hits = sum(
        field.replace("_", " ").casefold() in folded or field.casefold() in folded
        for field in required_fields
    )
    if node.output_contract.get("machine_readable_required"):
        try:
            parsed = json.loads(answer)
            if not isinstance(parsed, Mapping):
                reasons.append("machine-readable-output-not-object")
        except json.JSONDecodeError:
            reasons.append("invalid-required-json")
    components = [
        min(1.0, len(answer) / max(minimum_chars * 3, 1)),
        0.0 if finish in {"length", "max_tokens"} else 1.0,
    ]
    if required_fields:
        components.append(field_hits / len(required_fields))
    score = sum(components) / len(components)
    confidence = max(0.0, min(1.0, float(node.estimated_quality)))
    uncertainty = max(0.0, min(1.0, float(node.quality_uncertainty)))
    threshold = max(0.45, min(0.85, (confidence + (1.0 - uncertainty)) / 2.0))
    if score + 1e-12 < threshold:
        reasons.append(f"quality-score<{threshold:.3f}")
    return not reasons, round(score, 6), reasons
'''

FULL_LOAD_TEST = '''from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import ExecutionGraph  # noqa: E402
from v5_runtime import (  # noqa: E402
    BudgetController,
    FailureCategory,
    RuntimeConfig,
)

PUBLIC_INVESTMENT_TASK = (
    "比较三个城市公共投资方案，完成财务建模、政策与法律合规、证据核验、"
    "预测推演、独立红队反证和最终决策。"
)
CLOSED_WORLD_TASK = (
    "仅依据题面，不得调用外部工具，不联网，不得编造。"
    "比较方案A与方案B，只接受完整交付。"
)
SUPPLY_CHAIN_TASK = (
    "比较四种供应链方案，考虑现金流、交付中断、质量、合同、最坏情景、"
    "红队反证和逐日切换计划，最终给出有条件选择规则。"
)


def _empty_graph() -> ExecutionGraph:
    return ExecutionGraph(
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


def _budget(*, cost: float | None, failures: int = 3) -> BudgetController:
    config = RuntimeConfig(
        total_call_limit=16,
        recovery_call_limit=2,
        cost_anomaly_usd=cost,
        quality_tier="value",
        max_provider_failures=failures,
    )
    return BudgetController(config, _empty_graph())


class TestV5FullLoadStability(unittest.TestCase):
    def test_concurrent_reservations_never_exceed_total_or_recovery_caps(self) -> None:
        budget = _budget(cost=None)
        attempts = (
            [("initial", index) for index in range(960)]
            + [("retry", index) for index in range(32)]
            + [("replacement", index) for index in range(32)]
        )

        def reserve(value: tuple[str, int]) -> tuple[str, bool, str]:
            kind, index = value
            accepted, reason = budget.reserve(kind, 0.0, f"node-{index % 16}")
            return kind, accepted, reason

        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(pool.map(reserve, attempts))
        accepted = [item for item in results if item[1]]
        snapshot = budget.snapshot()
        self.assertEqual(16, len(accepted))
        self.assertEqual(14, snapshot["initial_calls_reserved"])
        self.assertEqual(2, snapshot["recovery_calls_reserved"])
        self.assertEqual(16, snapshot["calls_reserved"])
        self.assertTrue(snapshot["denials"])

    def test_concurrent_cost_reservations_fail_closed(self) -> None:
        budget = _budget(cost=0.01)
        with ThreadPoolExecutor(max_workers=64) as pool:
            results = list(
                pool.map(
                    lambda index: budget.reserve("initial", 0.001, f"node-{index}"),
                    range(1000),
                )
            )
        snapshot = budget.snapshot()
        accepted = sum(1 for ok, _ in results if ok)
        self.assertLessEqual(accepted, 8)
        self.assertLessEqual(snapshot["estimated_cost_reserved_usd"], 0.01)
        self.assertTrue(snapshot["denials"])

    def test_concurrent_reconciliation_has_no_lost_updates(self) -> None:
        budget = _budget(cost=None)
        reservations = [budget.reserve("initial", 0.0001, f"node-{i}") for i in range(14)]
        self.assertTrue(all(ok for ok, _ in reservations))
        with ThreadPoolExecutor(max_workers=14) as pool:
            reconciliations = list(pool.map(budget.reconcile, [0.0001] * 14))
        snapshot = budget.snapshot()
        self.assertFalse(any(reconciliations))
        self.assertEqual(0.0, snapshot["estimated_cost_reserved_usd"])
        self.assertEqual(0.0014, snapshot["actual_cost_usd"])

    def test_provider_circuit_updates_are_atomic(self) -> None:
        budget = _budget(cost=None, failures=3)
        with ThreadPoolExecutor(max_workers=64) as pool:
            list(
                pool.map(
                    lambda _: budget.fail_endpoint(
                        "provider-a", FailureCategory.PROVIDER_TIMEOUT
                    ),
                    range(512),
                )
            )
        snapshot = budget.snapshot()["provider_circuit"]
        self.assertFalse(budget.endpoint_available("provider-a"))
        self.assertEqual(512, snapshot["failures"]["provider-a"])
        self.assertEqual(512, len(snapshot["reasons"]["provider-a"]))

    def test_parallel_constitutional_dry_runs_are_isolated(self) -> None:
        scenarios = (
            ("public", PUBLIC_INVESTMENT_TASK, 16, 2),
            ("closed", CLOSED_WORLD_TASK, 8, 1),
            ("supply", SUPPLY_CHAIN_TASK, 8, 1),
        )
        cases = [(*row, iteration) for row in scenarios for iteration in range(4)]
        env = os.environ.copy()
        env.pop("OPENROUTER_API_KEY", None)
        with tempfile.TemporaryDirectory(prefix="v5-native-load-") as directory:
            root = Path(directory)

            def run_case(case: tuple[str, str, int, int, int]):
                name, task, total, recovery, iteration = case
                output = root / f"{name}-{iteration}"
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / "open-model-market" / "v5_constitutional_pipeline.py"),
                        "--task", task,
                        "--catalog-file", str(ROOT / "tests/fixtures/models.json"),
                        "--endpoint-file", str(ROOT / "tests/fixtures/endpoints.json"),
                        "--dry-run",
                        "--maximum-total-calls", str(total),
                        "--maximum-recovery-calls", str(recovery),
                        "--quality-tier", "value",
                        "--output-dir", str(output),
                    ],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                if completed.returncode:
                    return name, completed.returncode, completed.stdout + completed.stderr
                dry = json.loads((output / "v5-dry-run.json").read_text())
                graph = json.loads((output / "v5-execution-graph.json").read_text())
                signature = json.dumps(graph, sort_keys=True, ensure_ascii=False)
                self.assertEqual("planned-not-executed", dry["status"])
                self.assertTrue(dry["production_entrypoint_changed"])
                self.assertFalse(dry["global_monkey_patching"])
                return name, 0, signature

            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(run_case, cases))
        failures = [row for row in results if row[1]]
        self.assertFalse(failures, failures[0][2] if failures else "")
        for name, *_ in scenarios:
            signatures = {row[2] for row in results if row[0] == name}
            self.assertEqual(1, len(signatures), name)


if __name__ == "__main__":
    unittest.main()
'''


def module_map() -> dict[str, Path]:
    return {
        path.stem: path
        for path in MARKET.glob("*.py")
        if path.name != "__init__.py"
    }


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def delete_top_level_functions(path: Path, names: set[str]) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    if not nodes:
        return []
    lines = source.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda value: value.lineno, reverse=True):
        del lines[node.lineno - 1 : node.end_lineno]
    path.write_text("".join(lines), encoding="utf-8")
    return sorted(node.name for node in nodes)


def replace_install_statements(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "install"
    ]
    if not nodes:
        return 0
    lines = source.splitlines(keepends=True)
    for node in sorted(nodes, key=lambda value: value.lineno, reverse=True):
        original = lines[node.lineno - 1]
        indent = original[: len(original) - len(original.lstrip())]
        lines[node.lineno - 1 : node.end_lineno] = [
            indent + "pass  # legacy install hook removed\n"
        ]
    path.write_text("".join(lines), encoding="utf-8")
    return len(nodes)


def dependencies(modules: dict[str, Path]) -> dict[str, set[str]]:
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in modules:
                        graph[name].add(root)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0]
                if root in modules:
                    graph[name].add(root)
    return graph


def reachable(graph: dict[str, set[str]]) -> set[str]:
    values = set(PRODUCTION_ROOTS)
    pending = list(PRODUCTION_ROOTS)
    while pending:
        current = pending.pop()
        for target in graph.get(current, set()):
            if target not in values:
                values.add(target)
                pending.append(target)
    return values


def replace_text(path: Path, old: str, new: str, *, required: bool = True) -> None:
    source = path.read_text(encoding="utf-8")
    if old not in source:
        if required:
            raise RuntimeError(f"required text missing in {path}: {old!r}")
        return
    path.write_text(source.replace(old, new), encoding="utf-8")


def rewrite_p0_runner() -> None:
    path = ROOT / "tools" / "run_v5_p0_regressions.py"
    source = path.read_text(encoding="utf-8")
    old = '''    (TESTS / "test_v5_quality_status_integrity.py", "V5QualityStatusIntegrityTests", 6),
    (TESTS / "test_v5_empty_output_recovery.py", "V5EmptyOutputRecoveryTests", 6),
'''
    new = '''    (TESTS / "test_v5_quality_status_integrity.py", "V5QualityStatusIntegrityTests", 6),
    (TESTS / "test_v5_task_constraints.py", "TaskConstraintPolarityTests", 3),
    (TESTS / "test_v5_task_constraints.py", "ClosedWorldEvidenceTests", 5),
    (TESTS / "test_v5_task_constraints.py", "DynamicObjectiveTests", 1),
    (TESTS / "test_v5_task_constraints.py", "ActualCompanyAuditTests", 2),
'''
    if old not in source:
        raise RuntimeError("P0 suite replacement marker missing")
    path.write_text(source.replace(old, new), encoding="utf-8")


def update_workflows(deleted_modules: set[str], deleted_tests: set[str]) -> None:
    v5_validate = WORKFLOWS / "v5-validate.yml"
    replace_text(
        v5_validate,
        "            open-model-market/v5_total_call_cap.py\n",
        "",
        required=False,
    )
    replace_text(
        v5_validate,
        "            tests.test_v5_r8_gate_wiring \\\n",
        "            tests.test_v5_full_load_stability \\\n",
        required=False,
    )
    validate = WORKFLOWS / "validate.yml"
    replace_text(
        validate,
        "          test -s open-model-market/v5_empty_output_recovery.py\n",
        "",
        required=False,
    )
    replace_text(
        validate,
        "          test -s open-model-market/v5_total_call_cap.py\n",
        "",
        required=False,
    )
    replace_text(
        validate,
        "          test -s tests/test_v5_empty_output_recovery.py\n",
        "          test -s tests/test_v5_full_load_stability.py\n",
        required=False,
    )
    for path in WORKFLOWS.glob("*.yml"):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        filtered = []
        for line in lines:
            if any(name in line for name in deleted_modules):
                continue
            if any(name in line for name in deleted_tests):
                continue
            filtered.append(line)
        path.write_text("".join(filtered), encoding="utf-8")


def main() -> int:
    report: dict[str, object] = {
        "schema_version": "v5-legacy-cleanup-report-3",
        "status": "FAIL",
    }
    (MARKET / "v5_execution_primitives.py").write_text(PRIMITIVES, encoding="utf-8")

    replace_text(
        MARKET / "v5_cost_reliability_hardening.py",
        "import v5_executor as executor",
        "import v5_execution_primitives as executor",
    )
    replace_text(
        MARKET / "v5_cost_reliability_hardening.py",
        "executor._extract_answer",
        "executor.extract_answer",
    )
    replace_text(
        MARKET / "v5_output_contract_delivery.py",
        "import v5_executor",
        "import v5_execution_primitives as primitives",
    )
    replace_text(
        MARKET / "v5_output_contract_delivery.py",
        "v5_executor.PROMPT_MODULES",
        "primitives.PROMPT_MODULES",
    )
    replace_text(
        MARKET / "v5_output_contract_delivery.py",
        "v5_executor._system_prompt",
        "primitives.system_prompt",
    )
    replace_text(
        MARKET / "v5_output_contract_delivery.py",
        "v5_executor.quality_gate",
        "primitives.quality_gate",
    )
    replace_text(
        MARKET / "v5_dynamic_prompt_delivery.py",
        "import v5_executor as executor\n",
        "",
    )
    quality = MARKET / "v5_quality_status_integrity.py"
    replace_text(quality, "import sys\n", "", required=False)
    replace_text(quality, "import v5_executor as executor\n", "", required=False)

    removed_functions: dict[str, list[str]] = {}
    module_specific = {
        "v5_quality_status_integrity": {
            "integrity_execute_v5_graph",
            "_patch_loaded_callers",
            "install",
        },
        "v5_cost_reliability_hardening": {"install"},
        "v5_output_contract_delivery": {"install"},
        "v5_dynamic_prompt_delivery": {"install"},
    }
    for name, functions in module_specific.items():
        removed = delete_top_level_functions(MARKET / f"{name}.py", functions)
        if removed:
            removed_functions[name] = removed

    for path in MARKET.glob("*.py"):
        removed = delete_top_level_functions(path, {"install"})
        if removed:
            removed_functions.setdefault(path.stem, []).extend(removed)
        replace_install_statements(path)
    test_install_replacements = 0
    for path in TESTS.glob("test*.py"):
        test_install_replacements += replace_install_statements(path)

    for path in (
        MARKET / "v5_cost_reliability_hardening.py",
        MARKET / "v5_output_contract_delivery.py",
        MARKET / "v5_dynamic_prompt_delivery.py",
        MARKET / "v5_quality_status_integrity.py",
    ):
        source = path.read_text(encoding="utf-8")
        source = re.sub(r"^_INSTALLED\s*=.*\n", "", source, flags=re.MULTILINE)
        source = re.sub(r"^_ORIGINAL_EXECUTE:.*\n", "", source, flags=re.MULTILINE)
        path.write_text(source, encoding="utf-8")

    pipeline = MARKET / "v5_pipeline.py"
    replace_text(
        pipeline,
        'if __name__ == "__main__":\n    raise SystemExit(main())\n',
        'if __name__ == "__main__":\n    from v5_constitutional_pipeline import main as constitutional_main\n\n    raise SystemExit(constitutional_main())\n',
    )

    (TESTS / "test_v5_full_load_stability.py").write_text(
        FULL_LOAD_TEST,
        encoding="utf-8",
    )
    rewrite_p0_runner()

    modules = module_map()
    graph = dependencies(modules)
    production_before_delete = reachable(graph)
    prohibited_reachable = sorted(production_before_delete.intersection(EXPLICIT_LEGACY_MODULES))
    if prohibited_reachable:
        raise RuntimeError(
            "legacy modules remain production-reachable before deletion: "
            + ",".join(prohibited_reachable)
        )

    deleted_modules = set(EXPLICIT_LEGACY_MODULES)
    for name in sorted(deleted_modules):
        path = MARKET / f"{name}.py"
        if path.exists():
            path.unlink()

    changed = True
    while changed:
        changed = False
        modules = module_map()
        graph = dependencies(modules)
        prod = reachable(graph)
        for name, deps in sorted(graph.items()):
            missing = {value for value in deps if not (MARKET / f"{value}.py").exists()}
            legacy_refs = deps.intersection(deleted_modules)
            if not missing and not legacy_refs:
                continue
            if name in prod:
                raise RuntimeError(
                    f"production module {name} depends on removed modules: "
                    f"{sorted(missing | legacy_refs)}"
                )
            (MARKET / f"{name}.py").unlink()
            deleted_modules.add(name)
            changed = True
            break

    deleted_tests: set[str] = set()
    for path in TESTS.glob("test*.py"):
        text = path.read_text(encoding="utf-8")
        if any(
            re.search(rf"\b(?:import|from)\s+{re.escape(name)}\b", text)
            for name in deleted_modules
        ):
            path.unlink()
            deleted_tests.add(path.stem)

    update_workflows(deleted_modules, deleted_tests)

    modules = module_map()
    graph = dependencies(modules)
    prod = reachable(graph)
    install_defs: dict[str, list[int]] = {}
    install_calls: dict[str, list[int]] = {}
    legacy_imports: dict[str, list[str]] = {}
    for name, path in modules.items():
        tree = parse(path)
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "install"
                and node.col_offset == 0
            ):
                install_defs.setdefault(name, []).append(node.lineno)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "install"
            ):
                install_calls.setdefault(name, []).append(node.lineno)
        text = path.read_text(encoding="utf-8")
        hits = sorted(
            legacy
            for legacy in deleted_modules
            if re.search(rf"\b(?:import|from)\s+{re.escape(legacy)}\b", text)
        )
        if hits:
            legacy_imports[name] = hits

    test_install_calls: dict[str, list[int]] = {}
    for path in TESTS.glob("test*.py"):
        for node in ast.walk(parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "install"
            ):
                test_install_calls.setdefault(str(path.relative_to(ROOT)), []).append(node.lineno)

    failures = []
    if install_defs:
        failures.append("install definitions remain")
    if install_calls:
        failures.append("install calls remain")
    if test_install_calls:
        failures.append("test install calls remain")
    if legacy_imports:
        failures.append("imports of deleted legacy modules remain")
    if "v5_executor" in prod:
        failures.append("v5_executor remains production reachable")
    if not (MARKET / "v5_execution_primitives.py").is_file():
        failures.append("native execution primitives missing")
    pipeline_text = pipeline.read_text(encoding="utf-8")
    if "constitutional_main()" not in pipeline_text:
        failures.append("legacy CLI delegation missing")

    report.update(
        {
            "status": "PASS" if not failures else "FAIL",
            "production_reachable_modules": sorted(prod),
            "production_reachable_module_count": len(prod),
            "removed_install_functions": removed_functions,
            "test_install_statements_replaced": test_install_replacements,
            "deleted_modules": sorted(deleted_modules),
            "deleted_tests": sorted(deleted_tests),
            "install_definitions_remaining": install_defs,
            "install_calls_remaining": install_calls,
            "test_install_calls_remaining": test_install_calls,
            "legacy_imports_remaining": legacy_imports,
            "v5_executor_production_reachable": "v5_executor" in prod,
            "legacy_cli_delegates_to_constitutional_pipeline": (
                "constitutional_main()" in pipeline_text
            ),
            "global_monkey_patching_allowed": False,
            "cross_task_history_allowed": False,
            "failures": failures,
        }
    )
    (MARKET / "legacy-cleanup-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if failures:
        raise RuntimeError("; ".join(failures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
