#!/usr/bin/env python3
"""Actions-only transformer for V4 final-contract and node-evidence defects."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
TESTS = ROOT / "tests"
P0 = ROOT / "tools" / "run_v5_p0_regressions.py"


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    if source.count(old) != 1:
        raise RuntimeError(
            f"expected one marker in {path}, got {source.count(old)}: {old[:80]!r}"
        )
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def replace_top_level_function(path: Path, name: str, replacement: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    matches = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {name} in {path}, got {len(matches)}")
    node = matches[0]
    lines = source.splitlines(keepends=True)
    lines[node.lineno - 1 : node.end_lineno] = [replacement.rstrip() + "\n\n"]
    path.write_text("".join(lines), encoding="utf-8")


def patch_delivery_contract() -> None:
    path = MARKET / "v5_task_delivery_contract.py"
    replace_once(
        path,
        "def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:",
        "def _extract_explicit_markdown_contract_legacy(task: str) -> dict[str, Any]:",
    )
    addition = r'''

_CHINESE_INTEGER_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:严格|必须|务必|请)?\s*(?:依次|严格依次|按照顺序)?\s*"
    r"(?:使用|采用|按照|保留)\s*"
    r"(?P<count>\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\s*个?\s*"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2\s+headings?)"
    r"[^：:\n]{0,80}[：:]\s*(?P<headings>[^；;。\n]+)",
    re.IGNORECASE,
)
_FINAL_FORMAT_LINE_RE = re.compile(
    r"(?:"
    r"(?:严格|必须|务必|请)?\s*(?:依次|严格依次|按照顺序)?\s*"
    r"(?:使用|采用|按照|保留)[^。；;\n]{0,180}"
    r"(?:Markdown\s*)?(?:二级标题|H2|level[- ]2\s+headings?)"
    r"|(?:JSON\s*)?顶层[^。；;\n]{0,180}(?:字段|键|包含)"
    r"|top[- ]level[^.;\n]{0,180}(?:fields|keys)"
    r"|(?:严格|必须|请|use|include|provide)[^。；;\n]{0,180}"
    r"(?:Markdown\s*)?(?:表格|table)"
    r")",
    re.IGNORECASE,
)


def _chinese_integer(value: str) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "百" in text:
        left, _, right = text.partition("百")
        hundreds = _CHINESE_INTEGER_DIGITS.get(left, 1 if not left else None)
        remainder = _chinese_integer(right) if right else 0
        if hundreds is None or remainder is None:
            return None
        return 100 * hundreds + remainder
    if "十" in text:
        left, _, right = text.partition("十")
        tens = _CHINESE_INTEGER_DIGITS.get(left, 1 if not left else None)
        ones = _CHINESE_INTEGER_DIGITS.get(right, 0 if not right else None)
        if tens is None or ones is None:
            return None
        return 10 * tens + ones
    if len(text) == 1:
        return _CHINESE_INTEGER_DIGITS.get(text)
    return None


def _inline_delimited_markdown_headings(task: str) -> list[str]:
    match = _INLINE_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    expected = _chinese_integer(match.group("count"))
    if expected is None or expected < 2 or expected > 128:
        return []
    raw = match.group("headings").strip()
    for delimiter in ("、", "，", ","):
        values = [_clean_heading(value) for value in raw.split(delimiter)]
        values = [value for value in values if value]
        if len(values) != expected:
            continue
        normalized = [_normalized_heading(value) for value in values]
        if all(normalized) and len(set(normalized)) == len(normalized):
            return values
    return []


def extract_explicit_markdown_contract(task: str) -> dict[str, Any]:
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


def project_task_for_node(
    task: str,
    output_contract: Mapping[str, Any],
) -> str:
    """Remove final delivery-format clauses from internal-node task text."""
    text = str(task or "")
    if explicit_contract_kind(output_contract) != "generic":
        return text
    explicit = extract_explicit_markdown_contract(text)
    json_contract = extract_explicit_contract(text)
    table_contract = extract_explicit_table_contract(text)
    if not (explicit or json_contract or table_contract):
        return text

    heading_keys = {
        _normalized_heading(value)
        for value in explicit.get("exact_markdown_headings", [])
        if _normalized_heading(value)
    }
    rendered: list[str] = []
    skip_numbered_headings = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        format_match = _FINAL_FORMAT_LINE_RE.search(line)
        if format_match:
            prefix = line[: format_match.start()].rstrip(" ：:；;，,。")
            if prefix and not prefix.startswith("-"):
                rendered.append(prefix)
            skip_numbered_headings = bool(explicit)
            continue
        numbered = re.match(r"^\s*\d{1,3}[）).、:]\s*(.+?)\s*$", line)
        if numbered and skip_numbered_headings:
            if _normalized_heading(numbered.group(1)) in heading_keys:
                continue
        if line and _normalized_heading(line) in heading_keys:
            continue
        skip_numbered_headings = False
        rendered.append(raw_line)

    projected = "\n".join(rendered).strip()
    notice = (
        "内部节点任务投影：只处理事实、计算、证据、风险和本节点原子工作；"
        "用户指定的最终报告格式仅由最终综合节点执行。"
    )
    return f"{projected}\n\n{notice}" if projected else notice
'''
    source = path.read_text(encoding="utf-8")
    path.write_text(source.rstrip() + addition + "\n", encoding="utf-8")


def patch_constraints() -> None:
    path = MARKET / "v5_task_constraints.py"
    replace_once(
        path,
        '''    "km": "kilometer",
}''',
        '''    "km": "kilometer",
    "元": "yuan",
    "块": "yuan",
    "人民币": "yuan",
    "rmb": "yuan",
    "cny": "yuan",
    "yuan": "yuan",
    "美元": "usd",
    "美金": "usd",
    "usd": "usd",
}''',
    )


def patch_runtime() -> None:
    path = MARKET / "v5_constitutional_runtime.py"
    replace_once(
        path,
        '''        payload = cost_hardening.hardened_build_node_payload(
            node,
            original_task,
            structured,
        )''',
        '''        node_task = delivery_contract.project_task_for_node(
            original_task,
            node.output_contract,
        )
        payload = cost_hardening.hardened_build_node_payload(
            node,
            node_task,
            structured,
        )''',
    )
    replace_once(
        path,
        '''        if attempt is None or attempt.status != "passed" or not attempt.answer:
            return attempt
''',
        '''        if attempt is None or not attempt.answer:
            return attempt
''',
    )
    replace_once(
        path,
        '''        if not violations:
            return attempt

        attempt.status = "quality_gate_failed"
''',
        '''        if not violations:
            return attempt

        attempt.gate_reasons = list(
            dict.fromkeys([*attempt.gate_reasons, *violations])
        )
        if attempt.status != "passed":
            return attempt

        attempt.status = "quality_gate_failed"
''',
    )
    replace_once(
        path,
        '''        attempt.gate_reasons = list(
            dict.fromkeys([*attempt.gate_reasons, *violations])
        )
        attempt.failure = ExecutionFailure(
''',
        '''        attempt.failure = ExecutionFailure(
''',
    )
    replace_top_level_function(
        path,
        "_actual_company_audit",
        '''    @staticmethod
    def _actual_company_audit(
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        strict_successful: list[dict[str, str]] = []
        degraded: list[dict[str, str]] = []
        resolved_nodes: list[dict[str, str]] = []
        called: list[dict[str, str]] = []
        strict_statuses = {
            "success",
            "success_retried",
            "success_recovered",
        }
        for node in result.get("node_results", []):
            if not isinstance(node, Mapping):
                continue
            node_id = str(node.get("node_id") or "")
            resolved = str(
                node.get("resolved_model")
                or node.get("selected_model")
                or ""
            )
            status = str(node.get("status") or "")
            resolved_node = status.startswith("success") and bool(resolved)
            row = {
                "node_id": node_id,
                "model": resolved,
                "company": canonical_model_company(resolved),
                "status": status,
            }
            if resolved_node:
                resolved_nodes.append(row)
            if status in strict_statuses and resolved:
                strict_successful.append(row)
            elif status.startswith("success_degraded") and resolved:
                degraded.append(row)

            node_attempt_models: list[str] = []
            attempts = node.get("attempts", [])
            if not isinstance(attempts, list):
                attempts = []
            for attempt in attempts:
                if not isinstance(attempt, Mapping):
                    continue
                model = str(
                    attempt.get("response_model")
                    or attempt.get("model")
                    or ""
                )
                if not model:
                    continue
                node_attempt_models.append(model)
                called.append(
                    {
                        "node_id": node_id,
                        "attempt_kind": str(
                            attempt.get("attempt_kind") or ""
                        ),
                        "model": model,
                        "company": canonical_model_company(model),
                        "status": str(attempt.get("status") or ""),
                    }
                )

            if resolved_node and not node_attempt_models:
                called.append(
                    {
                        "node_id": node_id,
                        "attempt_kind": "resolved-model-evidence-fallback",
                        "model": resolved,
                        "company": canonical_model_company(resolved),
                        "status": status,
                    }
                )

        by_company: dict[str, set[str]] = {}
        for row in called:
            by_company.setdefault(row["company"], set()).add(row["node_id"])
        duplicates = {
            company: sorted(nodes)
            for company, nodes in by_company.items()
            if len(nodes) > 1
        }
        unresolved = [
            row
            for row in called
            if not row["company"] or row["company"] == "unknown"
        ]
        return {
            "status": "FAIL" if duplicates or unresolved else "PASS",
            "policy": "recompute-from-all-actual-called-models",
            "successful_node_models": strict_successful,
            "strict_successful_node_models": strict_successful,
            "degraded_node_models": degraded,
            "resolved_node_models": resolved_nodes,
            "all_called_models": called,
            "duplicate_called_companies_across_nodes": duplicates,
            "duplicate_successful_companies": duplicates,
            "unresolved_called_companies": unresolved,
            "same_node_retry_is_not_a_second_expert": True,
            "failed_calls_are_included": True,
            "degraded_nodes_are_not_labeled_strict_success": True,
            "resolved_model_fallback_used_only_when_attempt_evidence_missing": True,
            "cross_task_history_used": False,
        }''',
    )


def add_tests() -> None:
    path = TESTS / "test_v5_v4_contract_isolation.py"
    path.write_text(
        '''from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import SelectedNode  # noqa: E402
import task_semantic_compiler as compiler  # noqa: E402
from v5_constitutional_runtime import (  # noqa: E402
    ConstitutionalExecutionEngine,
    ConstitutionalPromptPolicy,
    validate_scope_boundaries,
)
import v5_task_delivery_contract as contracts  # noqa: E402

HEADINGS = [
    "题面事实",
    "计算与校验",
    "推断与未知",
    "结论与反转条件",
]
TASK = (
    "仅依据题面。方案A一次性投入1200元、每月300元；"
    "方案B一次性投入300元、每月450元；评估期24个月。\n"
    "执行要求：\n"
    "- 严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空\n"
    "- 不得调用外部工具，不得引入题面外精确数量。"
)


def node(contract, functions=("analysis",)):
    return SelectedNode(
        node_id="node-test",
        assigned_work=("work-test",),
        professional_capabilities={"analysis": 0.8, "synthesis": 0.8},
        functions=tuple(functions),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "high"},
        parameter_profile={"supported_parameters": ["reasoning"]},
        model="openai/test-model",
        provider_endpoint="openai/test-model@provider-a",
        output_contract=dict(contract),
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.001,
        failure_probability=0.02,
        request_config={
            "provider": {
                "order": ["provider-a"],
                "only": ["provider-a"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


class V5V4ContractIsolationTests(unittest.TestCase):
    def test_inline_chinese_count_markdown_contract_is_extracted(self):
        contract = contracts.extract_explicit_markdown_contract(TASK)
        self.assertTrue(contract["explicit_markdown_contract"])
        self.assertEqual(HEADINGS, contract["exact_markdown_headings"])

    def test_synthesis_owns_inline_final_contract_internal_node_does_not(self):
        final = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        internal = compiler._output_contract(
            TASK,
            {"analysis": 1.0, "decision_comparison": 1.0},
            False,
        )
        self.assertEqual(HEADINGS, final["required_fields"])
        self.assertTrue(final["explicit_markdown_contract"])
        self.assertFalse(internal.get("explicit_markdown_contract", False))
        self.assertNotEqual(HEADINGS, internal["required_fields"])

    def test_internal_task_projection_removes_final_headings_but_keeps_facts(self):
        internal = compiler._output_contract(TASK, {"analysis": 1.0}, False)
        projected = contracts.project_task_for_node(TASK, internal)
        self.assertIn("1200元", projected)
        self.assertIn("不得调用外部工具", projected)
        for heading in HEADINGS:
            self.assertNotIn(heading, projected)
        self.assertIn("最终报告格式仅由最终综合节点执行", projected)

    def test_prompt_policy_projects_internal_task_and_preserves_final_task(self):
        internal_contract = compiler._output_contract(
            TASK, {"analysis": 1.0}, False
        )
        final_contract = compiler._output_contract(
            TASK, {"synthesis": 1.0}, False
        )
        policy = ConstitutionalPromptPolicy()
        internal_payload = policy.build_payload(
            node(internal_contract), TASK, []
        )
        final_payload = policy.build_payload(
            node(final_contract, functions=("synthesis",)), TASK, []
        )
        internal_user = internal_payload["messages"][1]["content"]
        final_user = final_payload["messages"][1]["content"]
        for heading in HEADINGS:
            self.assertNotIn(heading, internal_user)
            self.assertIn(heading, final_user)

    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
        answer = (
            "若评估期为3个月，方案A为2100元，方案B为1650元。"
        )
        violations = validate_scope_boundaries(TASK, answer)
        self.assertIn("closed-world-unsupported-quantity:3:month", violations)
        self.assertIn("closed-world-unsupported-quantity:2100:yuan", violations)
        self.assertIn("closed-world-unsupported-quantity:1650:yuan", violations)

    def test_company_audit_separates_degraded_from_strict_success(self):
        audit = ConstitutionalExecutionEngine._actual_company_audit(
            {
                "node_results": [
                    {
                        "node_id": "n1",
                        "resolved_model": "deepseek/model-a",
                        "status": "success",
                        "attempts": [
                            {"model": "deepseek/model-a", "status": "passed"}
                        ],
                    },
                    {
                        "node_id": "n2",
                        "resolved_model": "xiaomi/model-b",
                        "status": "success_degraded",
                        "attempts": [
                            {
                                "model": "xiaomi/model-b",
                                "status": "quality_gate_failed",
                            }
                        ],
                    },
                ]
            }
        )
        self.assertEqual(["deepseek"], [
            row["company"] for row in audit["successful_node_models"]
        ])
        self.assertEqual(["xiaomi"], [
            row["company"] for row in audit["degraded_node_models"]
        ])
        self.assertTrue(audit["degraded_nodes_are_not_labeled_strict_success"])

    def test_v4_dry_run_binds_exact_contract_only_to_final_nodes(self):
        with tempfile.TemporaryDirectory(prefix="v5-v4-contract-") as directory:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "open-model-market/v5_constitutional_pipeline.py"),
                    "--task", TASK,
                    "--catalog-file", str(ROOT / "tests/fixtures/models.json"),
                    "--endpoint-file", str(ROOT / "tests/fixtures/endpoints.json"),
                    "--dry-run",
                    "--maximum-total-calls", "4",
                    "--maximum-recovery-calls", "1",
                    "--cost-anomaly-usd", "0.25",
                    "--quality-tier", "value",
                    "--output-dir", directory,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            graph = json.loads(
                (Path(directory) / "v5-execution-graph.json").read_text()
            )
        finals = set(graph["final_nodes"])
        self.assertTrue(finals)
        for row in graph["nodes"]:
            is_final = row["node_id"] in finals
            profile = row["parameter_profile"]
            if is_final:
                self.assertEqual("exact-markdown", profile["output_contract_kind"])
                self.assertEqual(HEADINGS, row["output_contract"]["required_fields"])
            else:
                self.assertEqual("generic", profile["output_contract_kind"])
                self.assertNotEqual(HEADINGS, row["output_contract"]["required_fields"])


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def patch_p0() -> None:
    replace_once(
        P0,
        '''    (TESTS / "test_v5_critical_delivery_reliability.py", "V5CriticalDeliveryReliabilityTests", 4),
)''',
        '''    (TESTS / "test_v5_critical_delivery_reliability.py", "V5CriticalDeliveryReliabilityTests", 4),
    (TESTS / "test_v5_v4_contract_isolation.py", "V5V4ContractIsolationTests", 7),
)''',
    )


def normalize() -> None:
    for path in (
        MARKET / "v5_task_delivery_contract.py",
        MARKET / "v5_task_constraints.py",
        MARKET / "v5_constitutional_runtime.py",
        TESTS / "test_v5_v4_contract_isolation.py",
        P0,
    ):
        path.write_text(path.read_text(encoding="utf-8").rstrip() + "\n", encoding="utf-8")


def main() -> int:
    patch_delivery_contract()
    patch_constraints()
    patch_runtime()
    add_tests()
    patch_p0()
    normalize()
    evidence = {
        "schema_version": "v5-v4-contract-isolation-fix-1",
        "status": "PENDING_VALIDATION",
        "v4_paid_run_id": "30710929788",
        "final_contract_inline_chinese_count_supported": True,
        "internal_task_final_format_projection_enabled": True,
        "node_evidence_violations_collected_after_base_gate_failure": True,
        "degraded_nodes_not_labeled_strict_success": True,
        "currency_quantities_closed_world_audited": True,
        "p0_expected_total": 45,
    }
    (MARKET / "v4-contract-isolation-fix-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
