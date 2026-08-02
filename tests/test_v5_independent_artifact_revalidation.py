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
        request = {
            "model": "openai/model-a",
            "messages": [{"role": "user", "content": canonical}],
            "provider": {
                "only": ["provider/default"],
                "order": ["provider/default"],
                "allow_fallbacks": False,
            },
        }
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
                        "request": request,
                    }
                ],
            }
        ]
        summary = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "final_answer": report,
            "actual_cost_usd": 0.01,
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
                "request_count": 1,
                "external_tools_allowed": False,
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
            "call-ledger.json",
            {"summary": {"provider_actual_cost_usd": 0.01}},
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
