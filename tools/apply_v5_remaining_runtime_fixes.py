#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_capability_installer() -> None:
    path = MARKET / "v5_capability_calibration.py"
    text = path.read_text(encoding="utf-8")
    old = '''def install() -> None:
    """Install calibrated candidate generation into all loaded V5 call paths."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    v5_planner.generate_candidate_graph = generate_calibrated_candidate_graph
    optimizer = sys.modules.get("v5_value_optimizer")
    if optimizer is not None:
        setattr(
            optimizer,
            "generate_candidate_graph",
            generate_calibrated_candidate_graph,
        )
'''
    new = '''def install() -> None:
    """Deprecated compatibility no-op; use PlannerPolicy explicitly."""
    return None
'''
    text = once(text, old, new, "capability installer")
    text = text.replace("import sys\n", "", 1)
    path.write_text(text, encoding="utf-8")


def patch_failed_resolution() -> None:
    path = MARKET / "v5_runtime.py"
    text = path.read_text(encoding="utf-8")
    old = '''        alternatives = [self._candidate(row, selected) for row in recovery_rows]
        alternatives.sort(
'''
    new = '''        alternatives = [self._candidate(row, selected) for row in recovery_rows]
        alternatives.sort(
'''
    text = once(text, old, new, "alternatives anchor")
    old = '''        if category in self.recovery_policy.replace_categories:
            for replacement in alternatives:
                attempted = call(replacement, "replacement")
                if attempted is None:
                    continue
                if attempted.status == "passed":
'''
    new = '''        last_attempted_node = selected
        if category in self.recovery_policy.replace_categories:
            for replacement in alternatives:
                attempted = call(replacement, "replacement")
                if attempted is None:
                    continue
                last_attempted_node = replacement
                if attempted.status == "passed":
'''
    text = once(text, old, new, "replacement tracking")
    old = '''        active = alternatives[-1] if alternatives else selected
        return RuntimeNodeResult(
'''
    new = '''        active = last_attempted_node
        return RuntimeNodeResult(
'''
    text = once(text, old, new, "failed resolved model")
    path.write_text(text, encoding="utf-8")


def patch_pipeline_diagnostics() -> None:
    path = MARKET / "v5_pipeline.py"
    text = path.read_text(encoding="utf-8")
    for line in (
        '            "history_used": 0.0,\n',
        '            "speed_used": 0.0,\n',
        '            "popularity_used": 0.0,\n',
    ):
        if line not in text:
            raise RuntimeError(f"pipeline missing stale diagnostic: {line!r}")
        text = text.replace(line, "", 1)
    path.write_text(text, encoding="utf-8")


def patch_ticket_docstring() -> None:
    path = MARKET / "v5_production_ticket.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "It installs the consolidated production hardening stack before importing the V5\npipeline",
        "It constructs one explicit ProductionRuntime before invoking the V5 pipeline",
        1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_capability_installer()
    patch_failed_resolution()
    patch_pipeline_diagnostics()
    patch_ticket_docstring()
    print("remaining explicit runtime fixes applied")


if __name__ == "__main__":
    main()
