#!/usr/bin/env python3
"""One-shot PR #227 R6 request-audit complexity refactor."""
from __future__ import annotations

from pathlib import Path


PATH = Path("open-model-market/v5_pipeline.py")


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{PATH}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "def _merge_request_audit(\n",
        '''_FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "tools",
        "tool_choice",
        "plugins",
        "web_search",
        "web_search_options",
        "file_search",
        "browser",
        "code_interpreter",
        "models",
    }
)


def _forbidden_request_fields(row: Mapping[str, Any]) -> set[str]:
    direct = set(_FORBIDDEN_REQUEST_FIELDS.intersection(row))
    recorded = row.get("request_fields")
    if isinstance(recorded, list):
        direct.update(
            _FORBIDDEN_REQUEST_FIELDS.intersection(str(value) for value in recorded)
        )
    return direct


def _merge_request_audit(
''',
    )
    text = replace_once(
        text,
        '''    forbidden = {
        "tools",
        "tool_choice",
        "plugins",
        "web_search",
        "web_search_options",
        "file_search",
        "browser",
        "code_interpreter",
        "models",
    }

    def forbidden_fields(row: Mapping[str, Any]) -> set[str]:
        direct = forbidden.intersection(row)
        recorded = row.get("request_fields")
        if isinstance(recorded, list):
            direct.update(
                forbidden.intersection(str(value) for value in recorded)
            )
        return direct

''',
        "",
    )
    text = text.replace("forbidden_fields(row)", "_forbidden_request_fields(row)")
    if "forbidden_fields(row)" in text:
        raise SystemExit(f"{PATH}: nested forbidden-fields call remains")
    PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
