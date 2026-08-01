from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_paid_acceptance_revalidation import revalidate  # noqa: E402


class V5PaidAcceptanceRevalidationTests(unittest.TestCase):
    def _write(self, root: Path, name: str, value) -> None:
        path = root / name
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _fixture(self, root: Path) -> None:
        headings = [
            "已知条件、假设与未知项",
            "成本结构、公式与选择阈值",
            "实施风险、运营风险与反证",
            "综合结论、适用条件与下一步",
        ]
        task = (
            "不得调用外部工具。严格使用以下4个Markdown二级标题：\n"
            + "\n".join(f"{index}）{heading}" for index, heading in enumerate(headings, 1))
        )
        report = "\n\n".join(
            f"## {heading}\n假设：仅进行定性比较。"
            for heading in headings
        )
        node = {
            "node_id": "n1",
            "status": "success",
            "contract": {"required_fields_complete": True},
        }
        self._write(root, "v5-result.json", {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
        })
        self._write(root, "v5-execution-summary.json", {
            "status": "success",
            "completion_mode": "full",
            "actual_cost_usd": 0.001,
            "execution_budget": {"calls_reserved": 1},
        })
        self._write(root, "actual-model-company-audit.json", {
            "status": "PASS",
            "policy": "recompute-from-all-actual-cross-node-calls-and-successes",
            "successful_node_models": [
                {"node_id": "n1", "model": "openai/model", "company": "openai"}
            ],
            "all_called_models": [
                {"node_id": "n1", "model": "openai/model", "company": "openai"}
            ],
            "duplicate_successful_companies": {},
            "duplicate_called_companies": {},
            "unknown_company_models": [],
        })
        self._write(root, "v5-execution-graph.json", {"nodes": [{"node_id": "n1"}]})
        self._write(root, "v5-adaptive-search.json", {
            "policy": "task-shape-feasibility-marginal-value",
            "attempts": [{"attempt": 1}],
        })
        self._write(root, "v5-constitution.json", {"version": "v5-constitution-2"})
        self._write(root, "v5-node-results.json", [node])
        self._write(root, "v5-final-report.md", report)
        self._write(root, "paid-acceptance-task.txt", task)
        entries = []
        for path in sorted(root.iterdir()):
            if path.name == "artifact-manifest.json":
                continue
            entries.append({
                "path": path.name,
                "size": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        self._write(root, "artifact-manifest.json", {"files": entries})

    def test_revalidation_recomputes_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-paid-revalidate-") as directory:
            root = Path(directory)
            self._fixture(root)
            result = revalidate(
                root,
                head_sha="a" * 40,
                run_id="123",
                run_url="https://github.invalid/actions/runs/123",
                maximum_calls=4,
                maximum_cost=0.03,
                artifact_id=456,
                artifact_name="artifact",
                artifact_digest="sha256:" + "b" * 64,
            )
            self.assertEqual("PASS", result["status"])
            self.assertTrue(result["raw_evidence_recomputed"])
            self.assertEqual("PASS", result["manifest_file_revalidation"])

    def test_unmanifested_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-paid-revalidate-") as directory:
            root = Path(directory)
            self._fixture(root)
            (root / "untracked.txt").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "coverage mismatch"):
                revalidate(
                    root,
                    head_sha="a" * 40,
                    run_id="123",
                    run_url="https://github.invalid/actions/runs/123",
                    maximum_calls=4,
                    maximum_cost=0.03,
                    artifact_id=456,
                    artifact_name="artifact",
                    artifact_digest="sha256:" + "b" * 64,
                )


if __name__ == "__main__":
    unittest.main()
