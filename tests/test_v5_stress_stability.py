import hashlib
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import v5_budget_runtime_parity as budget_parity  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402


TASK = (
    "JSON顶层必须严格包含且仅包含以下字段：facts、assumptions、unknowns、options、"
    "formulas、decision_tree、hard_rejections、day_by_day_plan、red_team、"
    "final_recommendation。options必须恰好包含continue_current、supplier_a、"
    "supplier_b、hybrid四个对象；day_by_day_plan必须覆盖day_0到day_14。"
)
OPTIONS = ("continue_current", "supplier_a", "supplier_b", "hybrid")
DAYS = tuple(f"day_{index}" for index in range(15))


def valid_payload():
    return {
        "facts": ["known"],
        "assumptions": ["assumed"],
        "unknowns": ["unknown"],
        "options": {key: {"decision": "conditional"} for key in OPTIONS},
        "formulas": ["x=y"],
        "decision_tree": {"if": "then"},
        "hard_rejections": ["unsafe"],
        "day_by_day_plan": {key: {"action": key} for key in DAYS},
        "red_team": ["counterexample"],
        "final_recommendation": {"condition": "evidence"},
    }


class TestV5StressStability(unittest.TestCase):
    def test_contract_extraction_and_validation_are_concurrently_deterministic(self):
        expected_contract = contract_policy.extract_explicit_contract(TASK)
        expected_payload = valid_payload()
        expected = hashlib.sha256(
            json.dumps(
                expected_contract,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        def exercise(_):
            contract = contract_policy.extract_explicit_contract(TASK)
            violations = contract_policy.validate_parsed_contract(
                expected_payload, contract
            )
            digest = hashlib.sha256(
                json.dumps(
                    contract,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            return digest, violations

        with ThreadPoolExecutor(max_workers=32) as pool:
            results = list(pool.map(exercise, range(1024)))
        self.assertEqual({digest for digest, _ in results}, {expected})
        self.assertTrue(all(not violations for _, violations in results))

    def test_contract_rejects_large_matrix_of_schema_mutations(self):
        contract = contract_policy.extract_explicit_contract(TASK)
        mutations = []
        for field in contract["exact_top_level_fields"]:
            payload = valid_payload()
            payload.pop(field)
            mutations.append(payload)
        for field in OPTIONS:
            payload = valid_payload()
            payload["options"].pop(field)
            mutations.append(payload)
        for field in DAYS:
            payload = valid_payload()
            payload["day_by_day_plan"].pop(field)
            mutations.append(payload)
        for index in range(128):
            payload = valid_payload()
            payload[f"unexpected_{index}"] = index
            mutations.append(payload)

        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(
                pool.map(
                    lambda payload: contract_policy.validate_parsed_contract(
                        payload, contract
                    ),
                    mutations,
                )
            )
        self.assertEqual(len(results), len(mutations))
        self.assertTrue(all(result for result in results))

    def test_budget_parity_boundary_matrix_matches_runtime_guard(self):
        limits = GraphLimits(max_budget_usd=0.25, cost_risk_multiplier=1.18)
        raw_limit = 0.25 / 1.18
        raw_values = [raw_limit * index / 100 for index in range(1, 101)]

        def fake_optimize(candidate_bundle, *, limits, **kwargs):
            raw = candidate_bundle["raw"]
            self.assertLessEqual(raw, limits.max_budget_usd + 1e-12)
            return {
                "execution_graph": {
                    "estimated_total_cost": raw,
                    "metadata": {},
                }
            }

        with patch.object(
            budget_parity,
            "_ORIGINAL_OPTIMIZE",
            side_effect=fake_optimize,
        ):
            for raw in raw_values:
                result = budget_parity.risk_budgeted_optimize_execution_graph(
                    {"raw": raw, "candidates": [{}] * 64},
                    limits=limits,
                    solver_timeout_seconds=20.0,
                )
                evidence = result["budget_preflight_parity"]
                self.assertLessEqual(
                    evidence["selected_risk_adjusted_cost_usd"],
                    0.25 + 1e-8,
                )


if __name__ == "__main__":
    unittest.main()
