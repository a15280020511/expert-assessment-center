#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE_PATCHER = ROOT / "tools" / "apply_direct_governance_plan_endpoints.py"
PIPELINE = ROOT / "open-model-market" / "v5_price_ranked_pipeline.py"


def load_base():
    spec = importlib.util.spec_from_file_location("direct_plan_base_patcher", BASE_PATCHER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load base patcher")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    load_base().main()
    text = PIPELINE.read_text(encoding="utf-8")
    text = text.replace("    del args\n", "", 1)
    old = '''    if run.dry_run and run.catalog_file:
        payloads: Mapping[str, Any] = {}
        endpoint_source = "synthetic-fixture-endpoints"
        synthetic = True
    else:
'''
    new = '''    if args.endpoint_file:
        payloads: Mapping[str, Any] = _load_mapping(Path(args.endpoint_file))
        endpoint_source = f"fixture:{args.endpoint_file}"
        synthetic = False
    elif run.dry_run and run.catalog_file:
        payloads = {}
        endpoint_source = "synthetic-fixture-endpoints"
        synthetic = True
    else:
'''
    if old not in text:
        raise RuntimeError("generated endpoint source block is missing")
    PIPELINE.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
