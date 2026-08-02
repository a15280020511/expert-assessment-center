from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
if str(MARKET) not in sys.path:
    sys.path.insert(0, str(MARKET))

import model_market  # noqa: E402
from v5_catalog_view import (  # noqa: E402
    compact_endpoint_catalog,
    eligible_models,
)
from v5_governance_runtime import run_single_pass_governance  # noqa: E402
from v5_proposal_materializer import (  # noqa: E402
    ProposalValidationError,
    graph_sha256,
    materialize_proposal,
)
from v5_task_envelope import build_task_envelope  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def _catalog(task: str = "extreme deterministic test") -> dict:
    args = SimpleNamespace(
        task=task,
        output_dir="unused",
        config=str(MARKET / "config.json"),
        catalog_file=str(FIXTURES / "models.json"),
        endpoint_file=str(FIXTURES / "endpoints.json"),
        ranking_limit=150,
        maximum_total_calls=16,
        maximum_recovery_calls=1,
        max_completion_tokens=10_000,
        reasoning_effort="low",
        require_live_catalog=False,
        dry_run=True,
    )
    run = model_market.build_run_config(args)
    models, _ = model_market.fetch_catalog(run)
    ranked = eligible_models(
        models,
        requested_context=20_000,
        maximum_models=150,
    )
    endpoints = json.loads(
        (FIXTURES / "endpoints.json").read_text(encoding="utf-8")
    )
    return compact_endpoint_catalog(ranked, endpoints)


def _proposal(catalog: dict, node_count: int = 12) -> dict:
    rows = catalog["endpoints"]
    unique: list[dict] = []
    companies: set[str] = set()
    for row in rows:
        if row["company"] in companies:
            continue
        companies.add(row["company"])
        unique.append(row)
    if len(unique) < node_count + 1:
        raise AssertionError("fixture lacks globally unique expert companies")
    work_items = []
    nodes = []
    edges = []
    for index in range(node_count):
        work_id = f"work-{index + 1}"
        dependencies = [] if index == 0 else [f"work-{index}"]
        work_items.append({
            "work_id": work_id,
            "objective": f"Complete independent bounded objective {index + 1}",
            "dependencies": dependencies,
            "required_outputs": [f"result_{index + 1}"],
        })
        endpoint = unique[index]
        nodes.append({
            "node_id": f"node-{index + 1}",
            "work_ids": [work_id],
            "role": f"Dynamically selected role {index + 1}",
            "functions": [f"function_{index + 1}"],
            "model": endpoint["model"],
            "provider": endpoint["provider"],
            "reasoning_effort": "medium",
            "max_output_tokens": 1024,
            "recovery": [],
        })
        if index:
            edges.append({
                "source": f"node-{index}",
                "target": f"node-{index + 1}",
                "relation_type": "dependency",
            })
    nodes[0]["recovery"] = [{
        "model": unique[node_count]["model"],
        "provider": unique[node_count]["provider"],
    }]
    return {
        "work_items": work_items,
        "nodes": nodes,
        "edges": edges,
        "final_nodes": [f"node-{node_count}"],
    }


def _envelope(task: str) -> dict:
    return build_task_envelope(
        task,
        minimum_context_length=16_384,
        maximum_completion_tokens=10_000,
    )


class ExtremeAdvisoryStressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.task = (
            "Evaluate a twelve-part decision system with explicit dependencies, "
            "separate evidence, calculations, adversarial review, and final synthesis."
        )
        cls.catalog = _catalog(cls.task)
        cls.envelope = _envelope(cls.task)
        cls.proposal = _proposal(cls.catalog)

    def _materialize(self, proposal: dict | None = None):
        return materialize_proposal(
            proposal or self.proposal,
            self.task,
            self.envelope,
            self.catalog,
            approved_total_calls=16,
            governance_calls_reserved=3,
            approved_recovery_calls=1,
            cost_anomaly_usd=None,
        )

    def test_maximum_initial_team_materializes_without_local_scoring(self) -> None:
        graph, limits, audit = self._materialize()
        self.assertEqual(12, len(graph.nodes))
        self.assertEqual(12, len(graph.required_work))
        self.assertEqual(12, len(graph.execution_stages))
        self.assertEqual(12, limits.max_nodes)
        self.assertEqual("PASS", audit["status"])
        self.assertFalse(audit["local_task_classification_used"])
        self.assertFalse(audit["local_atomic_work_generation_used"])
        self.assertFalse(audit["local_resource_matrix_used"])
        self.assertFalse(audit["local_scoring_used"])
        self.assertFalse(audit["optimizer_used"])

    def test_parallel_materialization_is_deterministic_and_isolated(self) -> None:
        def run(_: int) -> str:
            graph, _, audit = self._materialize(copy.deepcopy(self.proposal))
            return graph_sha256({"graph": graph.to_dict(), "audit": audit})

        with ThreadPoolExecutor(max_workers=32) as pool:
            digests = list(pool.map(run, range(512)))
        self.assertEqual(1, len(set(digests)))

    def test_duplicate_company_across_selected_and_recovery_fails(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["nodes"][0]["recovery"][0] = {
            "model": proposal["nodes"][1]["model"],
            "provider": proposal["nodes"][1]["provider"],
        }
        with self.assertRaisesRegex(
            ProposalValidationError, "globally unique"
        ):
            self._materialize(proposal)

    def test_work_dependency_cycle_fails_before_execution(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["work_items"][0]["dependencies"] = ["work-12"]
        with self.assertRaisesRegex(
            ProposalValidationError, "acyclic"
        ):
            self._materialize(proposal)

    def test_missing_dependency_edge_fails_before_execution(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["edges"].pop(0)
        with self.assertRaisesRegex(
            ProposalValidationError, "missing-dependency-edge"
        ):
            self._materialize(proposal)

    def test_unknown_provider_fails_closed(self) -> None:
        proposal = copy.deepcopy(self.proposal)
        proposal["nodes"][0]["provider"] = "unknown-provider"
        with self.assertRaisesRegex(
            ProposalValidationError, "unknown exact endpoint"
        ):
            self._materialize(proposal)

    def test_governance_executes_exactly_gpt_claude_gpt_once(self) -> None:
        proposal_json = json.dumps(self.proposal, ensure_ascii=False)
        calls: list[str] = []

        def fake_call(run, request):
            model = str(request["model"])
            calls.append(model)
            if "claude" in model:
                content = json.dumps({"suggestions": []})
                provider = "anthropic"
            else:
                content = proposal_json
                provider = "openai"
            return ({
                "id": f"response-{len(calls)}",
                "model": model,
                "provider": provider,
                "choices": [{"message": {"content": content}}],
                "usage": {"cost": 0.0},
            }, 0.001)

        graph, _, governance, ledger = run_single_pass_governance(
            run=SimpleNamespace(api_key="fixture", model_timeout_seconds=1),
            task=self.task,
            task_digest=self.envelope["task_sha256"],
            task_envelope=self.envelope,
            catalog=self.catalog,
            approved_total_calls=16,
            governance_calls_reserved=3,
            approved_recovery_calls=1,
            cost_anomaly_usd=None,
            call_fn=fake_call,
        )
        self.assertEqual(3, len(calls))
        self.assertEqual(
            [
                "~openai/gpt-latest",
                "~anthropic/claude-opus-latest",
                "~openai/gpt-latest",
            ],
            calls,
        )
        self.assertEqual(12, len(graph.nodes))
        self.assertEqual(3, ledger["actual_governance_calls"])
        self.assertEqual(1, ledger["claude_red_team_calls"])
        self.assertEqual(1, ledger["gpt_synthesis_calls"])
        self.assertFalse(governance["second_claude_review_allowed"])
        self.assertFalse(governance["model_loop_allowed"])

    def test_oversized_task_dry_run_is_zero_call_and_deterministic(self) -> None:
        headings = "；".join(f"第{index}节" for index in range(1, 17))
        task = (
            "请严格使用以下16个Markdown二级标题，顺序不得改变且每节非空："
            + headings
            + "。仅依据题面，不得联网，不得调用工具。"
            + "甲" * 48_000
        )
        signatures: list[str] = []
        with tempfile.TemporaryDirectory(prefix="v5-extreme-dry-") as directory:
            root = Path(directory)
            for iteration in range(4):
                output = root / str(iteration)
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(MARKET / "v5_pipeline.py"),
                        "--task",
                        task,
                        "--catalog-file",
                        str(FIXTURES / "models.json"),
                        "--endpoint-file",
                        str(FIXTURES / "endpoints.json"),
                        "--dry-run",
                        "--maximum-total-calls",
                        "16",
                        "--maximum-recovery-calls",
                        "1",
                        "--output-dir",
                        str(output),
                    ],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=180,
                    check=False,
                )
                self.assertEqual(
                    0,
                    completed.returncode,
                    completed.stdout + completed.stderr,
                )
                dry = json.loads(
                    (output / "v5-dry-run.json").read_text(encoding="utf-8")
                )
                self.assertEqual(0, dry["model_calls"])
                self.assertFalse(
                    dry["local_task_classification_used"]
                )
                self.assertFalse(
                    dry["local_atomic_work_generation_used"]
                )
                self.assertFalse(dry["local_resource_matrix_used"])
                self.assertFalse(
                    (output / "v5-execution-graph.json").exists()
                )
                signatures.append(
                    json.dumps(dry, sort_keys=True, ensure_ascii=False)
                )
        self.assertEqual(1, len(set(signatures)))


if __name__ == "__main__":
    unittest.main()
