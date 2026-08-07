from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_dynamic_role_assignment import solve_dynamic_roles  # noqa: E402
from v5_dynamic_role_scoring import (  # noqa: E402
    build_dynamic_recovery_metrics,
    build_dynamic_role_metrics,
    role_structural_profile,
)


def candidates(count: int = 8) -> list[dict[str, object]]:
    return [
        {
            "model": f"vendor-{index}/reasoner-{index}",
            "company": f"vendor-{index}",
            "popularity_rank": index,
            "official_intelligence_rank": count - index + 1,
            "prompt_usd_per_million": 0.1 * index,
            "completion_usd_per_million": 0.2 * index,
            "request_usd": 0.0,
            "context_length": 262_144,
            "max_completion_tokens": 32_768,
        }
        for index in range(1, count + 1)
    ]


def profile() -> dict[str, object]:
    return {
        "expected_prompt_tokens": 1200,
        "expected_completion_tokens": 900,
        "protocol_reserve_tokens": 400,
        "governance_context_floor": 1000,
        "requirement_count": 4,
        "acceptance_count": 3,
        "delivery_item_count": 2,
        "evidence_count": 5,
        "task_characters": 1000,
        "evidence_characters": 1800,
        "pressure": {
            "overall": 55,
            "input": 44,
            "constraints": 62,
            "evidence": 70,
            "delivery": 48,
        },
    }


def roles() -> list[dict[str, object]]:
    return [
        {
            "role_id": "alpha-branch",
            "role_kind": "dynamic:unseen-alpha-shape",
            "assigned_work_units": ["a"],
            "depends_on_role_ids": [],
            "functions": ["analyze:alpha"],
            "final_role": False,
        },
        {
            "role_id": "beta-cross-check",
            "role_kind": "dynamic:unseen-beta-shape",
            "assigned_work_units": ["b", "c"],
            "depends_on_role_ids": ["alpha-branch"],
            "functions": ["analyze:beta", "challenge:upstream"],
            "final_role": False,
        },
        {
            "role_id": "omega-terminal",
            "role_kind": "dynamic:never-predeclared-terminal",
            "assigned_work_units": ["d", "e", "f"],
            "depends_on_role_ids": ["alpha-branch", "beta-cross-check"],
            "functions": ["integrate:x", "verify:y", "deliver:z"],
            "final_role": True,
        },
    ]


class DynamicRoleScoringTests(unittest.TestCase):
    def test_role_demand_depends_on_structure_not_semantic_role_class(self) -> None:
        current_profile = profile()
        current_roles = roles()
        shapes = [role_structural_profile(current_profile, role) for role in current_roles]
        self.assertLess(shapes[0]["required_context_tokens"], shapes[-1]["required_context_tokens"])
        self.assertNotEqual(shapes[0]["weights"], shapes[-1]["weights"])
        self.assertTrue(all(not row["fixed_metric_role_class_used"] for row in shapes))
        self.assertTrue(all(not row["semantic_role_routing_used"] for row in shapes))

        renamed = dict(current_roles[1])
        renamed["role_id"] = "completely-different-name"
        renamed["role_kind"] = "dynamic:no-known-category"
        original_shape = role_structural_profile(current_profile, current_roles[1])
        renamed_shape = role_structural_profile(current_profile, renamed)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "required_context_tokens",
            "weights",
            "structural_pressure",
        ):
            self.assertEqual(original_shape[key], renamed_shape[key])

    def test_metrics_accept_arbitrary_roles_without_fixed_adapter(self) -> None:
        current_roles = roles()
        metrics = build_dynamic_role_metrics(candidates(), profile(), current_roles[1])
        self.assertEqual(8, len(metrics))
        for row in metrics.values():
            self.assertEqual(
                "current-generated-role-structural-signals",
                row["metric_source"],
            )
            self.assertFalse(row["fixed_metric_role_class_used"])
            self.assertEqual("beta-cross-check", row["role_id"])

    def test_recovery_uses_heaviest_current_generated_role(self) -> None:
        current_roles = roles()
        _, recovery_shape = build_dynamic_recovery_metrics(
            candidates(), profile(), current_roles
        )
        heaviest = max(
            (role_structural_profile(profile(), role) for role in current_roles),
            key=lambda row: (
                row["required_context_tokens"],
                row["completion_tokens"],
                row["structural_pressure"],
                row["role_id"],
            ),
        )
        self.assertEqual(
            heaviest["required_context_tokens"],
            recovery_shape["required_context_tokens"],
        )
        self.assertEqual("heaviest-current-generated-role", recovery_shape["source"])

    def test_ortools_assignment_preserves_arbitrary_roles(self) -> None:
        selected, recoveries, audit = solve_dynamic_roles(
            candidates(10), profile(), roles(), 2
        )
        self.assertEqual(3, len(selected))
        self.assertEqual(2, len(recoveries))
        self.assertEqual(5, len({row["model"] for row in [*selected, *recoveries]}))
        self.assertEqual(
            ["dynamic:unseen-alpha-shape", "dynamic:unseen-beta-shape", "dynamic:never-predeclared-terminal"],
            [row["role_kind"] for row in selected],
        )
        self.assertFalse(audit["metric_role_adapter_used"])
        self.assertFalse(audit["fixed_metric_role_grammar_used"])
        self.assertFalse(audit["semantic_role_routing_used"])
        self.assertEqual(
            "current-generated-role-structural-signals",
            audit["role_metric_mode"],
        )

    def test_active_hierarchical_optimizer_bypasses_legacy_metric_adapter(self) -> None:
        source = (MARKET / "v5_hierarchical_candidate_optimizer.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("solve_dynamic_roles", source)
        self.assertIn('role.pop("metric_role_id", None)', source)
        self.assertNotIn("selected, recoveries, solver_audit = base._solve", source)


if __name__ == "__main__":
    unittest.main()
