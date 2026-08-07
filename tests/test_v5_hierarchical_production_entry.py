from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))


class HierarchicalProductionEntryTests(unittest.TestCase):
    def test_ticket_entry_uses_hierarchical_materializer_explicitly(self) -> None:
        entry = importlib.import_module("v5_price_ranked_issue_ticket")
        hierarchical = importlib.import_module("v5_hierarchical_candidate_optimizer")
        self.assertTrue(entry.HIERARCHICAL_PRODUCTION_ENTRY_ACTIVE)
        self.assertIs(
            entry._core.materialize_candidate_pool_selection,
            hierarchical.materialize_candidate_pool_selection,
        )

    def test_batch_is_not_a_business_gate_but_online_and_auto_remain_forbidden(self) -> None:
        dynamic = importlib.import_module("v5_dynamic_pipeline")
        validator = importlib.import_module("execution_graph_validator")
        self.assertTrue(dynamic.ROUTED_BATCH_BUSINESS_GATE_DISABLED)
        self.assertNotIn(":batch", validator._FORBIDDEN_MODEL_TERMS)
        self.assertIn(":online", validator._FORBIDDEN_MODEL_TERMS)
        self.assertIn("openrouter/auto", validator._FORBIDDEN_MODEL_TERMS)
        self.assertTrue(dynamic.ONLINE_TOOL_ROUTE_REMAINS_FORBIDDEN)
        self.assertTrue(dynamic.AUTO_MODEL_IDENTITY_ROUTE_REMAINS_FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
