from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "open-model-market" / "v5_price_ranked_issue_ticket.py"


class GovernanceRepairRetryLimitTests(unittest.TestCase):
    def test_governance_wrapper_keeps_a_finite_repair_reserve(self) -> None:
        text = WRAPPER.read_text(encoding="utf-8")
        self.assertIn(
            "LEGACY_RETRY_LIMIT = legacy.MAXIMUM_RETRIES_PER_ISSUE",
            text,
        )
        self.assertIn("GOVERNANCE_REPAIR_RETRY_LIMIT = 4", text)
        self.assertIn(
            "legacy.MAXIMUM_RETRIES_PER_ISSUE = GOVERNANCE_REPAIR_RETRY_LIMIT",
            text,
        )
        self.assertNotIn("float('inf')", text)
        self.assertNotIn("MAXIMUM_RETRIES_PER_ISSUE = 999", text)


if __name__ == "__main__":
    unittest.main()
