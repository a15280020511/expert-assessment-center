#!/usr/bin/env python3
"""R6 formal comparison fixes: 10k output allowance and judge-label normalization."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_live_benchmark_economy as economy
import v5_live_benchmark_economy_verified as verified

OUTPUT_ALLOWANCE_TOKENS = 10000
_ORIGINAL_PREPARE = economy.prepare
_ORIGINAL_EXTRACT_JSON_OBJECT = economy.base._extract_json_object
_INSTALLED = False


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    code = _ORIGINAL_PREPARE(event_path, output_dir)
    config_path = Path(output_dir) / "benchmark-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["output_allowance_tokens"] = OUTPUT_ALLOWANCE_TOKENS
    economy._write_json(config_path, config)
    economy._write_output("output_allowance_tokens", OUTPUT_ALLOWANCE_TOKENS)
    return code


def canonical_candidate_label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).upper()
    if text.startswith("候选"):
        text = text[2:]
    match = re.fullmatch(r"C\d+", text)
    return match.group(0) if match else text


def normalized_extract_json_object(text: str) -> Mapping[str, Any]:
    parsed = dict(_ORIGINAL_EXTRACT_JSON_OBJECT(text))
    scores = parsed.get("scores")
    if isinstance(scores, Mapping):
        normalized: dict[str, Any] = {}
        for key, value in scores.items():
            label = canonical_candidate_label(key)
            if label and label not in normalized:
                normalized[label] = value
        parsed["scores"] = normalized
    ranking = parsed.get("ranking")
    if isinstance(ranking, list):
        parsed["ranking"] = [canonical_candidate_label(value) for value in ranking]
    return parsed


def install_r6_alignment() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    economy.DEFAULT_OUTPUT_ALLOWANCE = OUTPUT_ALLOWANCE_TOKENS
    verified.install_verified_alignment()
    economy.hardened.ALLOWANCE = OUTPUT_ALLOWANCE_TOKENS
    economy.base.prepare = prepare
    economy.base._extract_json_object = normalized_extract_json_object


def main(argv: Sequence[str] | None = None) -> int:
    install_r6_alignment()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
