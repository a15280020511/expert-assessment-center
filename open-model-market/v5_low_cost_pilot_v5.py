"""Task-derived domain calibration layer for the bounded V5 low-cost pilot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import v5_low_cost_pilot_v4
import v5_task_domain_proxy


def _annotate(output_dir: str | Path) -> None:
    root = Path(output_dir)
    result_path = root / "v5-low-cost-pilot-result.json"
    if not result_path.exists():
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["domain_capability_policy"] = {
        "method": "task-matrix-domain-functional-cooccurrence",
        "applies_to": "domain capability measurements only",
        "functional_capability_scores_changed": False,
        "hard_requirement_thresholds_changed": False,
        "work_requirements_changed": False,
        "independence_constraints_changed": False,
        "model_calls_for_calibration": 0,
        "production_cutover_allowed": False,
    }
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary_path = root / "v5-low-cost-pilot-summary.md"
    if summary_path.exists():
        lines = [
            "",
            "## Task-derived domain capability calibration",
            "",
            "- Method: `task-matrix-domain-functional-cooccurrence`",
            "- Domain hard thresholds changed: `false`",
            "- Functional capability scores changed: `false`",
            "- Work requirements changed: `false`",
            "- Independence constraints changed: `false`",
            "- Calibration model calls: `0`",
            "- Production cutover allowed: `false`",
        ]
        summary_path.write_text(
            summary_path.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    v5_task_domain_proxy.install()
    code = v5_low_cost_pilot_v4.run(config_path, suite_path, output_dir)
    _annotate(output_dir)
    return code
