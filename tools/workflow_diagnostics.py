#!/usr/bin/env python3
"""Analyze locally collected GitHub Actions metadata and redacted log archives.

This module performs no network access. The workflow fetches same-repository
Actions evidence with GitHub CLI before invoking this analyzer.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "workflow-diagnostics-v2"
FAILURES = {"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"}
MAX_REDACTED_FILE_BYTES = 8 * 1024 * 1024
MAX_KEY_LINES = 300
SECRET = re.compile(r"(?i)(authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|sendkey|sckey|secret|token)\s*[:=]\s*([^\s,;]+)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|secret|sendkey|sckey)=)[^&#\s]+")
GITHUB_SECRET = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
KEY_LINE = re.compile(r"(?i)(::error|##\[error\]|error:|exception|traceback|failed|failure|fatal|timed out|timeout|unauthorized|forbidden|rate limit|quota|assertion|warning:|##\[warning\])")
PATTERNS: Sequence[tuple[str, Sequence[str]]] = (
    ("secret_or_auth", ("missing or unavailable", "bad credentials", "unauthorized", "forbidden", "permission denied", "resource not accessible by integration", "http 401", "http 403")),
    ("rate_limit_or_quota", ("rate limit", "too many requests", "quota exceeded", "http 429")),
    ("timeout_or_cancellation", ("timed out", "timeout", "cancelled", "canceled", "deadline exceeded")),
    ("network_dns_tls", ("name resolution", "connection reset", "connection refused", "connection timed out", "certificate verify failed", "network is unreachable", "could not resolve host")),
    ("dependency_install", ("no matching distribution", "resolutionimpossible", "could not build wheels", "failed building wheel", "module not found", "modulenotfounderror", "dependency conflict")),
    ("schema_or_input", ("schema validation", "validationerror", "invalid json", "jsondecodeerror", "required property", "invalid ticket")),
    ("artifact_or_attestation", ("artifact", "attestation", "digest mismatch", "sha-256 mismatch", "manifest mismatch", "failed to upload artifact", "failed to download artifact")),
    ("provider_or_model", ("provider error", "model not found", "model unavailable", "context length", "content filter", "openrouter", "anthropic", "openai", "deepseek")),
    ("test_or_assertion", ("assertionerror", "tests failed", "failed test", "pytest")),
    ("resource_exhaustion", ("no space left", "out of memory", "memoryerror", "exit code 137", "disk quota")),
    ("syntax_or_runtime", ("syntaxerror", "typeerror", "valueerror", "keyerror", "attributeerror", "runtimeerror", "traceback (most recent call last)")),
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def redact(text: str) -> str:
    text = ANSI.sub("", text)
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = QUERY_SECRET.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return GITHUB_SECRET.sub("[REDACTED_GITHUB_TOKEN]", text)


def read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"diagnostic JSON file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return json.loads(text)


def duration(start: Any, end: Any) -> float | None:
    if not start or not end:
        return None
    try:
        return round((parse_time(str(end)) - parse_time(str(start))).total_seconds(), 3)
    except (TypeError, ValueError):
        return None


def select_runs(payload: Any, *, lookback_hours: float, max_runs: int, excluded: set[str]) -> list[dict[str, Any]]:
    rows = payload.get("workflow_runs", []) if isinstance(payload, Mapping) else []
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=lookback_hours)
    selected: list[dict[str, Any]] = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, Mapping):
            continue
        created = parse_time(str(raw.get("created_at") or now()))
        if created < cutoff or str(raw.get("path") or "") in excluded:
            continue
        selected.append({
            "id": int(raw.get("id") or 0), "name": raw.get("name"), "path": raw.get("path"),
            "event": raw.get("event"), "status": raw.get("status"), "conclusion": raw.get("conclusion"),
            "run_attempt": raw.get("run_attempt"), "head_branch": raw.get("head_branch"),
            "head_sha": raw.get("head_sha"), "created_at": raw.get("created_at"),
            "run_started_at": raw.get("run_started_at"), "updated_at": raw.get("updated_at"),
            "html_url": raw.get("html_url"),
        })
        if len(selected) >= max_runs:
            break
    return selected


def plan(args: argparse.Namespace) -> int:
    selected = select_runs(
        read_json(Path(args.runs_json)), lookback_hours=args.lookback_hours,
        max_runs=args.max_runs, excluded=set(args.exclude_workflow_path),
    )
    dump(Path(args.output), {
        "schema_version": SCHEMA, "created_at": now(), "repository": args.repository,
        "runs": selected,
        "failure_run_ids": [row["id"] for row in selected if str(row.get("conclusion") or "") in FAILURES],
    })
    return 0


def compact_jobs(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("jobs", []) if isinstance(payload, Mapping) else []
    output: list[dict[str, Any]] = []
    for job in rows if isinstance(rows, list) else []:
        if not isinstance(job, Mapping):
            continue
        steps: list[dict[str, Any]] = []
        for step in job.get("steps", []) if isinstance(job.get("steps"), list) else []:
            if isinstance(step, Mapping):
                steps.append({
                    "number": step.get("number"), "name": step.get("name"),
                    "status": step.get("status"), "conclusion": step.get("conclusion"),
                    "started_at": step.get("started_at"), "completed_at": step.get("completed_at"),
                    "duration_seconds": duration(step.get("started_at"), step.get("completed_at")),
                })
        output.append({
            "id": job.get("id"), "name": job.get("name"), "status": job.get("status"),
            "conclusion": job.get("conclusion"), "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
            "duration_seconds": duration(job.get("started_at"), job.get("completed_at")),
            "html_url": job.get("html_url"), "steps": steps,
        })
    return output


def classify(lines: list[str], conclusion: str, jobs: list[dict[str, Any]]) -> dict[str, Any]:
    text = "\n".join(lines).lower()
    candidates: list[dict[str, Any]] = []
    for category, signals in PATTERNS:
        hits = [signal for signal in signals if signal in text]
        if hits:
            candidates.append({"category": category, "signals": hits[:8]})
    if conclusion in {"cancelled", "timed_out", "startup_failure"}:
        candidates.insert(0, {"category": "timeout_or_cancellation", "signals": [conclusion]})
    if not candidates:
        candidates = [{"category": "unknown", "signals": ["no known signature matched"]}]
    primary = str(candidates[0]["category"])
    failed_steps = [
        {"job_id": job.get("id"), "job_name": job.get("name"), "step_number": step.get("number"),
         "step_name": step.get("name"), "conclusion": step.get("conclusion")}
        for job in jobs for step in job.get("steps", []) if step.get("conclusion") in FAILURES
    ]
    normalized = "\n".join(re.sub(r"\b[0-9a-f]{12,64}\b", "<ID>", line.lower())[:500] for line in lines[:20])
    retryable = primary in {"rate_limit_or_quota", "timeout_or_cancellation", "network_dns_tls", "provider_or_model"}
    return {
        "primary_category": primary, "candidates": candidates[:5], "failed_steps": failed_steps,
        "failure_fingerprint": hashlib.sha256((primary + "\n" + normalized).encode()).hexdigest(),
        "retry_guidance": {"retryable": retryable, "max_attempts": 2 if retryable else 0,
                           "strategy": "bounded_retry_with_backoff" if retryable else "fix_root_cause_before_retry"},
    }


def extract_logs(zip_path: Path, target: Path) -> tuple[list[Path], list[str]]:
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    notes: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as archive:
        for index, info in enumerate(archive.infolist()):
            if info.is_dir():
                continue
            name = Path(info.filename).name or f"log-{index}.txt"
            encoded = redact(archive.read(info).decode("utf-8", errors="replace")).encode("utf-8")
            if len(encoded) > MAX_REDACTED_FILE_BYTES:
                encoded = encoded[:MAX_REDACTED_FILE_BYTES] + b"\n[TRUNCATED]\n"
                notes.append(f"{name} truncated")
            path = target / f"{index:03d}-{name}"
            path.write_bytes(encoded)
            paths.append(path)
    return paths, notes


def key_lines(paths: Sequence[Path]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            normalized = re.sub(r"\s+", " ", line).strip().lower()
            if not KEY_LINE.search(line) or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(line[:2000])
            if len(rows) >= MAX_KEY_LINES:
                return rows
    return rows


def build_manifest(root: Path) -> None:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    dump(root / "manifest.json", {
        "schema_version": SCHEMA, "created_at": now(), "files": files,
        "security": {"secret_values_included": False, "logs_redacted": True,
                     "network_access_performed_by_analyzer": False},
    })


def analyze(args: argparse.Namespace) -> int:
    input_dir = Path(args.input_dir)
    plan_data = read_json(input_dir / "plan.json")
    selected = plan_data.get("runs", []) if isinstance(plan_data, Mapping) else []
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for run in selected if isinstance(selected, list) else []:
        if not isinstance(run, Mapping):
            continue
        run_id = int(run.get("id") or 0)
        run_dir = output / "runs" / str(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        jobs_path = input_dir / "jobs" / f"{run_id}.json"
        jobs = compact_jobs(read_json(jobs_path)) if jobs_path.exists() else []
        record = dict(run)
        record.update({
            "duration_seconds": duration(run.get("run_started_at"), run.get("updated_at")),
            "job_count": len(jobs),
            "failed_job_count": sum(1 for job in jobs if job.get("conclusion") in FAILURES),
            "diagnostic_path": run_dir.relative_to(output).as_posix(),
        })
        dump(run_dir / "run.json", record)
        jsonl(run_dir / "jobs.jsonl", jobs)
        conclusion = str(run.get("conclusion") or "unknown")
        if conclusion in FAILURES:
            notes: list[str] = []
            log_paths: list[Path] = []
            zip_path = input_dir / "logs" / f"{run_id}.zip"
            error_path = input_dir / "logs" / f"{run_id}.error.txt"
            if zip_path.exists() and zip_path.stat().st_size:
                try:
                    log_paths, notes = extract_logs(zip_path, run_dir / "redacted-logs")
                except (zipfile.BadZipFile, OSError) as exc:
                    notes.append(f"local log archive could not be parsed: {type(exc).__name__}: {exc}")
            elif error_path.exists():
                notes.append(redact(error_path.read_text(encoding="utf-8", errors="replace"))[:2000])
            else:
                notes.append("log archive was not collected")
            lines = key_lines(log_paths)
            jsonl(run_dir / "key-lines.jsonl", ({"line": line} for line in lines))
            failure = classify(lines, conclusion, jobs)
            failure.update({
                "schema_version": SCHEMA, "run_id": run_id, "workflow": run.get("name"),
                "conclusion": conclusion, "notes": notes,
                "redacted_log_file_count": len(log_paths), "key_line_count": len(lines),
            })
            dump(run_dir / "failure.json", failure)
        index.append(record)
    index.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    failures = [row for row in index if str(row.get("conclusion") or "") in FAILURES]
    dump(output / "diagnostic-index.json", {
        "schema_version": SCHEMA, "created_at": now(), "repository": args.repository,
        "center": args.center, "run_count": len(index), "runs": index,
        "collector": {"network_access_performed_by_analyzer": False, "raw_environment_collected": False,
                      "secret_values_included": False},
    })
    summary = [
        "# Workflow diagnostic sweep", "", f"- Repository: `{args.repository}`",
        f"- Center: `{args.center}`", f"- Runs inspected: **{len(index)}**",
        f"- Non-success runs: **{len(failures)}**", "",
        "Reading order: `diagnostic-index.json` → `runs/<run_id>/failure.json` → `key-lines.jsonl` → `jobs.jsonl` → `redacted-logs/` → `manifest.json`", "",
        "The Python analyzer performed no network access; GitHub CLI collected same-repository metadata in a separate workflow step.", "",
    ]
    (output / "summary.md").write_text("\n".join(summary), encoding="utf-8")
    build_manifest(output)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(summary))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--runs-json", required=True)
    p.add_argument("--repository", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--lookback-hours", type=float, default=2.0)
    p.add_argument("--max-runs", type=int, default=200)
    p.add_argument("--exclude-workflow-path", action="append", default=[])
    a = sub.add_parser("analyze")
    a.add_argument("--input-dir", required=True)
    a.add_argument("--repository", required=True)
    a.add_argument("--center", required=True)
    a.add_argument("--output-dir", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "plan":
        if not (0.1 <= args.lookback_hours <= 168) or not (1 <= args.max_runs <= 200):
            print("::error::invalid sweep bounds", file=sys.stderr)
            return 2
        return plan(args)
    return analyze(args)


if __name__ == "__main__":
    raise SystemExit(main())
