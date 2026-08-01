#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match in {path}, got {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    contract = ROOT / "open-model-market" / "v5_task_delivery_contract.py"
    replace_once(
        contract,
        '''def _valid_heading_sequence(
    values: Sequence[str],
    expected: int,
) -> list[str]:
    headings = [_clean_heading(value) for value in values]
''',
        '''_HEADING_TRAILING_REQUIREMENT_RE = re.compile(
    r"[；;。]\\s*(?=(?:每|各|不得|禁止|必须|务必|应当|内容|章节|执行|输出|"
    r"each|all|must|do\\s+not|section))",
    re.IGNORECASE,
)


def _valid_heading_sequence(
    values: Sequence[str],
    expected: int,
) -> list[str]:
    headings = [
        _clean_heading(
            _HEADING_TRAILING_REQUIREMENT_RE.split(str(value), maxsplit=1)[0]
        )
        for value in values
    ]
''',
    )

    independent = ROOT / "open-model-market" / "v5_independent_artifact_revalidation.py"
    replace_once(
        independent,
        '''def _final_contract_violations(
    graph: Mapping[str, Any],
    report: str,
    task: str,
) -> list[str]:
''',
        '''def _final_contract_violations(
    graph: Mapping[str, Any],
    report: str,
    task: str = "",
) -> list[str]:
''',
    )

    tests = ROOT / "tests" / "test_v5_explicit_markdown_contract_revalidation.py"
    replace_once(
        tests,
        '''INTERNAL_HEADINGS = [
''',
        '''TRAILING_REQUIREMENT_TASK = (
    "严格依次使用四个Markdown二级标题：题面事实、计算与校验、"
    "推断与未知、结论与反转条件；每节不得为空；不得调用外部工具。"
)
TRAILING_REQUIREMENT_HEADINGS = [
    "题面事实",
    "计算与校验",
    "推断与未知",
    "结论与反转条件",
]
INTERNAL_HEADINGS = [
''',
    )
    replace_once(
        tests,
        '''    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
''',
        '''    def test_trailing_requirements_are_not_absorbed_into_last_heading(self) -> None:
        contract = contract_policy.extract_explicit_markdown_contract(
            TRAILING_REQUIREMENT_TASK
        )
        self.assertEqual(
            contract["exact_markdown_headings"],
            TRAILING_REQUIREMENT_HEADINGS,
        )

    def test_synthesis_contract_replaces_internal_generic_headings(self) -> None:
''',
    )


if __name__ == "__main__":
    main()
