#!/usr/bin/env python3
"""One-shot deterministic migration for the explicit V5 runtime branch."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one occurrence, got {text.count(old)}")
    return text.replace(old, new, 1)


def patch_model_market() -> None:
    path = MARKET / "model_market.py"
    text = path.read_text(encoding="utf-8")
    for line in (
        "    history_weight: float\n",
        "    history_path: Path\n",
        "        history_weight=0.0,\n",
        "        history_path=Path(\"runtime-state/unused.json\"),\n",
    ):
        if line not in text:
            raise RuntimeError(f"model_market.py missing expected history line: {line!r}")
        text = text.replace(line, "", 1)
    path.write_text(text, encoding="utf-8")


def patch_production_ticket() -> None:
    path = MARKET / "v5_production_ticket.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("import os\n", "", 1)
    text = replace_once(
        text,
        "import v5_production_hardening\n\nv5_production_hardening.install()\nimport v5_pipeline  # noqa: E402\n",
        "import v5_pipeline\nfrom v5_runtime import ProductionRuntime, RuntimeConfig\n",
        "production imports",
    )
    text = replace_once(
        text,
        'RUNTIME_VERSION = "v5-r8"',
        'RUNTIME_VERSION = "v5-native-runtime-1"',
        "runtime version",
    )
    text = replace_once(
        text,
        '"hardening": "v5_production_hardening.install",',
        '"runtime_constructor": "v5_runtime.ProductionRuntime",\n        "global_monkey_patching": False,',
        "runtime evidence",
    )
    pattern = re.compile(
        r"def _retryable_provider_failure\(output: Path\) -> bool:\n.*?\n\ndef build_parser\(\)",
        re.DOTALL,
    )
    replacement = '''def _retryable_provider_failure(output: Path) -> bool:
    rows = _load(output / "v5-node-results.json", [])
    attempts = _attempt_rows(rows)
    if not attempts:
        return False
    saw_failure = False
    for attempt in attempts:
        failure = attempt.get("failure") if isinstance(attempt.get("failure"), Mapping) else None
        if failure is None:
            if str(attempt.get("status") or "") == "passed":
                return False
            continue
        saw_failure = True
        if not bool(failure.get("retryable")):
            return False
    return saw_failure


def build_parser()'''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("failed to replace retryable provider classifier")
    text = replace_once(
        text,
        '    os.environ["TOTAL_MODEL_CALLS"] = str(args.maximum_total_calls)\n',
        "",
        "environment budget mutation",
    )
    marker = '    if not 0 <= args.maximum_recovery_calls < args.maximum_total_calls:\n        raise ValueError("maximum-recovery-calls must be non-negative and below total calls")\n'
    runtime = marker + '''    runtime = ProductionRuntime(RuntimeConfig(
        total_call_limit=args.maximum_total_calls,
        recovery_call_limit=args.maximum_recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        quality_tier=args.quality_tier,
        tools_allowed=False,
        live_catalog_required=args.require_live_catalog,
        provider_lock_required=True,
    ))
'''
    text = replace_once(text, marker, runtime, "runtime construction")
    text = replace_once(
        text,
        "        code = int(v5_pipeline.main(command))",
        "        code = int(v5_pipeline.main(command, runtime=runtime))",
        "pipeline runtime injection",
    )
    path.write_text(text, encoding="utf-8")


def patch_production_hardening() -> None:
    path = MARKET / "v5_production_hardening.py"
    path.write_text(
        '''"""Deprecated compatibility surface for the pre-runtime V5 hardening chain.

Production, dry-run and tests construct ``ProductionRuntime`` explicitly.
Calling ``install`` is intentionally a no-op and never mutates global symbols.
"""
from __future__ import annotations

from v5_cost_reliability_hardening import (
    COST_UNCERTAINTY_MULTIPLIER,
    MIN_PROVIDER_RELIABILITY,
    conservative_estimated_cost,
    hardened_build_node_payload,
    hardened_candidate_for,
    robust_extract_answer,
)
from v5_runtime import MIN_DEGRADED_WORK_COVERAGE


def install() -> None:
    """Compatibility no-op; retained temporarily for stale external imports."""
    return None
''',
        encoding="utf-8",
    )


def patch_candidate_diversity() -> None:
    path = MARKET / "v5_candidate_diversity.py"
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(r"def install\(\) -> None:\n.*\Z", re.DOTALL)
    text, count = pattern.subn(
        '''def install() -> None:
    """Deprecated compatibility no-op; use the function explicitly."""
    return None
''',
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("failed to replace candidate diversity installer")
    for line in (
        "import sys\n",
        "import v5_capability_calibration\n",
        "import v5_output_contract_delivery\n",
        "import v5_production_hardening\n",
    ):
        text = text.replace(line, "")
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_model_market()
    patch_production_ticket()
    patch_production_hardening()
    patch_candidate_diversity()
    print("explicit runtime migration applied")


if __name__ == "__main__":
    main()
