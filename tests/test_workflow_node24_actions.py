import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

APPROVED = {
    "actions/checkout": "de0fac2e4500dabe0009e67214ff5f5447ce83dd",
    "actions/setup-python": "a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "actions/github-script": "ff4b64fc288a21d5291396a384c1273f032e6333",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "37930b1c2abaa49bbe596cd826c3c89aef350131",
}
USES = re.compile(r"^\s*-?\s*uses:\s*([^@\s]+)@([0-9a-f]{40})", re.MULTILINE)


class TestWorkflowNode24Actions(unittest.TestCase):
    def test_official_actions_use_approved_node24_commits(self):
        violations = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            for action, sha in USES.findall(text):
                expected = APPROVED.get(action)
                if expected is not None and sha != expected:
                    violations.append(
                        f"{path.relative_to(ROOT)}: {action}@{sha} != {expected}"
                    )
        self.assertEqual(violations, [], "\n".join(violations))

    def test_production_workflows_do_not_use_mutable_action_tags(self):
        violations = []
        for name in ("execution-ticket.yml", "promote-v5-production.yml"):
            path = WORKFLOWS / name
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                if stripped.startswith("uses: actions/") or stripped.startswith("- uses: actions/"):
                    reference = stripped.split("@", 1)[-1].split()[0]
                    if not re.fullmatch(r"[0-9a-f]{40}", reference):
                        violations.append(f"{name}:{line_number}: {stripped}")
        self.assertEqual(violations, [], "\n".join(violations))


if __name__ == "__main__":
    unittest.main()
