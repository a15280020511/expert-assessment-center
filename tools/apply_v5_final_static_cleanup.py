#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    builder = ROOT / "open-model-market" / "v5_evidence_bundle.py"
    text = builder.read_text(encoding="utf-8")
    text = once(text, "import shutil\n", "", "unused shutil import")
    builder.write_text(text, encoding="utf-8")

    workflow = ROOT / ".github" / "workflows" / "v5-validate.yml"
    text = workflow.read_text(encoding="utf-8")
    text = once(
        text,
        "            open-model-market/v5_runtime.py \\\n            open-model-market/v5_executor.py \\\n",
        "            open-model-market/v5_runtime.py \\\n            open-model-market/v5_evidence_bundle.py \\\n            open-model-market/v5_executor.py \\\n",
        "evidence builder compile entry",
    )
    workflow.write_text(text, encoding="utf-8")
    print("final static cleanup applied")


if __name__ == "__main__":
    main()
