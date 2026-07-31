#!/usr/bin/env python3
"""V5 production ticket adapter over the shared hardened ticket parser."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import issue_ticket_hardened as hardened

V5_MAXIMUM_MODEL_CALLS = 16
V5_MAXIMUM_RECOVERY_CALLS = 4


def _reject(status: dict[str, Any], reason: str) -> None:
    errors = list(status.get("errors") or [])
    if reason not in errors:
        errors.append(reason)
    status["errors"] = errors
    status["reason"] = "; ".join(errors)
    status["accepted"] = False


def prepare(args: argparse.Namespace) -> int:
    result = hardened.prepare(args)
    root = Path(args.output_dir)
    status_path = root / "ticket-status.json"
    ticket_path = root / "ticket.json"
    if not status_path.is_file() or not ticket_path.is_file():
        return result

    status = json.loads(status_path.read_text(encoding="utf-8"))
    packet = json.loads(ticket_path.read_text(encoding="utf-8"))
    budget = packet.get("approved_budget") if isinstance(packet, Mapping) else None
    budget = budget if isinstance(budget, Mapping) else {}

    total_calls = int(status.get("calls") or 0)
    recovery_calls = budget.get("maximum_recovery_calls")
    recovery_calls = (
        int(recovery_calls)
        if isinstance(recovery_calls, int) and not isinstance(recovery_calls, bool)
        else -1
    )
    cost_policy = str(budget.get("cost_policy") or "")
    anomaly = budget.get("cost_anomaly_usd")
    anomaly = (
        float(anomaly)
        if isinstance(anomaly, (int, float)) and not isinstance(anomaly, bool)
        else None
    )

    if status.get("accepted") is True:
        if not 4 <= total_calls <= V5_MAXIMUM_MODEL_CALLS:
            _reject(status, "approved_budget.calls must be between 4 and 16")
        if not 0 <= recovery_calls <= V5_MAXIMUM_RECOVERY_CALLS:
            _reject(status, "approved_budget.maximum_recovery_calls must be between 0 and 4")
        elif recovery_calls >= total_calls:
            _reject(status, "approved recovery calls must leave at least one initial call")
        if cost_policy != "unbounded_with_anomaly_guard":
            _reject(status, "approved_budget.cost_policy must be unbounded_with_anomaly_guard")

    status["runtime_version"] = "v5-r8"
    status["calls"] = total_calls
    status["maximum_recovery_calls"] = max(0, recovery_calls)
    status["maximum_replacements"] = max(0, recovery_calls)
    status["maximum_initial_calls"] = max(0, total_calls - max(0, recovery_calls))
    status["max_cost_usd"] = anomaly
    status["cost_anomaly_usd"] = anomaly
    status["call_policy"] = "approved-total-includes-initial-and-recovery-calls"
    status["cost_policy"] = cost_policy or "invalid"
    status["analysis_owner"] = "github-v5-dynamic-expert-graph"
    status["fallback_policy"] = "disabled-fail-closed"
    status["legacy_runtime_present"] = False
    if status.get("accepted") is True:
        status["reason"] = (
            "ticket, authorization, uniqueness, approved total-call ceiling, "
            "reserved recovery pool, anomaly guard, and fail-closed policy accepted"
        )

    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    hardened._rewrite_outputs(status)
    for key in ("maximum_recovery_calls", "maximum_initial_calls", "cost_anomaly_usd"):
        hardened.base._write_output(key, status.get(key, ""))
    return result


def render(args: argparse.Namespace) -> int:
    status = json.loads(
        (Path(args.output_dir) / "ticket-status.json").read_text(encoding="utf-8")
    )
    run_line = f"- Run: `{args.run_url}`\n" if args.run_url else ""
    if args.phase == "accepted":
        heading = "EXECUTION_RETRY_ACCEPTED" if status.get("is_retry") else "EXECUTION_ACCEPTED"
        retry_line = f"- RETRY_ID: `{status.get('retry_id')}`\n" if status.get("is_retry") else ""
        anomaly = status.get("cost_anomaly_usd")
        anomaly_text = f"`${anomaly}`" if anomaly is not None else "`账户级与估算偏差守卫；无固定美元目标`"
        text = (
            f"## {heading}\n\n"
            "GitHub Issue Runner 已接收唯一 V5 动态专家图任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + retry_line
            + "- 分析责任：`GitHub V5动态专家DAG + 动态综合节点`\n"
            + "- 网页 GPT 职责：`仅提交、监控、取回和忠实转述；不得自行替代分析`\n"
            + "- 组合方式：`根据任务资源矩阵动态计算节点、职业、模型、Provider、提示词和参数`\n"
            + f"- 付费调用总硬上限：`{status.get('calls')}`（初始、重试、替换合计）\n"
            + f"- 初始调用规划上限：`{status.get('maximum_initial_calls')}`\n"
            + f"- 总额内恢复调用保留：`{status.get('maximum_recovery_calls')}`\n"
            + f"- 费用异常停止阈值：{anomaly_text}\n"
            + "- 专家外部工具：`禁止`\n"
            + "- 选模方式：`实时目录 + 多通道候选池 + 任务资源矩阵 + CP-SAT整体性价比优化`\n"
            + "- 隐式路由：`禁止；模型与Provider显式锁定`\n"
            + "- 失败策略：`失败关闭；不调用其他运行时`\n"
            + "- 旧运行时状态：`已从当前代码树删除`\n"
            + "- 公开交付：`最终报告分段发布；完整动态图、请求、费用和证明保存在Artifact`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "模型调用：`0`。请直接修正本 Issue 正文，然后评论："
            "`/retry-expert-team <唯一retry_id>`；不要为同一任务新建 Issue。\n"
            + run_line
        )
    else:
        return hardened.render(args)
    print(text)
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--event-path")
    prepare_parser.add_argument("--issue-title", default="")
    prepare_parser.add_argument("--issue-body", default="")
    prepare_parser.add_argument("--issue-number", default=0, type=int)
    prepare_parser.add_argument("--actor", default="")
    prepare_parser.add_argument("--author-association", default="")
    prepare_parser.add_argument("--comment-body", default="")
    prepare_parser.add_argument("--output-dir", default="ticket-artifacts")
    prepare_parser.set_defaults(func=prepare)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--phase", choices=["accepted", "rejected", "success", "failure"], required=True)
    render_parser.add_argument("--output-dir", default="ticket-artifacts")
    render_parser.add_argument("--run-url", default="")
    render_parser.set_defaults(func=render)
    return root


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.func(arguments))
