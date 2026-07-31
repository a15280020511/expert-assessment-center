#!/usr/bin/env python3
"""V5 production ticket adapter over the preserved hardened ticket parser."""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import issue_ticket_hardened as hardened

V5_MAXIMUM_MODEL_CALLS = 16
V5_MAXIMUM_REPLACEMENTS = 2


def prepare(args: argparse.Namespace) -> int:
    result = hardened.prepare(args)
    root = Path(args.output_dir)
    status_path = root / "ticket-status.json"
    if not status_path.is_file():
        return result
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["runtime_version"] = "v5-r8"
    status["legacy_requested_calls"] = status.get("calls")
    status["calls"] = V5_MAXIMUM_MODEL_CALLS
    status["maximum_replacements"] = V5_MAXIMUM_REPLACEMENTS
    status["call_policy"] = "dynamic-graph-actual-use-with-16-call-hard-ceiling"
    status["cost_policy"] = "finite-by-call-and-token-bounds-no-fixed-dollar-ceiling"
    status["analysis_owner"] = "github-v5-dynamic-expert-graph"
    status["v3_fallback_policy"] = "disabled"
    if status.get("accepted") is True:
        status["reason"] = (
            "ticket, authorization, uniqueness, V5 dynamic graph call ceiling, "
            "and fail-closed no-fallback policy accepted"
        )
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    hardened._rewrite_outputs(status)
    return result


def render(args: argparse.Namespace) -> int:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = hardened.render(args)
    text = buffer.getvalue()
    replacements = {
        "- 分析责任：`GitHub 专家团 + 裁判`": "- 分析责任：`GitHub V5动态专家DAG + 动态综合节点`",
        "- 固定组合：`3名专家 + 1名裁判`": "- 组合方式：`根据任务资源矩阵动态计算节点、职业、模型、Provider、提示词和参数`",
        "- 批准调用数：`16`": "- 动态调用安全上限：`16`（实际调用由任务规划决定）",
        "- 额外调用额度（专家或裁判故障替换共享）：`2`": "- 全局故障恢复：`最多2次有限替换；不自动回退V3`",
        "- 选模方式：`稳定和能力硬门槛；通过后value档性价比优先；厂商独立`": "- 选模方式：`实时目录 + 任务资源矩阵 + CP-SAT整体性价比优化`",
        "- 推理参数：`受控动态字段；生产统一low reasoning与low verbosity；不发送人为Token上限`": "- 推理参数：`按节点价值动态计算reasoning、采样、上下文和输出许可`",
        "- 语义路由：`默认关闭`": "- 隐式路由：`禁止；模型与Provider显式锁定`",
        "- 公开回退：`完整裁判报告将分段发布到本Issue；Artifact下载失败时可直接读取评论`": "- 公开交付：`V5最终报告分段发布；完整动态图与请求证据保存在Artifact`",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if args.phase == "accepted":
        text += "\n- 生产运行时：`V5 R8`\n- V3隐式fallback：`禁止`\n- V3状态：`仅保留人工回滚，不参与本次执行`\n"
    print(text.rstrip())
    return result


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
