#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def patch_builder() -> None:
    path = MARKET / "v5_evidence_bundle.py"
    text = path.read_text(encoding="utf-8")
    text = once(text, "import shutil\n", "", "unused shutil import")
    path.write_text(text, encoding="utf-8")


def patch_production_ticket() -> None:
    path = MARKET / "v5_production_ticket.py"
    text = path.read_text(encoding="utf-8")
    text = once(
        text,
        "import v5_pipeline\nfrom v5_runtime import ProductionRuntime, RuntimeConfig\n",
        "import v5_pipeline\nfrom v5_evidence_bundle import ApprovedRun, EvidenceBundleBuilder, EvidenceInputs\nfrom v5_runtime import ProductionRuntime, RuntimeConfig\n",
        "evidence imports",
    )
    pattern = re.compile(
        r"def _normalize_evidence\(.*?\n\ndef _normalize\(",
        re.DOTALL,
    )
    replacement = '''def _normalize_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
    require_report: bool,
) -> dict[str, Any]:
    """Normalize all production evidence from one immutable input snapshot."""
    inputs = EvidenceInputs.from_directory(output)
    builder = EvidenceBundleBuilder(
        inputs,
        ApprovedRun(
            total_calls=total_calls,
            recovery_calls=recovery_calls,
            cost_anomaly_usd=anomaly_budget,
        ),
    )
    return builder.write(output, require_report=require_report)


def _normalize('''
    text, count = pattern.subn(replacement, text, count=1)
    if count != 1:
        raise RuntimeError("failed to replace _normalize_evidence")
    text = text.replace(
        "It installs the consolidated production hardening stack before importing the V5\npipeline",
        "It constructs one explicit ProductionRuntime and one EvidenceBundleBuilder before\ninvoking the V5 pipeline",
        1,
    )
    path.write_text(text, encoding="utf-8")


def patch_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "execution-ticket.yml"
    text = path.read_text(encoding="utf-8")
    text = once(
        text,
        '''          path: |
            final-attestation.json
            final-status.md
''',
        '''          path: |
            final-attestation.json
            final-status.md
            ticket-artifacts/final-status.json
''',
        "final artifact paths",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_builder()
    patch_production_ticket()
    patch_workflow()
    print("evidence builder wiring applied")


if __name__ == "__main__":
    main()
