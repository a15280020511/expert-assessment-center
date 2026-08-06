from __future__ import annotations

import unittest

from tests.test_top20_pool_selector import _load_selector, _packet


class Top50FullStandbyInventoryTests(unittest.TestCase):
    def test_all_fifty_models_are_retained_with_explicit_state(self) -> None:
        module = _load_selector()
        packet, receipt = module.materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        inventory = plan["expert_center_top50_inventory"]
        raw = plan["top50_reasoning_models"]

        self.assertTrue(plan["all_top50_models_received_by_expert_center"])
        self.assertEqual(plan["expert_center_top50_inventory_count"], 50)
        self.assertEqual(len(inventory), 50)
        self.assertEqual(
            [row["model"] for row in inventory],
            [row["model"] for row in raw],
        )
        self.assertTrue(all(row["retained_by_expert_center"] for row in inventory))
        self.assertEqual(
            plan["expert_center_top50_inventory_sha256"],
            module._sha256(inventory),
        )
        self.assertEqual(receipt["top50_inventory_count"], 50)
        self.assertEqual(
            receipt["standby_inventory_sha256"], module._sha256(inventory)
        )

    def test_inventory_distinguishes_active_warm_extended_and_ineligible(self) -> None:
        module = _load_selector()
        packet, _ = module.materialize_top50_selection(_packet())
        plan = packet["governance_model_plan"]
        counts = plan["expert_center_top50_inventory_state_counts"]

        self.assertEqual(counts["active"], 4)
        self.assertEqual(counts["warm-recovery"], 4)
        self.assertEqual(counts["extended-recovery"], 22)
        self.assertEqual(counts["ineligible-standby"], 20)
        self.assertEqual(sum(counts.values()), 50)

        inventory = plan["expert_center_top50_inventory"]
        warm = [row for row in inventory if row["standby_state"] == "warm-recovery"]
        extended = [
            row for row in inventory if row["standby_state"] == "extended-recovery"
        ]
        disabled = [
            row for row in inventory if row["standby_state"] == "ineligible-standby"
        ]
        self.assertTrue(all(row["execution_eligible"] for row in warm))
        self.assertTrue(all(row["execution_eligible"] for row in extended))
        self.assertTrue(all(not row["execution_eligible"] for row in disabled))
        self.assertEqual(
            [row["recovery_priority"] for row in warm + extended],
            list(range(1, 27)),
        )


if __name__ == "__main__":
    unittest.main()
