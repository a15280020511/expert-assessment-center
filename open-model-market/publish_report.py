#!/usr/bin/env python3
"""Split an audited V5 report into GitHub-safe Issue comment files."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, List, Mapping

DEFAULT_MAX_COMMENT_CHARS = 50_000
HEADER_RESERVE_CHARS = 1_200
STRICT_SUCCESS_STATUSES = {"success", "success_retried", "success_recovered"}
DEGRADED_SUCCESS_STATUSES = {"success_degraded"}


def split_text(text: str, payload_limit: int) -> List[str]:
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


def render_comments(
    report: str,
    *,
    run_url: str,
    max_chars: int,
    delivery_status: str = "full_success",
) -> List[str]:
    if max_chars <= HEADER_RESERVE_CHARS + 256:
        raise ValueError("max_chars is too small for a safe report comment")
    run_id = _validated_run_id(run_url)
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
    payloads = split_text(report, max_chars - HEADER_RESERVE_CHARS)
    total = len(payloads)
    degraded = delivery_status == "degraded_success"
    comments: List[str] = []
    for index, payload in enumerate(payloads, 1):
        marker = f"<!-- expert-team-report-run:{run_id}:part:{index:03d} -->"
        disclosure = (
            "- 交付状态：`DEGRADED_SUCCESS`。最低可用覆盖、严格成功内容节点、"
            "不可降级工作、证据、公司唯一性与工具禁令均已通过；缺失、失败和"
            "降级项目必须在报告及 Artifact 中披露。\n"
            if degraded
            else "- 交付状态：`FULL_SUCCESS`。全部节点质量门、输出合同和运行完整性检查通过。\n"
        )
        header = (
            f"{marker}\n"
            f"## EXPERT_TEAM_REPORT {index}/{total}\n\n"
            f"- Run: `{run_url}`\n"
            f"- Source: `expert-team-report.md`\n"
            f"- Report SHA256: `{digest}`\n"
            + disclosure
            + "- 交付范围：最终报告；全部动态节点原始回答、失败、恢复和底层调用证据保存在 Artifact。\n"
            "- 公开提示：本评论位于公开仓库 Issue，任何人可见。\n\n"
            "---\n\n"
        )
        comment = header + payload
        if len(comment) > max_chars:
            raise ValueError(f"rendered comment {index} exceeds {max_chars} characters")
        comments.append(comment)
    return comments


def write_comments(
    report_path: Path,
    output_dir: Path,
    *,
    run_url: str,
    max_chars: int,
    delivery_status: str = "full_success",
) -> dict[str, Any]:
    report = report_path.read_text(encoding="utf-8")
    comments = render_comments(
        report,
        run_url=run_url,
        max_chars=max_chars,
        delivery_status=delivery_status,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for index, comment in enumerate(comments, 1):
        path = output_dir / f"report-comment-{index:03d}.md"
        path.write_text(comment, encoding="utf-8")
        files.append(path.name)
    run_id = _validated_run_id(run_url)
    degraded = delivery_status == "degraded_success"
    manifest = {
        "version": 4,
        "source": str(report_path),
        "run_url": run_url,
        "run_id": run_id,
        "publication_status": (
            "prepared_degraded_success" if degraded else "prepared_full_success"
        ),
        "delivery_status": delivery_status,
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest(),
        "report_chars": len(report),
        "comment_count": len(comments),
        "max_comment_chars": max_chars,
        "files": files,
        "publication_gate": "audited-full-or-degraded-success",
        "report_comment_preparation_status": "PASS",
        "report_comment_preparation_mode": "deterministic-files",
        "issue_context_required": False,
        "degradation_disclosure_required": degraded,
    }
    (output_dir / "report-comments-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _load_mapping(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _node_rows(artifact_root: Path) -> list[Mapping[str, Any]]:
    try:
        value = json.loads(
            (artifact_root / "v5-node-results.json").read_text(encoding="utf-8")
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _delivery_mode(summary: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    status = str(summary.get("status") or result.get("status") or "").casefold()
    completion = str(
        summary.get("completion_mode") or result.get("completion_mode") or ""
    ).casefold()
    quality = str(
        summary.get("quality_status") or result.get("quality_status") or ""
    ).casefold()
    integrity = summary.get("quality_integrity")
    integrity = dict(integrity) if isinstance(integrity, Mapping) else {}
    integrity_status = str(integrity.get("status") or "").upper()
    if (
        status == "success"
        and completion == "full"
        and quality == "full_success"
        and integrity_status == "PASS"
    ):
        return "full_success"
    if (
        status == "success"
        and completion == "degraded"
        and quality == "degraded_success"
        and integrity_status == "DEGRADED"
    ):
        return "degraded_success"
    return ""


def _degraded_delivery_blockers(summary: Mapping[str, Any]) -> list[str]:
    blockers: list[str] = []
    delivery = summary.get("delivery_policy")
    delivery = dict(delivery) if isinstance(delivery, Mapping) else {}
    coverage = summary.get("work_coverage")
    coverage = dict(coverage) if isinstance(coverage, Mapping) else {}
    if delivery.get("allow_degraded_success") is not True:
        blockers.append("degraded-delivery:not-authorized")
    if delivery.get("blockers"):
        blockers.append("degraded-delivery:runtime-blockers-present")
    if delivery.get("missing_non_degradable_work_ids"):
        blockers.append("degraded-delivery:missing-non-degradable-work")
    try:
        observed = float(coverage.get("coverage_ratio") or 0.0)
        minimum = float(coverage.get("minimum_degraded_coverage") or 1.0)
        strict_nodes = int(coverage.get("successful_content_nodes") or 0)
    except (TypeError, ValueError):
        return [*blockers, "degraded-delivery:invalid-coverage-evidence"]
    if observed + 1e-12 < minimum:
        blockers.append("degraded-delivery:insufficient-coverage")
    if strict_nodes < 1:
        blockers.append("degraded-delivery:no-strict-successful-content-node")
    return blockers


def _publication_state_blockers(
    summary: Mapping[str, Any],
    result: Mapping[str, Any],
) -> list[str]:
    mode = _delivery_mode(summary, result)
    if mode == "full_success":
        return []
    if mode == "degraded_success":
        return _degraded_delivery_blockers(summary)
    return [
        "execution-status-or-delivery-mode:not-audited-success"
    ]


def _node_publication_blockers(
    rows: list[Mapping[str, Any]],
    delivery_status: str,
) -> list[str]:
    if not rows:
        return ["node-results:missing"]
    blockers: list[str] = []
    for row in rows:
        node_id = str(row.get("node_id") or "unknown")
        node_status = str(row.get("status") or "")
        contract = row.get("contract")
        contract = dict(contract) if isinstance(contract, Mapping) else {}
        if delivery_status == "full_success":
            if node_status not in STRICT_SUCCESS_STATUSES:
                blockers.append(f"node-status:{node_id}:{node_status or 'missing'}")
            if contract.get("required_fields_complete") is not True:
                blockers.append(f"node-contract-incomplete:{node_id}")
            continue
        if node_status in STRICT_SUCCESS_STATUSES:
            if contract.get("required_fields_complete") is not True:
                blockers.append(f"node-contract-incomplete:{node_id}")
        elif node_status in DEGRADED_SUCCESS_STATUSES:
            if contract.get("required_fields_complete") is not True:
                blockers.append(f"degraded-node-contract-incomplete:{node_id}")
        elif node_status != "failed":
            blockers.append(f"unknown-node-status:{node_id}:{node_status or 'missing'}")
    return blockers


def strict_publication_gate(artifact_root: Path) -> tuple[bool, list[str]]:
    """Compatibility name: accept audited full or audited degraded success."""
    summary = _load_mapping(artifact_root / "v5-execution-summary.json")
    result = _load_mapping(artifact_root / "expert-team-result.json")
    rows = _node_rows(artifact_root)
    delivery_status = _delivery_mode(summary, result)
    blockers = _publication_state_blockers(summary, result)
    blockers.extend(_node_publication_blockers(rows, delivery_status))
    return not blockers, list(dict.fromkeys(blockers))


def write_skip_manifest(
    artifact_root: Path,
    report_path: Path,
    output_dir: Path,
    *,
    run_url: str,
    max_chars: int,
    publication_status: str,
    blockers: list[str],
) -> dict[str, Any]:
    run_id = _validated_run_id(run_url)
    report = report_path.read_text(encoding="utf-8") if report_path.is_file() else ""
    output_dir.mkdir(parents=True, exist_ok=True)
    for stale in output_dir.glob("report-comment-*.md"):
        stale.unlink()
    summary = _load_mapping(artifact_root / "v5-execution-summary.json")
    result = _load_mapping(artifact_root / "expert-team-result.json")
    status = str(result.get("status") or summary.get("status") or "").casefold()
    manifest = {
        "version": 4,
        "source": str(report_path),
        "run_url": run_url,
        "run_id": run_id,
        "publication_status": publication_status,
        "execution_status": status or "unknown",
        "report_sha256": hashlib.sha256(report.encode("utf-8")).hexdigest() if report else None,
        "report_chars": len(report),
        "comment_count": 0,
        "max_comment_chars": max_chars,
        "files": [],
        "publication_gate": "audited-full-or-degraded-success",
        "report_comment_preparation_status": "NOT_APPLICABLE",
        "report_comment_preparation_mode": "deterministic-files",
        "issue_context_required": False,
        "publication_blockers": blockers,
    }
    (output_dir / "report-comments-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def write_failure_skip_manifest(
    artifact_root: Path,
    report_path: Path,
    output_dir: Path,
    *,
    run_url: str,
    max_chars: int,
) -> dict[str, Any]:
    _, blockers = strict_publication_gate(artifact_root)
    if not blockers:
        blockers = ["execution-status:failed"]
    return write_skip_manifest(
        artifact_root,
        report_path,
        output_dir,
        run_url=run_url,
        max_chars=max_chars,
        publication_status="skipped_failed_execution",
        blockers=blockers,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("--report")
    root.add_argument("--output-dir", required=True)
    root.add_argument("--comments-dir")
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
    summary = _load_mapping(artifact_root / "v5-execution-summary.json")
    result = _load_mapping(artifact_root / "expert-team-result.json")
    delivery_status = _delivery_mode(summary, result)
    publishable, blockers = strict_publication_gate(artifact_root)
    if report_path.is_file() and publishable:
        manifest = write_comments(
            report_path,
            comments_path,
            run_url=args.run_url,
            max_chars=args.max_chars,
            delivery_status=delivery_status,
        )
    else:
        status = "skipped_non_audited_execution" if report_path.is_file() else "skipped_failed_execution"
        if not report_path.is_file() and not blockers:
            blockers = ["report:missing"]
        manifest = write_skip_manifest(
            artifact_root,
            report_path,
            comments_path,
            run_url=args.run_url,
            max_chars=args.max_chars,
            publication_status=status,
            blockers=blockers,
        )
    print(json.dumps(manifest, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
