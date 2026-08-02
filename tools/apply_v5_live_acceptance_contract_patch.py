#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one replacement in {path}, found {count}")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "open-model-market/v5_task_constraints.py",
    '''_FACT_LINE_RE = re.compile(
    r"(?im)^\\s*(?:[-*+]\\s*)?(?:事实|已知事实|fact)\\s*"
    r"(?:[（(][^）)]*[）)])?\\s*[:：-]?\\s*(?P<claim>.+?)\\s*$"
)
''',
    '''_FACT_LINE_RE = re.compile(
    r"(?im)^\\s*(?:[-*+]\\s*)?(?:\\*\\*)?(?:事实|已知事实|fact)"
    r"(?:\\s*[（(][^）)]*[）)]|\\s*[|｜]\\s*[^*\\n]+)?(?:\\*\\*)?"
    r"\\s*[:：-]\\s*(?P<claim>.+?)\\s*$"
)
''',
)

replace_once(
    "open-model-market/v5_independent_artifact_revalidation.py",
    '    result = _load(root / "v5-result.json")\n',
    '    result = _load(root / "expert-team-result.json")\n',
)
replace_once(
    "open-model-market/v5_independent_artifact_revalidation.py",
    '    runtime_evidence = _load(root / "evidence-integrity.json")\n',
    '    runtime_evidence = _load_optional(root / "evidence-integrity.json", {})\n',
)

replace_once(
    "open-model-market/v5_constitutional_runtime.py",
    '''        result = super().execute_graph(*args, **kwargs)
        company_audit = self._actual_company_audit(result)
        evidence_violations = validate_answer_evidence(
''',
    '''        try:
            result = super().execute_graph(*args, **kwargs)
        except Exception:
            if root is not None:
                try:
                    node_results = json.loads(
                        (root / "v5-node-results.json").read_text(encoding="utf-8")
                    )
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    node_results = []
                if not isinstance(node_results, list):
                    node_results = []
                try:
                    summary = json.loads(
                        (root / "v5-execution-summary.json").read_text(
                            encoding="utf-8"
                        )
                    )
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    summary = {}
                if not isinstance(summary, Mapping):
                    summary = {}
                company_audit = self._actual_company_audit(
                    {"node_results": node_results}
                )
                evidence_violations = validate_answer_evidence(
                    original_task,
                    str(summary.get("final_answer") or ""),
                    constraints,
                )
                evidence_audit = {
                    "schema_version": "v5-evidence-integrity-1",
                    "status": "FAIL" if evidence_violations else "PASS",
                    "constraints": constraints.to_dict(),
                    "violations": evidence_violations,
                    "fact_truth_not_inferred_from_structure": True,
                    "upstream_model_claims_are_not_promoted_to_user_facts": True,
                    "written_after_execution_failure": True,
                }
                self._write_json(
                    root / "actual-model-company-audit.json",
                    company_audit,
                )
                self._write_json(
                    root / "evidence-integrity.json",
                    evidence_audit,
                )
            raise
        company_audit = self._actual_company_audit(result)
        evidence_violations = validate_answer_evidence(
''',
)

replace_once(
    "tests/test_v5_independent_artifact_revalidation.py",
    '        self._write(root, "v5-result.json", summary)\n',
    '        self._write(root, "expert-team-result.json", summary)\n',
)

replace_once(
    "tests/test_v5_task_constraints.py",
    '''    def test_contradictory_fact_remains_rejected(self) -> None:
        task = self.TASK + "A路线存在积水。"
        violations = validate_answer_evidence(task, "事实：A路线未发现积水。")
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )


class DynamicObjectiveTests(unittest.TestCase):
''',
    '''    def test_contradictory_fact_remains_rejected(self) -> None:
        task = self.TASK + "A路线存在积水。"
        violations = validate_answer_evidence(task, "事实：A路线未发现积水。")
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )

    def test_fact_taxonomy_sentence_is_not_a_fact_label(self) -> None:
        answer = (
            "- 事实、假设、推断、未知四类标签在正文中明确区分，"
            "且事实仅来自题面原句。"
        )
        self.assertEqual(validate_answer_evidence(self.TASK, answer), [])

    def test_bold_fact_source_label_is_validated(self) -> None:
        task = self.TASK + "北门外地面干燥。"
        supported = "- **事实｜来源：题面**：北门外地面干燥。"
        unsupported = "- **事实｜来源：题面**：北门外发生坍塌。"
        self.assertEqual(validate_answer_evidence(task, supported), [])
        violations = validate_answer_evidence(task, unsupported)
        self.assertTrue(
            any(value.startswith("unsupported-fact-label:") for value in violations)
        )


class DynamicObjectiveTests(unittest.TestCase):
''',
)

replace_once(
    "tests/test_v5_independent_artifact_revalidation.py",
    '''    def test_manifest_run_and_file_coverage_fail_closed(self) -> None:
''',
    '''    def test_missing_runtime_evidence_returns_structured_failure(self) -> None:
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
''',
)

failure_test = ROOT / "tests/test_v5_failure_evidence_persistence.py"
failure_test.write_text(
    '''from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitutional_runtime import ConstitutionalExecutionEngine  # noqa: E402
from v5_runtime import ExecutionEngine  # noqa: E402


class FailureEvidencePersistenceTests(unittest.TestCase):
    def test_runtime_failure_still_persists_constitutional_evidence(self) -> None:
        task = "仅依据题面，不得编造。北门外地面干燥。"
        report = "事实：北门外地面干燥。"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "v5-node-results.json").write_text(
                json.dumps(
                    [
                        {
                            "node_id": "node-a",
                            "status": "failed",
                            "selected_model": "openai/model-a",
                            "attempts": [
                                {
                                    "attempt_kind": "initial",
                                    "status": "call_failed",
                                    "model": "openai/model-a",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (root / "v5-execution-summary.json").write_text(
                json.dumps({"final_answer": report}),
                encoding="utf-8",
            )
            engine = ConstitutionalExecutionEngine.__new__(
                ConstitutionalExecutionEngine
            )
            with patch.object(
                ExecutionEngine,
                "execute_graph",
                side_effect=RuntimeError("coverage gate failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "coverage gate failed"):
                    engine.execute_graph(
                        original_task=task,
                        output_dir=root,
                    )

            evidence = json.loads(
                (root / "evidence-integrity.json").read_text(encoding="utf-8")
            )
            company = json.loads(
                (root / "actual-model-company-audit.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(evidence["status"], "PASS")
            self.assertTrue(evidence["written_after_execution_failure"])
            self.assertEqual(company["status"], "PASS")
            self.assertTrue(company["failed_calls_are_included"])
            self.assertEqual(len(company["all_called_models"]), 1)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
