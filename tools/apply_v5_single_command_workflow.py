#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "execution-ticket.yml"


def once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, found {count}")
    return text.replace(old, new, 1)


def main() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    text = once(
        text,
        '''on:
  issues:
    types: [opened, reopened]
  issue_comment:
    types: [created]
''',
        '''on:
  issue_comment:
    types: [created]
''',
        "single event trigger",
    )
    text = once(
        text,
        '''      - name: Classify event
        id: event
        env:
          ISSUE_ACTION: ${{ github.event.action }}
          COMMENT_BODY: ${{ github.event.comment.body || '' }}
        run: |
          set -euo pipefail
          if [[ "$ISSUE_ACTION" == "opened" || "$ISSUE_ACTION" == "reopened" ]]; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          elif [[ "$COMMENT_BODY" =~ ^/retry-expert-team[[:space:]]+[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$ ]]; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi
''',
        '''      - name: Classify explicit command
        id: event
        env:
          COMMENT_BODY: ${{ github.event.comment.body || '' }}
        run: |
          set -euo pipefail
          if [[ "$COMMENT_BODY" == /run-expert-team\ * || "$COMMENT_BODY" == /retry-expert-team\ * ]]; then
            echo "run=true" >> "$GITHUB_OUTPUT"
          else
            echo "run=false" >> "$GITHUB_OUTPUT"
          fi
''',
        "command classifier",
    )
    checkout = '''        with:
          persist-credentials: false
'''
    pinned = '''        with:
          ref: production
          persist-credentials: false
'''
    if text.count(checkout) != 2:
        raise RuntimeError(f"checkout count: expected 2, found {text.count(checkout)}")
    text = text.replace(checkout, pinned)
    text = once(
        text,
        '''          path: |
            final-attestation.json
            final-status.md
''',
        '''          path: |
            final-attestation.json
            final-status.md
            ticket-artifacts/final-status.json
''',
        "final status json artifact",
    )
    WORKFLOW.write_text(text, encoding="utf-8")
    print("single command production-pinned workflow applied")


if __name__ == "__main__":
    main()
