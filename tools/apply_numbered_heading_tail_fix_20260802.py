#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_between(path: Path, start_marker: str, end_marker: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one replacement in {path}: {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def patch_contract() -> None:
    path = ROOT / "open-model-market" / "v5_task_delivery_contract.py"
    replacement = '''_NUMBERED_HEADING_PREFIX_RE = re.compile(
    r"^\\s*(\\d{1,3})[）).、:：]\\s*(.+?)\\s*$"
)


def _trim_heading_candidate(value: str) -> str:
    return _clean_heading(
        _HEADING_TRAILING_REQUIREMENT_RE.split(str(value), maxsplit=1)[0]
    )


def _valid_heading_sequence(
    values: Sequence[str],
    expected: int,
) -> list[str]:
    headings = [_trim_heading_candidate(value) for value in values]
    headings = [value for value in headings if value]
    if len(headings) != expected:
        return []

    sequentially_numbered: list[str] = []
    for index, heading in enumerate(headings, start=1):
        match = _NUMBERED_HEADING_PREFIX_RE.match(heading)
        if not match or int(match.group(1)) != index:
            sequentially_numbered = []
            break
        candidate = _trim_heading_candidate(match.group(2))
        if not candidate:
            sequentially_numbered = []
            break
        sequentially_numbered.append(candidate)
    if len(sequentially_numbered) == expected:
        headings = sequentially_numbered

    normalized = [_normalized_heading(value) for value in headings]
    if not all(normalized) or len(set(normalized)) != len(normalized):
        return []
    return headings

'''
    replace_between(
        path,
        "def _valid_heading_sequence(\n",
        "def _inline_delimited_markdown_headings",
        replacement,
    )


def patch_tests() -> None:
    path = ROOT / "tests" / "test_v5_explicit_markdown_contract_revalidation.py"
    replace_once(
        path,
        '''TRAILING_REQUIREMENT_TASK = (
    "严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空；不得调用外部工具。"
)
''',
        '''TRAILING_REQUIREMENT_TASK = (
    "严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空；不得调用外部工具。"
)
NUMBERED_TRAILING_REQUIREMENT_TASK = (
    TASK
    + "不得出现任何其他Markdown二级标题，每个指定标题下必须有非空正文。"
)
''',
    )
    replace_once(
        path,
        '''    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
''',
        '''    def test_numbered_list_with_trailing_requirements_strips_enumerators(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(
            NUMBERED_TRAILING_REQUIREMENT_TASK
        )
        self.assertEqual(contract["exact_markdown_headings"], HEADINGS)
        self.assertEqual(contract["task_explicit_delivery_section_count"], 8)
        synthesis = compiler._output_contract(
            NUMBERED_TRAILING_REQUIREMENT_TASK,
            {"synthesis": 1.0},
            False,
        )
        self.assertEqual(synthesis["required_fields"], HEADINGS)

    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
''',
    )


def main() -> None:
    patch_contract()
    patch_tests()


if __name__ == "__main__":
    main()
