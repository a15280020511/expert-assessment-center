#!/usr/bin/env python3
"""Collect, redact, classify and package GitHub Actions diagnostics."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Mapping

SCHEMA = "workflow-diagnostics-v2"
API_VERSION = "2022-11-28"
FAILURES = {"failure", "cancelled", "timed_out", "action_required", "stale", "startup_failure"}
SECRET = re.compile(r"(?i)(authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|private[_-]?key|password|passwd|sendkey|sckey|secret|token)\s*[:=]\s*([^\s,;]+)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|key|secret|sendkey|sckey)=)[^&#\s]+")
GITHUB_SECRET = re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{12,}\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
KEY_LINE = re.compile(r"(?i)(::error|##\[error\]|error:|exception|traceback|failed|failure|fatal|timed out|timeout|unauthorized|forbidden|rate limit|quota|assertion|warning:|##\[warning\])")
PATTERNS = (
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


def redact(text: str) -> str:
    text = ANSI.sub("", text)
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = SECRET.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = QUERY_SECRET.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
    return GITHUB_SECRET.sub("[REDACTED_GITHUB_TOKEN]", text)


class Client:
    def __init__(self, repository: str, token: str) -> None:
        self.repository = repository
        self.token = token

    def get(self, path: str) -> bytes:
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "workflow-diagnostics",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(100 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as exc:
            body = redact(exc.read(2000).decode("utf-8", errors="replace"))
            raise RuntimeError(f"GitHub API HTTP {exc.code}: {body}") from exc
        if len(data) > 100 * 1024 * 1024:
            raise RuntimeError("GitHub API response exceeded safety limit")
        return data

    def get_json(self, path: str) -> Any:
        data = self.get(path)
        return json.loads(data.decode("utf-8")) if data else None

    def recent_runs(self, cutoff: dt.datetime, limit: int) -> list[Mapping[str, Any]]:
        found: list[Mapping[str, Any]] = []
        for page in range(1, 11):
            payload = self.get_json(f"/repos/{self.repository}/actions/runs?per_page=100&page={page}")
            rows = payload.get("workflow_runs", []) if isinstance(payload, Mapping) else []
            if not rows:
                break
            stop = False
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                created = parse_time(str(row.get("created_at") or now()))
                if created < cutoff:
                    stop = True
                    continue
                found.append(row)
                if len(found) >= limit:
                    return found
            if stop:
                break
        return found

    def jobs(self, run_id: int) -> list[Mapping[str, Any]]:
        payload = self.get_json(f"/repos/{self.repository}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
        rows = payload.get("jobs", []) if isinstance(payload, Mapping) else []
        return [row for row in rows if isinstance(row, Mapping)]


def duration(start: Any, end: Any) -> float | None:
    if not start or not end:
        return None
    try:
        return round((parse_time(str(end)) - parse_time(str(start))).total_seconds(), 3)
    except ValueError:
        return None


def compact_jobs(jobs: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for job in jobs:
        steps = []
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
    candidates = []
    for category, signals in PATTERNS:
        hits = [signal for signal in signals if signal in text]
        if hits:
            candidates.append({"category": category, "signals": hits[:8]})
    if conclusion in {"cancelled", "timed_out", "startup_failure"}:
        candidates.insert(0, {"category": "timeout_or_cancellation", "signals": [conclusion]})
    if not candidates:
        candidates = [{"category": "unknown", "signals": ["no known signature matched"]}]
    primary = candidates[0]["category"]
    failed_steps = [
        {"job_id": job["id"], "job_name": job["name"], "step_number": step["number"], "step_name": step["name"], "conclusion": step["conclusion"]}
        for job in jobs for step in job["steps"] if step.get("conclusion") in FAILURES
    ]
    normalized = "\n".join(re.sub(r"\b[0-9a-f]{12,64}\b", "<ID>", line.lower())[:500] for line in lines[:20])
    retryable = primary in {"rate_limit_or_quota", "timeout_or_cancellation", "network_dns_tls", "provider_or_model"}
    return {
        "primary_category": primary, "candidates": candidates[:5], "failed_steps": failed_steps,
        "failure_fingerprint": hashlib.sha256((primary + "\n" + normalized).encode()).hexdigest(),
        "retry_guidance": {"retryable": retryable, "max_attempts": 2 if retryable else 0,
                           "strategy": "bounded_retry_with_backoff" if retryable else "fix_root_cause_before_retry"},
    }


def extract_logs(data: bytes, target: Path) -> tuple[list[Path], list[str]]:
    target.mkdir(parents=True, exist_ok=True)
    paths, notes = [], []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for index, info in enumerate(archive.infolist()):
            if info.is_dir():
                continue
            name = Path(info.filename).name or f"log-{index}.txt"
            content = redact(archive.read(info).decode("utf-8", errors="replace"))
            encoded = content.encode("utf-8")
            if len(encoded) > 8 * 1024 * 1024:
                encoded = encoded[:8 * 1024 * 1024] + b"\n[TRUNCATED]\n"
                notes.append(f"{name} truncated")
            path = target / f"{index:03d}-{name}"
            path.write_bytes(encoded)
            paths.append(path)
    return paths, notes


def manifest(root: Path) -> None:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            files.append({"path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    dump(root / "manifest.json", {"schema_version": SCHEMA, "created_at": now(), "files": files,
                                  "security": {"secret_values_included": False, "logs_redacted": True}})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY", ""))
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN", ""))
    parser.add_argument("--center", default=os.getenv("DIAGNOSTIC_CENTER", "unknown"))
    parser.add_argument("--output-dir", default="diagnostic-bundle")
    parser.add_argument("--lookback-hours", type=float, default=2.0)
    parser.add_argument("--max-runs", type=int, default=200)
    parser.add_argument("--exclude-workflow-path", action="append", default=[])
    args = parser.parse_args()
    if not args.repository or not args.token or not (0.1 <= args.lookback_hours <= 168) or not (1 <= args.max_runs <= 1000):
        print("::error::invalid repository, token, or sweep bounds", file=sys.stderr)
        return 2
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    cutoff_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=args.lookback_hours)
    client = Client(args.repository, args.token)
    index = []
    try:
        for run in client.recent_runs(cutoff_dt, args.max_runs):
            if str(run.get("path") or "") in set(args.exclude_workflow_path):
                continue
            run_id = int(run.get("id") or 0)
            run_dir = root / "runs" / str(run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            jobs = compact_jobs(client.jobs(run_id))
            conclusion = str(run.get("conclusion") or "unknown")
            record = {
                "id": run_id, "name": run.get("name"), "path": run.get("path"), "event": run.get("event"),
                "status": run.get("status"), "conclusion": conclusion, "run_attempt": run.get("run_attempt"),
                "head_branch": run.get("head_branch"), "head_sha": run.get("head_sha"),
                "created_at": run.get("created_at"), "run_started_at": run.get("run_started_at"),
                "updated_at": run.get("updated_at"), "html_url": run.get("html_url"),
                "duration_seconds": duration(run.get("run_started_at"), run.get("updated_at")),
                "job_count": len(jobs), "diagnostic_path": run_dir.relative_to(root).as_posix(),
            }
            dump(run_dir / "run.json", record)
            (run_dir / "jobs.jsonl").write_text("".join(json.dumps(job, ensure_ascii=False, sort_keys=True) + "\n" for job in jobs), encoding="utf-8")
            if conclusion in FAILURES:
                lines, notes, paths = [], [], []
                try:
                    paths, notes = extract_logs(client.get(f"/repos/{args.repository}/actions/runs/{run_id}/logs"), run_dir / "redacted-logs")
                    seen = set()
                    for path in paths:
                        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                            if KEY_LINE.search(line) and line not in seen:
                                seen.add(line)
                                lines.append(line[:2000])
                                if len(lines) >= 300:
                                    break
                except Exception as exc:
                    notes.append(redact(str(exc)))
                (run_dir / "key-lines.jsonl").write_text("".join(json.dumps({"line": line}, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")
                failure = classify(lines, conclusion, jobs)
                failure.update({"schema_version": SCHEMA, "run_id": run_id, "workflow": run.get("name"),
                                "conclusion": conclusion, "notes": notes, "redacted_log_file_count": len(paths)})
                dump(run_dir / "failure.json", failure)
            index.append(record)
        index.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
        failures = [row for row in index if row["conclusion"] in FAILURES]
        dump(root / "diagnostic-index.json", {"schema_version": SCHEMA, "created_at": now(),
                                              "repository": args.repository, "center": args.center,
                                              "cutoff": cutoff_dt.replace(microsecond=0).isoformat(),
                                              "run_count": len(index), "runs": index,
                                              "security": {"secret_values_included": False}})
        summary = ["# Workflow diagnostic sweep", "", f"- Repository: `{args.repository}`", f"- Center: `{args.center}`",
                   f"- Runs inspected: **{len(index)}**", f"- Non-success runs: **{len(failures)}**", "",
                   "Reading order: `diagnostic-index.json` → `runs/<run_id>/failure.json` → `key-lines.jsonl` → `jobs.jsonl` → `redacted-logs/` → `manifest.json`", ""]
        (root / "summary.md").write_text("\n".join(summary), encoding="utf-8")
        manifest(root)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
                handle.write("\n".join(summary))
        return 0
    except Exception as exc:
        dump(root / "collector-error.json", {"schema_version": SCHEMA, "created_at": now(),
                                              "error_type": type(exc).__name__, "message": redact(str(exc)),
                                              "security": {"secret_values_included": False}})
        manifest(root)
        print(f"::error::{redact(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
