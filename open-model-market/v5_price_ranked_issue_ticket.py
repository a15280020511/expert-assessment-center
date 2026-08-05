#!/usr/bin/env python3
"""Price-ranked admission wrapper preserving V5 ticket security controls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import v5_issue_ticket as legacy

LEGACY_GOVERNANCE_RESERVE_REASON = (
    "approved recovery calls must leave at least one initial expert call "
    "after three governance calls"
)

DELEGATION_NOTICE = (
    "委托边界：专家编组由本仓库的确定性 Python 选模器完成；只从合格模型目录中按"
    "预计任务成本由低到高选择，并强制模型公司互异。NetworkX 只负责验证和编排"
    "有向无环执行图。Claude、GPT 选模与任何其他治理模型调用均已关闭。"
    "专家禁止外部工具；网页GPT只负责忠实提交、监控、取回和转述。"
)


def _rewrite_outputs(status: Mapping[str, Any]) -> None:
    legacy._rewrite_outputs(status)  # noqa: SLF001


def _postprocess(root: Path) -> dict[str, Any]:
    path = root / "ticket-status.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    total = int(status.get("calls") or 0)
    recovery = int(status.get("maximum_recovery_calls") or 0)
    errors = [str(value) for value in status.get("errors", [])]
    legacy_only_rejection = (
        status.get("accepted") is not True
        and errors == [LEGACY_GOVERNANCE_RESERVE_REASON]
        and total - recovery >= 3
    )
    if legacy_only_rejection:
        packet = json.loads((root / "ticket.json").read_text(encoding="utf-8"))
        status["accepted"] = True
        status["errors"] = []
        status["reason"] = ""
        projected = legacy._substantive_task_text(packet)  # noqa: SLF001
        if projected.startswith(legacy.DELEGATION_NOTICE):
            projected = DELEGATION_NOTICE + projected[len(legacy.DELEGATION_NOTICE) :]
        (root / "task.txt").write_text(projected, encoding="utf-8")

    if status.get("accepted") is True:
        if total - recovery < 3:
            status["accepted"] = False
            status["errors"] = [
                "price-ranked expert team requires at least three initial calls"
            ]
            status["reason"] = status["errors"][0]
        else:
            status.update(
                {
                    "required_model_calls": 3,
                    "maximum_initial_calls": total - recovery,
                    "analysis_owner": "github-price-ranked-networkx-expert-graph",
                    "runtime_version": "v5-price-ranked-runtime-1",
                    "claude_red_team_calls": 0,
                    "claude_is_advisory_only": False,
                    "claude_gatekeeping_allowed": False,
                    "claude_mechanism_enabled": False,
                    "governance_model_calls": 0,
                    "call_policy": (
                        "approved-total-includes-experts-and-recovery-only"
                    ),
                    "reason": (
                        "explicit command, ticket, authorization, uniqueness, "
                        "expert reserve, recovery reserve, exact provider lock, "
                        "and fail-closed policy accepted"
                    ),
                }
            )
            task_path = root / "task.txt"
            if task_path.is_file():
                text = task_path.read_text(encoding="utf-8")
                if text.startswith(legacy.DELEGATION_NOTICE):
                    text = DELEGATION_NOTICE + text[len(legacy.DELEGATION_NOTICE) :]
                task_path.write_text(text, encoding="utf-8")
    path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _rewrite_outputs(status)
    return status


def prepare(args: argparse.Namespace) -> int:
    legacy.prepare(args)
    _postprocess(Path(args.output_dir))
    return 0


def render(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    status = json.loads((root / "ticket-status.json").read_text(encoding="utf-8"))
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = (
            "EXECUTION_RETRY_ACCEPTED"
            if status.get("is_retry")
            else "EXECUTION_ACCEPTED"
        )
        identity = (
            f"- RETRY_ID: `{status.get('retry_id')}`\n"
            if status.get("is_retry")
            else f"- EXECUTION_ID: `{status.get('execution_id')}`\n"
        )
        anomaly = status.get("cost_anomaly_usd")
        anomaly_text = (
            f"`${anomaly}`" if anomaly is not None else "`未配置固定美元阈值`"
        )
        text = (
            f"## {heading}\n\n"
            "GitHub Issue Runner 已接收唯一价格优先专家任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + identity
            + "- 编组：`合格模型目录 → 预计任务成本升序 → 模型公司去重 → NetworkX DAG`\n"
            + "- 组织：`并行独立分析 → 交叉审查 → 最终综合`\n"
            + "- Claude机制：`关闭；调用数0`\n"
            + "- GPT选模：`关闭；调用数0`\n"
            + f"- 模型调用总硬上限：`{status.get('calls')}`（专家与恢复合计）\n"
            + f"- 专家初始调用上限：`{status.get('maximum_initial_calls')}`\n"
            + f"- 恢复调用保留：`{status.get('maximum_recovery_calls')}`\n"
            + f"- 费用异常提示阈值：{anomaly_text}\n"
            + "- 专家外部工具：`禁止`\n"
            + "- Provider：`精确单锁；禁止fallback`\n"
            + "- 跨任务历史：`不读取、不保存、不参与编组`\n"
            + "- 失败策略：`失败关闭；不调用旧运行时`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "模型调用：`0`。首次执行请评论：`/run-expert-team <ticket_task_id>`；"
            "受控重试请评论：`/retry-expert-team <唯一retry_id>`。\n"
            + run_line
        )
    else:
        text = (
            "## EXECUTION_FAILED\n\n"
            + run_line
            + "最终状态由价格优先运行时、独立审计、主Artifact和最终证明发布。\n"
        )
    print(text)
    return 0


def main() -> int:
    parser = legacy.parser()
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare(args)
    if args.command == "render":
        return render(args)
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
