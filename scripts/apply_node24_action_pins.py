from __future__ import annotations

import re
from pathlib import Path

WORKFLOW_ROOT = Path(".github/workflows")
TARGETS = {
    "actions/checkout": (
        "de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        "v6.0.2",
    ),
    "actions/setup-python": (
        "a309ff8b426b58ec0e2a45f0f869d46889d02405",
        "v6.2.0",
    ),
    "actions/github-script": (
        "3a2844b7e9c422d3c10d287c895573f7108da1b3",
        "v9.0.0",
    ),
    "actions/upload-artifact": (
        "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "v7.0.1",
    ),
    "actions/download-artifact": (
        "37930b1c2abaa49bbe596cd826c3c89aef350131",
        "v7.0.0",
    ),
}


def patch_file(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8")
    original = text
    counts: dict[str, int] = {}
    for action, (sha, version) in TARGETS.items():
        pattern = re.compile(
            rf"(?m)^(?P<prefix>\s*uses:\s*){re.escape(action)}@[^\s#]+(?:\s*#.*)?$"
        )
        text, count = pattern.subn(
            rf"\g<prefix>{action}@{sha} # {version}",
            text,
        )
        counts[action] = count
    if text != original:
        path.write_text(text, encoding="utf-8")
    return counts


def main() -> int:
    if not WORKFLOW_ROOT.is_dir():
        raise SystemExit("workflow directory is missing")
    totals = {action: 0 for action in TARGETS}
    files = sorted((*WORKFLOW_ROOT.glob("*.yml"), *WORKFLOW_ROOT.glob("*.yaml")))
    if not files:
        raise SystemExit("no workflow files found")
    for path in files:
        counts = patch_file(path)
        for action, count in counts.items():
            totals[action] += count
    missing = [action for action, count in totals.items() if count == 0]
    if missing:
        raise SystemExit("expected action references were not found: " + ", ".join(missing))
    for action, count in totals.items():
        print(f"{action}: {count} references pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
