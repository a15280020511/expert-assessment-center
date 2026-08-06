#!/usr/bin/env python3
"""Refine migrated routing to an explicit audited multi-provider whitelist."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label} insertion point not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


materializer = ROOT / "open-model-market/v5_proposal_materializer.py"
replace_once(
    materializer,
    '''def _request_config(\n    endpoint: Mapping[str, Any],\n    effort: str,\n) -> dict[str, Any]:\n    supported = {\n        str(value).casefold()\n        for value in endpoint.get("supported_parameters", [])\n    }\n    result: dict[str, Any] = {\n        "provider": {\n            "order": [str(endpoint["provider"])],\n            "allow_fallbacks": True,\n            "require_parameters": True,\n        }\n    }\n''',
    '''def _request_config(\n    endpoint: Mapping[str, Any],\n    effort: str,\n) -> dict[str, Any]:\n    supported = {\n        str(value).casefold()\n        for value in endpoint.get("supported_parameters", [])\n    }\n    provider_order = [\n        value\n        for raw in endpoint.get("qualified_provider_order", [])\n        if (value := str(raw).strip())\n    ]\n    if not provider_order:\n        provider_order = [str(endpoint["provider"])]\n    result: dict[str, Any] = {\n        "provider": {\n            "only": provider_order,\n            "order": provider_order,\n            "allow_fallbacks": True,\n            "require_parameters": True,\n        }\n    }\n''',
    "audited provider request config",
)

replace_once(
    materializer,
    '''def _materialize_recoveries(\n    raw: Mapping[str, Any],\n    selected: SelectedNode,\n    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],\n''',
    '''def _qualified_provider_order(\n    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],\n    key: tuple[str, str],\n) -> list[str]:\n    model, primary = key\n    rows = [\n        (provider, row)\n        for (candidate_model, provider), row in endpoints.items()\n        if candidate_model == model and provider\n    ]\n    rows.sort(\n        key=lambda item: (\n            0 if item[0] == primary else 1,\n            float(item[1].get("prompt_price_per_million", 0.0) or 0.0)\n            + float(item[1].get("completion_price_per_million", 0.0) or 0.0),\n            item[0],\n        )\n    )\n    ordered = [provider for provider, _ in rows]\n    if primary not in ordered:\n        raise ProposalValidationError("primary provider is outside qualified endpoint catalog")\n    return ordered\n\n\ndef _materialize_recoveries(\n    raw: Mapping[str, Any],\n    selected: SelectedNode,\n    endpoints: Mapping[tuple[str, str], Mapping[str, Any]],\n''',
    "qualified provider order helper",
)

replace_once(
    materializer,
    '''        _, endpoint, company = _exact_endpoint(endpoints, recovery, recovery=True)\n        companies.append(company)\n        rows.append(\n            _recovery_row(\n                recovery,\n                endpoint,\n''',
    '''        key, endpoint, company = _exact_endpoint(endpoints, recovery, recovery=True)\n        endpoint_with_pool = {\n            **dict(endpoint),\n            "qualified_provider_order": _qualified_provider_order(endpoints, key),\n        }\n        companies.append(company)\n        rows.append(\n            _recovery_row(\n                recovery,\n                endpoint_with_pool,\n''',
    "recovery provider pool",
)

replace_once(
    materializer,
    '''        _, endpoint, company = _exact_endpoint(endpoints, raw)\n        node = _selected_node(\n            raw,\n            endpoint,\n''',
    '''        key, endpoint, company = _exact_endpoint(endpoints, raw)\n        endpoint_with_pool = {\n            **dict(endpoint),\n            "qualified_provider_order": _qualified_provider_order(endpoints, key),\n        }\n        node = _selected_node(\n            raw,\n            endpoint_with_pool,\n''',
    "selected provider pool",
)

config_path = ROOT / "open-model-market/config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
provider = config.setdefault("provider", {})
provider["allow_fallbacks"] = True
provider["explicit_provider_lock_required"] = True
provider["same_model_qualified_fallback_only"] = True
provider["fallback_scope"] = "audited-qualified-provider-whitelist"
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

(ROOT / "tools/refine_top50_provider_pool.py").unlink(missing_ok=True)
