#!/usr/bin/env python3
"""Split a completed judge report into GitHub-safe Issue comment files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import List

DEFAULT_MAX_COMMENT_CHARS = 50_000
HEADER_RESERVE_CHARS = 1_000


def split_text(text: str, payload_limit: int) -> List[str]:
    """Split UTF-8 text by characters, preferring newline boundaries."""
    if payload_limit < 256:
        raise ValueError("payload_limit must be at least 256 characters")
    if not text:
        raise ValueError("report is empty")

    chunks: List[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > payload_limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:payload_limit])
            line = line[payload_limit:]
        if len(current) + len(line) > payload_limit:
            if current:
                chunks.append(current)
            current = line
        else:
            current += line
    if current:
        chunks.append(current)
    return chunks


def render_comments(report: str, *, run_url: str, max_chars: int) -> List[str]:
    if max_chars <= HEADER_RESERVE_CHARS + 256:
        raise ValueError("max_chars is too small for a safe report comment")
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    payloads = split_text(report, max_chars - HEADER_RESERVE_CHARS)
    total = len(payloads)
    comments: List[str] = []
    run_id = run_url.rstrip("/").split("/")[-1] if run_url else "unknown"

    for index, payload in enumerate(payloads, 1):
        marker = f"<!-- expert-team-report-run:{run_id}:part:{index:03d} -->"
        header = (
            f"{marker}\n"
            f"## EXPERT_TEAM_REPORT {index}/{total}\n\n"
            f"- Run: `{run_url or 'unknown'}`\n"
            f"- Source: `expert-team-report.md`\n"
            f"- Report SHA256: `{digest}`\n"
            "- 交付范围：完整裁判报告；三名专家原始回答和底层调用证据仍保存在 Artifact。\n"
            "- 公开提示：本评论位于公开仓库 Issue，任何人可见。\n\n"
            "---\n\n"
        )
        comment = header + payload
        if len(comment) > max_chars:
            raise ValueError(f"rendered comment {index} exceeds {max_chars} characters")
        comments.append(comment)
    return comments


def write_comments(report_path: Path, output_dir: Path, *, run_url: str, max_chars: int) -> dict:
    report = report_path.read_text(encoding="utf-8")
    comments = render_comments(report, run_url=run_url, max_chars=max_chars)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for index, comment in enumerate(comments, 1):
        path = output_dir / f"report-comment-{index:03d}.md"
        path.write_text(comment, encoding="utf-8")
        files.append(path.name)

    manifest = {
        "version": 1,
        "source": str(report_path),
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "report_chars": len(report),
        "comment_count": len(comments),
        "max_comment_chars": max_chars,
        "files": files,
    }
    (output_dir / "report-comments-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--report", required=True)
    root.add_argument("--output-dir", required=True)
    root.add_argument("--run-url", default="")
    root.add_argument("--max-chars", type=int, default=DEFAULT_MAX_COMMENT_CHARS)
    return root


def main() -> int:
    args = parser().parse_args()
    manifest = write_comments(
        Path(args.report),
        Path(args.output_dir),
        run_url=args.run_url,
        max_chars=args.max_chars,
    )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
