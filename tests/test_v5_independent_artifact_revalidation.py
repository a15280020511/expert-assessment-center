from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_independent_artifact_revalidation import (  # noqa: E402
    _final_contract_violations,
    _manifest_checks,
    recompute,
)
from v5_task_constraints import compile_task_constraints  # noqa: E402


class IndependentArtifactRevalidationTests(unittest.TestCase):
    def _write(self, root: Path, name: str, value) -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _manifest(self, root: Path, *, sha: str, run_id: str) -> None:
        files = []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.name == "artifact-manifest.json":
                continue
            files.append(
                {
                    "path": str(path.relative_to(root)),
                    "size": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        self._write(
            root,
            "artifact-manifest.json",
            {
                "schema_version": "v5-artifact-manifest-2",
                "execution_sha": sha,
                "github_run_id": run_id,
                "execution_sha_policy": "checked-out-git-head-is-authoritative",
                "files": files,
            },
        )

    def _fixture(self, root: Path) -> tuple[str, str]:
        sha = "a" * 40
        run_id = "123456"
        task = {
            "question": "仅依据题面比较方案A和方案B，不得编造。",
            "requirements": ["事实与推断分开", "只接受完整交付"],
            "language": "zh-CN",
        }
        canonical = (
            task["question"]
            + "\n\n执行要求：\n- "
            + "\n- ".join(task["requirements"])
            + "\n\n输出语言：zh-CN"
        )
        constraints = compile_task_constraints(canonical).to_dict()
        report = (
            "## facts\n\n题面事实仅包括存在方案A和方案B。\n\n"
            "## conclusion\n\n在缺少成本与效果数据时，不作无依据数值判断；"
            "先收集同口径数据，再依据用户目标比较。"
            + "该结论保持事实、假设与推断边界。" * 10
        )
        graph = {
            "nodes": [
                {
                    "node_id": "node-final",
                    "output_contract": {
                        "required_fields": ["facts", "conclusion"],
                        "machine_readable_required": False,
                    },
                    "parameter_profile": {},
                }
            ],
            "final_nodes": ["node-final"],
        }
        def request(model: str) -> dict[str, object]:
            return {
                "model": model,
                "messages": [{"role": "user", "content": canonical}],
                "provider": {
                    "only": ["provider/default"],
                    "order": ["provider/default"],
                    "allow_fallbacks": False,
                },
            }

        expert_request = request("openai/model-a")
        governance_requests = [
            request("openai/gpt-latest"),
            request("anthropic/claude-opus-latest"),
            request("openai/gpt-latest"),
        ]
        nodes = [
            {
                "node_id": "node-final",
                "status": "success",
                "actual_cost_usd": 0.01,
                "contract": {"required_fields_complete": True},
                "attempts": [
                    {
                        "attempt_index": 1,
                        "attempt_kind": "initial",
                        "status": "passed",
                        "model": "openai/model-a",
                        "response_model": "openai/model-a",
                        "request": expert_request,
                    }
                ],
            }
        ]
        summary = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "final_answer": report,
            "actual_cost_usd": 0.016,
            "expert_actual_cost_usd": 0.01,
            "execution_budget": {"calls_reserved": 1},
        }
        self._write(root, "ticket.json", {"task": task})
        self._write(root, "expert-team-result.json", summary)
        self._write(root, "v5-execution-summary.json", summary)
        self._write(root, "v5-execution-graph.json", graph)
        self._write(root, "v5-node-results.json", nodes)
        self._write(
            root,
            "v5-request-audit.json",
            {
                "request_count": 4,
                "governance_request_count": 3,
                "expert_request_count": 1,
                "requests": [*governance_requests, expert_request],
                "external_tools_allowed": False,
                "provider_fallback_allowed": False,
            },
        )
        self._write(root, "task-constraints.json", constraints)
        self._write(
            root,
            "evidence-integrity.json",
            {
                "status": "PASS",
                "violations": [],
                "fact_truth_not_inferred_from_structure": True,
            },
        )
        self._write(
            root,
            "v5-governance-calls.json",
            {
                "actual_governance_calls": 3,
                "claude_red_team_calls": 1,
                "gpt_synthesis_calls": 1,
                "claude_is_advisory_only": True,
                "claude_gatekeeping_allowed": False,
                "second_claude_review_allowed": False,
                "model_loop_allowed": False,
                "actual_cost_usd": 0.006,
                "calls": [
                    {
                        "kind": "gpt_proposal",
                        "actual_cost_usd": 0.002,
                        "request": governance_requests[0],
                    },
                    {
                        "kind": "claude_red_team",
                        "actual_cost_usd": 0.002,
                        "request": governance_requests[1],
                    },
                    {
                        "kind": "gpt_synthesis",
                        "actual_cost_usd": 0.002,
                        "request": governance_requests[2],
                    },
                ],
            },
        )
        self._write(
            root,
            "call-ledger.json",
            {"summary": {"provider_actual_cost_usd": 0.016}},
        )
        self._write(root, "v5-final-report.md", report)
        self._manifest(root, sha=sha, run_id=run_id)
        return sha, run_id

    def test_full_primitive_recomputation_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha, run_id = self._fixture(root)
            result = recompute(
                root,
                expected_sha=sha,
                expected_run_id=run_id,
                maximum_calls=4,
                maximum_cost_usd=0.03,
            )
            self.assertEqual(result["status"], "PASS", result["failures"])
            self.assertTrue(result["recomputed_from_primitive_evidence"])
            self.assertFalse(result["paid_acceptance_verdict_used_as_source"])
            self.assertEqual(result["artifact_run_id"], run_id)
            self.assertEqual(result["model_calls"], 4)
            self.assertEqual(result["governance_model_calls"], 3)
            self.assertEqual(result["expert_model_calls"], 1)
            self.assertAlmostEqual(result["actual_cost_usd"], 0.016)
            self.assertAlmostEqual(result["governance_actual_cost_usd"], 0.006)
            self.assertAlmostEqual(result["expert_actual_cost_usd"], 0.01)

    def test_total_request_and_cost_accounting_include_governance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha, run_id = self._fixture(root)
            audit = json.loads(
                (root / "v5-request-audit.json").read_text(encoding="utf-8")
            )
            audit["request_count"] = 1
            self._write(root, "v5-request-audit.json", audit)
            self._manifest(root, sha=sha, run_id=run_id)
            result = recompute(
                root,
                expected_sha=sha,
                expected_run_id=run_id,
                maximum_calls=4,
                maximum_cost_usd=0.03,
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(
                any(
                    "request audit total count" in value
                    for value in result["failures"]
                )
            )

    def test_invalid_governance_sequence_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha, run_id = self._fixture(root)
            ledger = json.loads(
                (root / "v5-governance-calls.json").read_text(encoding="utf-8")
            )
            ledger["calls"][1]["kind"] = "gpt_proposal"
            self._write(root, "v5-governance-calls.json", ledger)
            self._manifest(root, sha=sha, run_id=run_id)
            result = recompute(
                root,
                expected_sha=sha,
                expected_run_id=run_id,
                maximum_calls=4,
                maximum_cost_usd=0.03,
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(
                any(
                    "governance call sequence" in value
                    for value in result["failures"]
                )
            )

    def test_missing_runtime_evidence_returns_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha, run_id = self._fixture(root)
            (root / "evidence-integrity.json").unlink()
            self._manifest(root, sha=sha, run_id=run_id)
            result = recompute(
                root,
                expected_sha=sha,
                expected_run_id=run_id,
                maximum_calls=4,
                maximum_cost_usd=0.03,
            )
            self.assertEqual(result["status"], "FAIL")
            self.assertTrue(result["recomputed_from_primitive_evidence"])
            self.assertTrue(
                any(
                    "runtime evidence integrity is not PASS" in value
                    for value in result["failures"]
                )
            )

    def test_manifest_run_and_file_coverage_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sha, run_id = self._fixture(root)
            (root / "unlisted.txt").write_text("not in manifest", encoding="utf-8")
            result = _manifest_checks(root, sha, "different-run")
            rendered = "\n".join(result["failures"])
            self.assertIn("Run ID mismatch", rendered)
            self.assertIn("omitted from manifest", rendered)

    def test_final_report_contract_is_recomputed(self) -> None:
        graph = {
            "nodes": [
                {
                    "node_id": "final",
                    "output_contract": {
                        "required_fields": ["facts", "conclusion"],
                        "machine_readable_required": False,
                    },
                    "parameter_profile": {},
                }
            ],
            "final_nodes": ["final"],
        }
        good = "## facts\n\nknown\n\n## conclusion\n\ndecision"
        bad = "## conclusion\n\ndecision"
        self.assertEqual(_final_contract_violations(graph, good), [])
        self.assertTrue(_final_contract_violations(graph, bad))


if __name__ == "__main__":
    unittest.main()
