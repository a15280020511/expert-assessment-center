#!/usr/bin/env python3
"""Synchronize runtime/audit text with top-50 and audited provider pools."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
OLD_LOCK = "provider.only/order-exact-single-endpoint"
NEW_LOCK = "provider.only/order-audited-same-model-endpoint-pool"
SCOPE = "same-model-audited-qualified-provider-whitelist"

runtime_files = (
    "v5_pipeline.py",
    "v5_run_evidence.py",
    "v5_execution_auditor_integrity.py",
    "v5_price_ranked_evidence.py",
    "v5_governance_plan_evidence.py",
    "v5_price_ranked_execution_auditor.py",
    "v5_independent_artifact_revalidation.py",
    "v5_price_ranked_production_ticket.py",
)
for name in runtime_files:
    path = MARKET / name
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    text = text.replace(OLD_LOCK, NEW_LOCK)
    if name in {
        "v5_pipeline.py",
        "v5_run_evidence.py",
        "v5_price_ranked_evidence.py",
        "v5_governance_plan_evidence.py",
        "v5_price_ranked_execution_auditor.py",
        "v5_independent_artifact_revalidation.py",
        "v5_price_ranked_production_ticket.py",
    }:
        text = text.replace(
            '"provider_fallback_allowed": False,',
            '"provider_fallback_allowed": True,\n'
            f'            "provider_fallback_scope": "{SCOPE}",\n'
            '            "unrestricted_provider_fallback_allowed": False,',
        )
    path.write_text(text, encoding="utf-8")

for path in (ROOT / "tests").glob("test_*.py"):
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "v5-constitutional-policy-5",
        "v5-constitutional-policy-6-top50-ortools",
    )
    text = text.replace(OLD_LOCK, NEW_LOCK)
    path.write_text(text, encoding="utf-8")

(ROOT / "tools/sync_top50_runtime_contract.py").unlink(missing_ok=True)
