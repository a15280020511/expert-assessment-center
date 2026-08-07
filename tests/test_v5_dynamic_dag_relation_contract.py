from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import execution_graph_validator as validator  # noqa: E402
from v5_governance_model_plan import plan_sha256  # noqa: E402
from v5_governed_plan_orchestrator import build_governed_proposal  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "governance-ticket.json"


class DynamicDagRelationContractTests(unittest.TestCase):
    def test_arbitrary_role_dag_uses_validator_supported_relation_enum(self) -> None:
        ticket = json.loads(FIXTURE.read_text(encoding="utf-8"))
        plan = ticket["governance_model_plan"]
        source = plan["selected_models"]
        rows = [copy.deepcopy(source[index % len(source)]) for index in range(4)]
        role_rows = [
            ("baseline", "dynamic:baseline", []),
            ("left", "dynamic:left-specialist", ["baseline"]),
            ("right", "dynamic:right-specialist", ["baseline"]),
            ("arbiter", "dynamic:arbiter", ["left", "right"]),
        ]
        for index, (role_id, role_kind, dependencies) in enumerate(role_rows):
            rows[index]["model"] = f"vendor-{index}/model-{index}"
            rows[index]["role_id"] = role_id
            rows[index]["role_kind"] = role_kind
            rows[index]["depends_on_role_ids"] = dependencies
            rows[index]["assigned_work_units"] = [f"unit-{index}"]
            rows[index]["final_role"] = role_id == "arbiter"
        plan["selected_models"] = rows
        plan["expert_count"] = len(rows)
        plan["recovery_models"] = []
        plan["recovery_count"] = 0
        plan["plan_sha256"] = plan_sha256(plan)

        proposal, audit = build_governed_proposal(
            ticket=ticket,
            catalog={},
            task_envelope={},
        )

        relation_types = {edge["relation_type"] for edge in proposal["edges"]}
        self.assertEqual({"dependency"}, relation_types)
        self.assertTrue(relation_types.issubset(validator._ALLOWED_RELATIONS))  # noqa: SLF001
        self.assertEqual("dependency", audit["execution_relation_type"])
        self.assertEqual(
            "current-plan-declared-role-dependency",
            audit["dependency_semantics"],
        )
        # Topology remains arbitrary: the protocol enum does not recreate a role grammar.
        edges = {(edge["source"], edge["target"]) for edge in proposal["edges"]}
        self.assertEqual(4, len(edges))
        self.assertFalse(audit["fixed_role_topology_used"])
        self.assertFalse(audit["fixed_role_grammar_used"])
        self.assertFalse(audit["role_dependencies_recomputed_from_role_kind"])


if __name__ == "__main__":
    unittest.main()
