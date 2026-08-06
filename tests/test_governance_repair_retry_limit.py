from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "open-model-market" / "v5_price_ranked_issue_ticket.py"
LEDGER = ROOT / "open-model-market" / "v5_governance_retry_state.py"


class GovernanceRepairRetryLimitTests(unittest.TestCase):
    def test_governance_wrapper_installs_finite_state_based_ledger(self) -> None:
        wrapper = WRAPPER.read_text(encoding="utf-8")
        ledger = LEDGER.read_text(encoding="utf-8")
        self.assertIn("governance_retry_state.patch(legacy)", wrapper)
        self.assertIn("BUSINESS_RETRY_LIMIT = 2", ledger)
        self.assertIn("SYSTEM_REPAIR_RETRY_LIMIT = 6", ledger)
        self.assertIn("in_flight_retry_count", ledger)
        self.assertNotIn("float('inf')", ledger)
        self.assertNotIn("MAXIMUM_RETRIES_PER_ISSUE = 999", ledger)


if __name__ == "__main__":
    unittest.main()
