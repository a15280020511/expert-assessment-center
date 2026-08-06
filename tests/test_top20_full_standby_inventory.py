from __future__ import annotations

import unittest

from tests.test_top20_pool_selector import _load_selector, _packet


class Top20FullStandbyInventoryTests(unittest.TestCase):
    def test_all_twenty_models_are_retained_with_explicit_state(self) -> None:
        module = _load_selector()
        packet, receipt = module.materialize_top20_selection(_packet())
        plan = packet["governance_model_plan"]
        inventory = plan["expert_center_top20_inventory"]
        raw = plan["top20_reasoning_models"]

        self.assertTrue(plan["all_top20_models_received_by_expert_center"])
        self.assertEqual(plan["expert_center_top20_inventory_count"], 20)
        self.assertEqual(plan["expert_center_standby_model_count"], 16)
        self.assertEqual(len(inventory), 20)
        self.assertEqual(
            [row["model"] for row in inventory],
            [row["model"] for row in raw],
        )
        self.assertTrue(all(row["retained_by_expert_center"] for row in inventory))
        self.assertEqual(
            plan["expert_center_top20_inventory_sha256"],
            module._sha256(inventory),
        )
        self.assertEqual(receipt["top20_inventory_count"], 20)
        self.assertEqual(receipt["standby_inventory_sha256"], module._sha256(inventory))

    def test_inventory_distinguishes_active_warm_and_deep_standby(self) -> None:
        module = _load_selector()
        packet, _ = module.materialize_top20_selection(_packet())
        plan = packet["governance_model_plan"]
        counts = plan["expert_center_top20_inventory_state_counts"]

        self.assertEqual(counts["active"], 4)
        self.assertEqual(counts["warm-recovery"], 4)
        self.assertEqual(counts["extended-standby"], 2)
        self.assertEqual(counts["ineligible-standby"], 10)
        self.assertEqual(sum(counts.values()), 20)

        inventory = plan["expert_center_top20_inventory"]
        warm = [row for row in inventory if row["standby_state"] == "warm-recovery"]
        deep = [row for row in inventory if row["standby_state"] == "extended-standby"]
        disabled = [
            row for row in inventory if row["standby_state"] == "ineligible-standby"
        ]
        self.assertTrue(all(row["callable_under_current_recovery_ceiling"] for row in warm))
        self.assertTrue(all(row["execution_eligible"] for row in deep))
        self.assertTrue(all(not row["execution_eligible"] for row in disabled))


if __name__ == "__main__":
    unittest.main()
