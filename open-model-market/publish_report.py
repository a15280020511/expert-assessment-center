#!/usr/bin/env python3
"""Split a completed V5 report into GitHub-safe Issue comment files."""
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


def _validated_run_id(run_url: str) -> str:
    normalized = str(run_url or "").strip().rstrip("/")
    marker = "/actions/runs/"
    if marker not in normalized:
        raise ValueError("run_url must identify a GitHub Actions run")
    run_id = normalized.rsplit(marker, 1)[-1]
    if not run_id.isdigit():
        raise ValueError("run_url must end with a numeric GitHub Actions run id")
    return run_id


def render_comments(report: str, *, run_url: str, max_chars: int) -> List[str]:
    if max_chars <= HEADER_RESERVE_CHARS + 256:
        raise ValueError("max_chars is too small for a safe report comment")
    run_id = _validated_run_id(run_url)
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    payloads = split_text(report, max_chars - HEADER_RESERVE_CHARS)
    total = len(payloads)
    comments: List[str] = []

    for index, payload in enumerate(payloads, 1):
        marker = f"<!-- expert-team-report-run:{run_id}:part:{index:03d} -->"
        header = (
            f"{marker}\n"
            f"## EXPERT_TEAM_REPORT {index}/{total}\n\n"
            f"- Run: `{run_url}`\n"
            f"- Source: `expert-team-report.md`\n"
            f"- Report SHA256: `{digest}`\n"
            "- 交付范围：完整V5最终报告；全部动态节点原始回答和底层调用证据保存在 Artifact。\n"
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

    run_id = _validated_run_id(run_url)
    manifest = {
        "version": 2,
        "source": str(report_path),
        "run_url": run_url,
        "run_id": run_id,
        "publication_status": "prepared",
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


def _load_mapping(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def write_failure_skip_manifest(
    artifact_root: Path,
    report_path: Path,
    output_dir: Path,
    *,
    run_url: str,
    max_chars: int,
) -> dict:
    """Record intentional report omission after a failed zero/dry execution.

    A failed execution has no business report to publish. Treating that absence
    as a publisher crash obscures the primary failure and breaks post-upload
    attestation. This manifest is evidence that publication was deliberately
    skipped, not silently lost.
    """
    result = _load_mapping(artifact_root / "expert-team-result.json")
    summary = _load_mapping(artifact_root / "v5-execution-summary.json")
    status = str(result.get("status") or summary.get("status") or "").casefold()
    if status not in {"failed", "failure"}:
        raise FileNotFoundError(report_path)
    run_id = _validated_run_id(run_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 2,
        "source": str(report_path),
        "run_url": run_url,
        "run_id": run_id,
        "publication_status": "skipped_failed_execution",
        "execution_status": status,
        "report_sha256": None,
        "report_chars": 0,
        "comment_count": 0,
        "max_comment_chars": max_chars,
        "files": [],
    }
    (output_dir / "report-comments-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument(
        "--report",
        help="Explicit final report path. Optional with the production --comments-dir form.",
    )
    root.add_argument(
        "--output-dir",
        required=True,
        help="Legacy comment destination, or production artifact root when --comments-dir is used.",
    )
    root.add_argument(
        "--comments-dir",
        help="Production comment destination. The report defaults to <output-dir>/v5-final-report.md.",
    )
    root.add_argument("--run-url", default="")
    root.add_argument("--max-chars", type=int, default=DEFAULT_MAX_COMMENT_CHARS)
    return root


def resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    artifact_root = Path(args.output_dir)
    if args.comments_dir:
        report_path = Path(args.report) if args.report else artifact_root / "v5-final-report.md"
        comments_path = Path(args.comments_dir)
    else:
        if not args.report:
            raise ValueError("--report is required unless --comments-dir is supplied")
        report_path = Path(args.report)
        comments_path = artifact_root
    return report_path, comments_path


def main() -> int:
    args = parser().parse_args()
    artifact_root = Path(args.output_dir)
    report_path, comments_path = resolve_paths(args)
    if report_path.is_file():
        manifest = write_comments(
            report_path,
            comments_path,
            run_url=args.run_url,
            max_chars=args.max_chars,
        )
    else:
        manifest = write_failure_skip_manifest(
            artifact_root,
            report_path,
            comments_path,
            run_url=args.run_url,
            max_chars=args.max_chars,
        )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
