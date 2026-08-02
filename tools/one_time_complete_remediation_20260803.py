from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_exact(path: str, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    actual = text.count(old)
    if actual < count:
        raise RuntimeError(f"{path}: expected at least {count} exact matches, found {actual}: {old!r}")
    write(path, text.replace(old, new, count))


def remove_lines_containing(path: str, needles: tuple[str, ...]) -> None:
    lines = read(path).splitlines(keepends=True)
    kept = [line for line in lines if not any(needle in line for needle in needles)]
    write(path, "".join(kept))


# 1. Remove the misleading external quality-tier contract end-to-end.
schema_path = ROOT / "open-model-market/execution-ticket.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))
schema["properties"].pop("quality_tier", None)
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

replace_exact(
    "open-model-market/v5_issue_ticket.py",
    '    if path == "quality_tier":\n        return "quality_tier must be budget, value, or quality."\n',
    "",
)
replace_exact(
    "open-model-market/v5_issue_ticket.py",
    '    quality_tier = packet.get("quality_tier", "value")\n    if not isinstance(quality_tier, str):\n        quality_tier = "value"\n',
    "",
)
remove_lines_containing(
    "open-model-market/v5_issue_ticket.py",
    ('"quality_tier": quality_tier', '"quality_tier",', '"quality_tier": validated["quality_tier"]'),
)

remove_lines_containing(
    "open-model-market/v5_production_ticket.py",
    ('parser.add_argument("--quality-tier"', '"--quality-tier",', 'str(args.quality_tier),'),
)
remove_lines_containing(
    "open-model-market/v5_pipeline.py",
    ('parser.add_argument("--quality-tier"',),
)

# Ticket gate signatures, validation, CLI, and evidence must not carry a fake preference.
gate = read("open-model-market/v5_ticket_gate.py")
gate = gate.replace("    expected_quality_tier: str,\n", "")
quality_block = '''    _require(
        errors,
        str(ticket.get("quality_tier") or "") == expected_quality_tier,
        "ticket quality tier differs from workflow expectation",
    )
    _require(
        errors,
        str(status.get("quality_tier") or "") == expected_quality_tier,
        "status quality tier differs from workflow expectation",
    )
    _require(
        errors,
        expected_quality_tier == "value",
        "quality tier must remain value",
    )
'''
if quality_block not in gate:
    raise RuntimeError("v5_ticket_gate.py: quality validation block not found")
gate = gate.replace(quality_block, "", 1)
gate = gate.replace('            "quality_tier": expected_quality_tier,\n', "")
gate = gate.replace('    parser.add_argument("--expected-quality-tier", required=True)\n', "")
gate = gate.replace('            expected_quality_tier=args.expected_quality_tier,\n', "")
write("open-model-market/v5_ticket_gate.py", gate)

remove_lines_containing(
    ".github/workflows/execution-ticket.yml",
    (
        "quality_tier: ${{ steps.ticket.outputs.quality_tier }}",
        '--expected-quality-tier "${{ needs.admit-ticket.outputs.quality_tier }}"',
        '--quality-tier "${{ needs.admit-ticket.outputs.quality_tier }}"',
    ),
)

# Update ticket-gate tests to the fixed, parameter-free policy.
test_gate = read("tests/test_v5_ticket_gate.py")
test_gate = re.sub(r'^\s*"quality_tier": "value",\n', "", test_gate, flags=re.MULTILINE)
test_gate = test_gate.replace('            expected_quality_tier="value",\n', "")
write("tests/test_v5_ticket_gate.py", test_gate)

# Authoritative delegation instructions must not tell GPTs to submit the removed field.
delegation = read("open-model-market/DELEGATION_CONTRACT.md")
delegation = delegation.replace(
    "2. 用户未指定质量档时使用 `quality_tier=value`；",
    "2. 不得提交质量档参数；GPT latest依据本次任务和实时目录直接提出专家图；",
)
write("open-model-market/DELEGATION_CONTRACT.md", delegation)

# 2. Consolidate the one exact heading-normalization implementation.
write(
    "open-model-market/text_normalization.py",
    '''"""Shared deterministic text normalization primitives."""
from __future__ import annotations

import re


def normalize_heading_key(value: str) -> str:
    """Return the canonical key used by all Markdown contract validators."""
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\\d+(?:\\.\\d+)*[\\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\\u4e00-\\u9fff]+", "_", value)
    return value.strip("_")
''',
)

replace_exact(
    "open-model-market/v5_deterministic_answer_normalization.py",
    "from typing import Any, Mapping, Sequence\n",
    "from typing import Any, Mapping, Sequence\n\nfrom text_normalization import normalize_heading_key\n",
)
replace_exact(
    "open-model-market/v5_deterministic_answer_normalization.py",
    '''def _heading_key(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\\d+(?:\\.\\d+)*[\\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\\u4e00-\\u9fff]+", "_", value)
    return value.strip("_")
''',
    "_heading_key = normalize_heading_key\n",
)

replace_exact(
    "open-model-market/v5_task_delivery_contract_impl.py",
    "from typing import Any, Mapping, Sequence\n",
    "from typing import Any, Mapping, Sequence\n\nfrom text_normalization import normalize_heading_key\n",
)
replace_exact(
    "open-model-market/v5_task_delivery_contract_impl.py",
    '''def _normalized_heading(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\\d+(?:\\.\\d+)*[\\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\\u4e00-\\u9fff]+", "_", value)
    return value.strip("_")
''',
    "_normalized_heading = normalize_heading_key\n",
)

replace_exact(
    "open-model-market/v5_runtime.py",
    "from openrouter_api import CHAT_URL, request_json\n",
    "from openrouter_api import CHAT_URL, request_json\nfrom text_normalization import normalize_heading_key\n",
)
replace_exact(
    "open-model-market/v5_runtime.py",
    '''    @staticmethod
    def _normalized_contract_field(value: str) -> str:
        value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
        value = re.sub(r"^\\d+(?:\\.\\d+)*[\\s.)、:：-]+", "", value)
        value = re.sub(r"[^0-9a-z_\\u4e00-\\u9fff]+", "_", value)
        return value.strip("_")
''',
    "    _normalized_contract_field = staticmethod(normalize_heading_key)\n",
)

# 3. Remove the permanently disabled dead workflow.
dead = ROOT / ".github/workflows/v5-one-time-paid-claude-acceptance-20260803.yml"
if dead.exists():
    dead.unlink()

# 4. Make paid acceptance explicit/manual instead of a misleading green no-op.
paid_path = ".github/workflows/v5-final-paid-claude-acceptance-20260803.yml"
paid = read(paid_path)
paid = re.sub(
    r"on:\n  pull_request:\n    paths:\n(?:      - .*\n)+",
    '''on:
  workflow_dispatch:
    inputs:
      confirm:
        description: Type RUN-EXACTLY-ONCE to authorize the bounded paid acceptance
        required: true
        type: string
''',
    paid,
    count=1,
)
paid = re.sub(
    r"    if: >-\n      github\.actor == github\.repository_owner &&\n      github\.event\.pull_request\.head\.ref == 'fix/v5-claude-unified-red-team-cleanup-20260803'\n",
    "    if: github.actor == github.repository_owner\n",
    paid,
    count=1,
)
paid = re.sub(
    r"\n      - name: Explain inactive marker state\n        if: \$\{\{ hashFiles\('\.v5-final-paid-acceptance-20260803'\) == '' \}\}\n        run: echo \"Final paid acceptance marker is absent; no model call is permitted\.\"\n",
    "",
    paid,
    count=1,
)
paid = re.sub(r"\n        if: \$\{\{ hashFiles\('\.v5-final-paid-acceptance-20260803'\) != '' \}\}", "", paid)
paid = paid.replace(
    '          test "$(cat .v5-final-paid-acceptance-20260803)" = "RUN-EXACTLY-ONCE"\n',
    '          test "${{ inputs.confirm }}" = "RUN-EXACTLY-ONCE"\n',
)
paid = paid.replace(
    "          ACCEPTANCE_SHA: ${{ github.event.pull_request.head.sha }}\n",
    "          ACCEPTANCE_SHA: ${{ github.sha }}\n",
)
paid = paid.replace(
    "        if: ${{ always() && hashFiles('.v5-final-paid-acceptance-20260803') != '' }}\n",
    "        if: always()\n",
)
if "pull_request" in paid or "hashFiles(" in paid or ".v5-final-paid-acceptance-20260803" in paid:
    raise RuntimeError("paid acceptance workflow still contains marker/PR no-op logic")
if "workflow_dispatch" not in paid or "inputs.confirm" not in paid:
    raise RuntimeError("paid acceptance workflow was not converted to explicit manual authorization")
write(paid_path, paid)

# 5. Add regression coverage for the cleaned contract and shared primitive.
write(
    "tests/test_v5_complete_cleanup_regression.py",
    '''from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open-model-market"
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from text_normalization import normalize_heading_key


class CompleteCleanupRegressionTests(unittest.TestCase):
    def test_quality_tier_is_not_an_external_contract(self) -> None:
        schema = json.loads((MODULE / "execution-ticket.schema.json").read_text())
        self.assertNotIn("quality_tier", schema["properties"])
        paths = [
            MODULE / "v5_issue_ticket.py",
            MODULE / "v5_production_ticket.py",
            MODULE / "v5_ticket_gate.py",
            ROOT / ".github/workflows/execution-ticket.yml",
        ]
        for path in paths:
            self.assertNotIn("quality_tier", path.read_text(), path)
        pipeline = (MODULE / "v5_pipeline.py").read_text()
        self.assertNotIn('parser.add_argument("--quality-tier"', pipeline)

    def test_paid_acceptance_is_explicit_and_not_a_green_noop(self) -> None:
        dead = ROOT / ".github/workflows/v5-one-time-paid-claude-acceptance-20260803.yml"
        self.assertFalse(dead.exists())
        paid = (ROOT / ".github/workflows/v5-final-paid-claude-acceptance-20260803.yml").read_text()
        self.assertIn("workflow_dispatch", paid)
        self.assertIn("inputs.confirm", paid)
        self.assertNotIn("hashFiles(", paid)
        self.assertNotIn("pull_request", paid)

    def test_heading_normalization_has_one_authoritative_implementation(self) -> None:
        self.assertEqual("最终_建议", normalize_heading_key("## 2. **最终 建议**"))
        source = (MODULE / "text_normalization.py").read_text()
        self.assertEqual(1, source.count("def normalize_heading_key"))
        for name in (
            "v5_deterministic_answer_normalization.py",
            "v5_runtime.py",
            "v5_task_delivery_contract_impl.py",
        ):
            text = (MODULE / name).read_text()
            self.assertIn("normalize_heading_key", text)
            self.assertNotIn('re.sub(r"[`*_~]"', text)

    def test_legacy_local_planning_stack_remains_absent(self) -> None:
        forbidden = (
            "v5_value_optimizer.py",
            "v5_planner.py",
            "v5_planning_runtime.py",
            "v5_constitutional_pipeline.py",
            "v5_cross_endpoint_planner.py",
            "v5_cross_endpoint_planner_impl.py",
            "v5_operational_resilience.py",
            "v5_general_task_planning.py",
            "task_semantic_compiler.py",
            "resource_matrix.py",
            "atomic_work_graph.py",
            "team_policy.json",
        )
        for name in forbidden:
            self.assertFalse((MODULE / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
''',
)

# Final source-level contract checks before tests execute.
for path in (
    "open-model-market/v5_issue_ticket.py",
    "open-model-market/v5_production_ticket.py",
    "open-model-market/v5_ticket_gate.py",
    ".github/workflows/execution-ticket.yml",
    "tests/test_v5_ticket_gate.py",
):
    if "quality_tier" in read(path):
        raise RuntimeError(f"external quality_tier residue remains in {path}")

print("one-time complete remediation applied")
