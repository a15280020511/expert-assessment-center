#!/usr/bin/env python3
"""Repair nested-template escapes after applying the answer normalizer transformer."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSFORMER = ROOT / "tools" / ".one_time_v5_deterministic_answer_normalization.py"


def load_transformer():
    spec = importlib.util.spec_from_file_location(
        "v5_answer_normalization_transformer",
        TRANSFORMER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TRANSFORMER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def repair_generated_normalizer(module) -> None:
    path = module.MARKET / "v5_deterministic_answer_normalization.py"
    source = path.read_text(encoding="utf-8")
    replacements = (
        (
            '        body = "\n".join(block[1:]).strip()',
            '        body = "\\n".join(block[1:]).strip()',
        ),
        (
            '    normalized = "\n".join(reordered).strip() + "\n"',
            '    normalized = "\\n".join(reordered).strip() + "\\n"',
        ),
        (
            '        working = "\n".join(collapsed).strip() + ("\n" if collapsed else "")',
            '        working = "\\n".join(collapsed).strip() + ("\\n" if collapsed else "")',
        ),
    )
    for old, new in replacements:
        count = source.count(old)
        if count != 1:
            raise RuntimeError(
                f"expected one generated escape marker, got {count}: {old!r}"
            )
        source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")


def main() -> int:
    module = load_transformer()
    result = module.main()
    repair_generated_normalizer(module)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
