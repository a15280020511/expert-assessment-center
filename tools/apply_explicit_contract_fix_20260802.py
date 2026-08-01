#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one replacement in {path}: {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_contract_extractor() -> None:
    path = ROOT / "open-model-market" / "v5_task_delivery_contract.py"
    old_regex = '''_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:严格|必须|务必|请)?\\s*(?:依次|严格依次|按照顺序)?\\s*"
    r"(?:使用|采用|按照|保留)\\s*"
    r"(?P<count>\\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\\s*个?\\s*"
    r"(?:Markdown\\s*)?(?:二级标题|H2|level[- ]2\\s+headings?)"
    r"[^：:\\n]{0,80}[：:]\\s*(?P<headings>[^；;。\\n]+)",
    re.IGNORECASE,
)
'''
    new_regex = '''_INLINE_MARKDOWN_CONTRACT_RE = re.compile(
    r"(?:最终(?:输出|报告|交付)\\s*)?"
    r"(?:必须|务必|应当|请)?\\s*(?:严格\\s*)?"
    r"(?:依次|严格依次|按照顺序|按顺序)?\\s*"
    r"(?:使用|采用|按照|保留)\\s*(?:以下|下列|following)?\\s*"
    r"(?P<count>\\d{1,3}|[零〇一二两三四五六七八九十百]{1,4})\\s*个?\\s*"
    r"(?:Markdown\\s*)?(?:二级标题|H2|level[- ]2\\s+headings?)"
    r"[^：:\\n]{0,100}[：:]\\s*(?P<headings>[^\\n]+)",
    re.IGNORECASE,
)
'''
    replace_once(path, old_regex, new_regex)

    old_helper = '''def _inline_delimited_markdown_headings(task: str) -> list[str]:
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
'''
    new_helper = '''def _valid_heading_sequence(
    values: Sequence[str],
    expected: int,
) -> list[str]:
    headings = [_clean_heading(value) for value in values]
    headings = [value for value in headings if value]
    if len(headings) != expected:
        return []
    normalized = [_normalized_heading(value) for value in headings]
    if not all(normalized) or len(set(normalized)) != len(normalized):
        return []
    return headings


def _inline_delimited_markdown_headings(task: str) -> list[str]:
    match = _INLINE_MARKDOWN_CONTRACT_RE.search(str(task or ""))
    if not match:
        return []
    expected = _chinese_integer(match.group("count"))
    if expected is None or expected < 2 or expected > 128:
        return []
    raw = match.group("headings").strip()

    numbered = [
        sequence
        for sequence in _numbered_sequences(raw)
        if len(sequence) == expected
    ]
    if numbered:
        valid = _valid_heading_sequence(numbered[0], expected)
        if valid:
            return valid

    for delimiter in ("；", ";", "、", "，", ","):
        valid = _valid_heading_sequence(raw.split(delimiter), expected)
        if valid:
            return valid
    return []
'''
    replace_once(path, old_helper, new_helper)


def patch_independent_revalidation() -> None:
    path = ROOT / "open-model-market" / "v5_independent_artifact_revalidation.py"
    old_import = '''from v5_task_delivery_contract import validate_answer_contract
'''
    new_import = '''from v5_task_delivery_contract import (
    apply_explicit_contract,
    explicit_contract_kind,
    validate_answer_contract,
)
'''
    replace_once(path, old_import, new_import)

    old_function = '''def _final_contract_violations(
    graph: Mapping[str, Any],
    report: str,
) -> list[str]:
    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list) or not final_ids:
        return ["final graph contract evidence is missing"]
    violations: list[str] = []
    matched = 0
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") not in final_ids:
            continue
        matched += 1
        contract = node.get("output_contract", {})
        parameters = node.get("parameter_profile", {})
        if not isinstance(contract, Mapping):
            violations.append("final node output contract is missing")
            continue
        node_violations = validate_answer_contract(
            report,
            contract,
            parameters if isinstance(parameters, Mapping) else {},
        )
        violations.extend(
            f"final-report-contract:{value}" for value in node_violations
        )
    if matched != len(final_ids):
        violations.append(
            f"final node definitions are incomplete: {matched}/{len(final_ids)}"
        )
    return list(dict.fromkeys(violations))
'''
    new_function = '''_EXPLICIT_CONTRACT_KEYS = (
    "explicit_user_contract",
    "exact_top_level_fields",
    "forbid_extra_top_level_fields",
    "all_required_fields_nonempty",
    "nested_exact_fields",
    "nested_values_must_be_objects",
    "explicit_markdown_contract",
    "exact_markdown_headings",
    "markdown_heading_level",
    "markdown_headings_must_be_nonempty",
    "markdown_heading_order_required",
    "explicit_table_contract",
    "exact_table_columns",
    "table_columns_must_be_nonempty",
    "table_column_order_required",
    "required_fields",
    "machine_readable_required",
)


def _explicit_contract_projection(contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: contract[key]
        for key in _EXPLICIT_CONTRACT_KEYS
        if key in contract
    }


def _recompiled_task_contract(task: str) -> dict[str, Any]:
    return apply_explicit_contract(
        task,
        {"synthesis": 1.0},
        {
            "required_fields": [],
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
    )


def _final_contract_violations(
    graph: Mapping[str, Any],
    report: str,
    task: str,
) -> list[str]:
    final_ids = {
        str(value)
        for value in graph.get("final_nodes", [])
        if str(value)
    }
    nodes = graph.get("nodes", [])
    if not isinstance(nodes, list) or not final_ids:
        return ["final graph contract evidence is missing"]

    violations: list[str] = []
    task_contract = _recompiled_task_contract(task)
    task_kind = explicit_contract_kind(task_contract)
    if task_kind != "generic":
        task_violations = validate_answer_contract(report, task_contract)
        violations.extend(
            f"task-recompiled-final-report-contract:{value}"
            for value in task_violations
        )

    matched = 0
    for node in nodes:
        if not isinstance(node, Mapping):
            continue
        if str(node.get("node_id") or "") not in final_ids:
            continue
        matched += 1
        contract = node.get("output_contract", {})
        parameters = node.get("parameter_profile", {})
        if not isinstance(contract, Mapping):
            violations.append("final node output contract is missing")
            continue

        graph_kind = explicit_contract_kind(contract)
        if task_kind != "generic":
            if graph_kind != task_kind:
                violations.append(
                    f"final-graph-contract-kind-mismatch:{task_kind}:{graph_kind}"
                )
            if _explicit_contract_projection(contract) != _explicit_contract_projection(
                task_contract
            ):
                violations.append(
                    "final-graph-contract-differs-from-task-recompilation"
                )

        node_violations = validate_answer_contract(
            report,
            contract,
            parameters if isinstance(parameters, Mapping) else {},
        )
        violations.extend(
            f"final-report-contract:{value}" for value in node_violations
        )
    if matched != len(final_ids):
        violations.append(
            f"final node definitions are incomplete: {matched}/{len(final_ids)}"
        )
    return list(dict.fromkeys(violations))
'''
    replace_once(path, old_function, new_function)

    old_call = '''    report_contract_violations = _final_contract_violations(
        graph if isinstance(graph, Mapping) else {},
        report,
    )
'''
    new_call = '''    report_contract_violations = _final_contract_violations(
        graph if isinstance(graph, Mapping) else {},
        report,
        task,
    )
'''
    replace_once(path, old_call, new_call)
    replace_once(
        path,
        '"schema_version": "v5-independent-artifact-revalidation-2",',
        '"schema_version": "v5-independent-artifact-revalidation-3",',
    )


def write_regression_tests() -> None:
    path = ROOT / "tests" / "test_v5_explicit_markdown_contract_revalidation.py"
    if path.exists():
        raise RuntimeError(f"test already exists: {path}")
    path.write_text(
        '''from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import task_semantic_compiler as compiler  # noqa: E402
import v5_independent_artifact_revalidation as independent  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402


HEADINGS = [
    "已知事实与未知",
    "风险优先级",
    "前15分钟行动",
    "通信降级",
    "门禁与人员清点",
    "资源分配",
    "失败模式与红队",
    "最终条件式建议",
]
TASK = (
    "真实闭卷实战。最终输出必须严格使用以下8个Markdown二级标题且顺序不得改变："
    "1. 已知事实与未知；2. 风险优先级；3. 前15分钟行动；4. 通信降级；"
    "5. 门禁与人员清点；6. 资源分配；7. 失败模式与红队；8. 最终条件式建议。"
)
INTERNAL_HEADINGS = [
    "agreements",
    "assumptions",
    "conclusions",
    "conflict_resolution",
    "disagreements",
    "evidence_gaps",
    "final_recommendation",
    "uncertainties",
]


def report(headings: list[str]) -> str:
    return "\\n\\n".join(
        f"## {heading}\\n\\n{index}号章节正文。"
        for index, heading in enumerate(headings, 1)
    )


def graph(contract: dict) -> dict:
    return {
        "final_nodes": ["final"],
        "nodes": [
            {
                "node_id": "final",
                "output_contract": contract,
                "parameter_profile": {},
            }
        ],
    }


class V5ExplicitMarkdownContractRevalidationTests(unittest.TestCase):
    def test_production_wording_extracts_exact_semicolon_numbered_headings(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(TASK)
        self.assertTrue(contract["explicit_markdown_contract"])
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertTrue(contract["markdown_heading_order_required"])

    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        self.assertEqual(contract["required_fields"], HEADINGS)
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertNotIn("agreements", contract["required_fields"])

    def test_internal_standard_report_is_rejected_by_recompiled_task_contract(self) -> None:
        internal_contract = {
            "required_fields": INTERNAL_HEADINGS,
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        }
        violations = independent._final_contract_violations(
            graph(internal_contract),
            report(INTERNAL_HEADINGS),
            TASK,
        )
        self.assertIn(
            "final-graph-contract-kind-mismatch:exact-markdown:generic",
            violations,
        )
        self.assertIn(
            "final-graph-contract-differs-from-task-recompilation",
            violations,
        )
        self.assertTrue(
            any(
                value.startswith(
                    "task-recompiled-final-report-contract:missing-exact-markdown-heading:"
                )
                for value in violations
            )
        )

    def test_exact_report_and_graph_contract_pass_independent_revalidation(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        self.assertEqual(
            independent._final_contract_violations(
                graph(contract),
                report(HEADINGS),
                TASK,
            ),
            [],
        )

    def test_wrong_order_is_rejected_from_original_task(self) -> None:
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, False)
        violations = independent._final_contract_violations(
            graph(contract),
            report(list(reversed(HEADINGS))),
            TASK,
        )
        self.assertIn(
            "task-recompiled-final-report-contract:exact-markdown-heading-order-mismatch",
            violations,
        )


if __name__ == "__main__":
    unittest.main()
''',
        encoding="utf-8",
    )


def main() -> None:
    patch_contract_extractor()
    patch_independent_revalidation()
    write_regression_tests()


if __name__ == "__main__":
    main()
