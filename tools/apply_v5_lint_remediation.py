"""One-shot deterministic remediation for the canonical V5 lint gate."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected exactly one match in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    run(
        sys.executable,
        "-m",
        "ruff",
        "format",
        "open-model-market/task_semantic_compiler.py",
        "tools/repository_audit.py",
    )
    run(sys.executable, "-m", "ruff", "check", "--fix", "--select", "F401", ".")

    replace_once(
        "tests/test_v5_production_cutover.py",
        "import v5_execution_auditor as auditor\n",
        "import v5_execution_auditor as auditor  # noqa: E402\n",
    )
    replace_once(
        "tests/test_v5_stage_d_provider_compat.py",
        "import v5_stage_d_provider_compat as compat\n",
        "import v5_stage_d_provider_compat as compat  # noqa: E402\n",
    )
    replace_once(
        "tests/test_v5_task_resource_compiler.py",
        "import json\nimport tempfile\nimport unittest\n",
        "import json\nimport sys\nimport tempfile\nimport unittest\n",
    )
    replace_once(
        "tests/test_v5_task_resource_compiler.py",
        "ROOT = Path(__file__).resolve().parents[1]\nimport sys\n",
        "ROOT = Path(__file__).resolve().parents[1]\n",
    )

    run(sys.executable, "-m", "ruff", "check", ".")
    run(sys.executable, "-m", "compileall", "-q", "open-model-market", "tests", "tools")

    (ROOT / ".github/workflows/apply-v5-lint-remediation.yml").unlink()
    Path(__file__).unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
