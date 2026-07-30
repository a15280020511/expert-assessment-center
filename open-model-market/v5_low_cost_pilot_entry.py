#!/usr/bin/env python3
"""Initialize pilot environment before importing modules with allowance constants."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _config_path(arguments: list[str]) -> Path | None:
    try:
        return Path(arguments[arguments.index("--config") + 1])
    except (ValueError, IndexError):
        return None


def main() -> int:
    arguments = sys.argv[1:]
    if arguments and arguments[0] == "run":
        path = _config_path(arguments)
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            allowance = int(data.get("output_allowance_tokens", 2000))
            os.environ["V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS"] = str(max(1024, min(2500, allowance)))
    import v5_low_cost_pilot as pilot
    return pilot.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
