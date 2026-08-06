#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

model_market = ROOT / "open-model-market" / "model_market.py"
text = model_market.read_text(encoding="utf-8")
if "MAX_CATALOG_MODELS = 1000" not in text:
    if "MAX_CATALOG_MODELS = 150" not in text:
        raise RuntimeError("model market rank ceiling marker is missing")
    text = text.replace("MAX_CATALOG_MODELS = 150", "MAX_CATALOG_MODELS = 1000", 1)
    text = text.replace(
        'raise ExpertTeamError("ranking_limit must be between 1 and 150")',
        'raise ExpertTeamError("ranking_limit must be between 1 and 1000")',
        1,
    )
    model_market.write_text(text, encoding="utf-8")

test_path = ROOT / "tests" / "test_v5_planned_catalog_scope.py"
test_text = test_path.read_text(encoding="utf-8")
needle = "        self.assertEqual(args.ranking_limit, 1000)\n"
addition = (
    needle
    + "        self.assertEqual(pipeline.model_market.MAX_CATALOG_MODELS, 1000)\n"
)
if "pipeline.model_market.MAX_CATALOG_MODELS" not in test_text:
    if needle not in test_text:
        raise RuntimeError("rank ceiling regression marker is missing")
    test_path.write_text(test_text.replace(needle, addition, 1), encoding="utf-8")
