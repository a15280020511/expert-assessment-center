import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
EXPECTED = {
    "actions/checkout": "de0fac2e4500dabe0009e67214ff5f5447ce83dd",
    "actions/setup-python": "a309ff8b426b58ec0e2a45f0f869d46889d02405",
    "actions/github-script": "3a2844b7e9c422d3c10d287c895573f7108da1b3",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "37930b1c2abaa49bbe596cd826c3c89aef350131",
}


class TestWorkflowActionRuntimePins(unittest.TestCase):
    def test_all_managed_actions_use_exact_node24_release_commits(self):
        files = sorted((*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml")))
        self.assertTrue(files)
        observed = {action: [] for action in EXPECTED}
        violations = []
        pattern = re.compile(r"^\s*uses:\s*(actions/[A-Za-z0-9_-]+)@([^\s#]+)")
        for path in files:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = pattern.match(line)
                if not match:
                    continue
                action, ref = match.groups()
                if action not in EXPECTED:
                    continue
                observed[action].append((path.name, line_number, ref))
                if ref != EXPECTED[action]:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line_number}: {action}@{ref}"
                    )
        self.assertFalse(
            violations,
            "managed action is not pinned to the approved Node 24 release commit:\n"
            + "\n".join(violations),
        )
        missing = [action for action, rows in observed.items() if not rows]
        self.assertFalse(missing, "managed action references disappeared: " + ", ".join(missing))

    def test_managed_actions_never_use_mutable_tags(self):
        mutable = []
        pattern = re.compile(r"^\s*uses:\s*(actions/[A-Za-z0-9_-]+)@([^\s#]+)")
        for path in sorted((*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml"))):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                match = pattern.match(line)
                if match and match.group(1) in EXPECTED and not re.fullmatch(r"[0-9a-f]{40}", match.group(2)):
                    mutable.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertFalse(mutable, "mutable action references found:\n" + "\n".join(mutable))


if __name__ == "__main__":
    unittest.main()
