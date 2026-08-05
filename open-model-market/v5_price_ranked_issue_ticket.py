#!/usr/bin/env python3
"""Admission wrapper for governance-selected expert execution tickets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

import v5_issue_ticket as legacy
from v5_governance_selection import validate_governance_selection

LEGACY_GOVERNANCE_RESERVE_REASON = (
    "approved recovery calls must leave at least one initial expert call "
    "after three governance calls"
)

DELEGATION_NOTICE = (
    "委托边界：模型选择、Provider选择、专家角色、任务分工和恢复模型全部由治理中心"
    "在派发前确定并绑定哈希；专家团中心只校验和执行，不读取模型市场、不排序、不换模、"
    "不补模，也不允许本地fallback。NetworkX仅验证和执行既定有向无环图。"
    "专家禁止外部工具；网页GPT只负责忠实提交、监控、取回和转述。"
)


def _rewrite_outputs(status: Mapping[str, Any]) -> None:
    legacy._rewrite_outputs(status)  # noqa: SLF001


def _reject(status: dict[str, Any], message: str) -> None:
    status["accepted"] = False
    status["errors"] = [message]
    status["reason"] = message
    status["required_model_calls"] = 0
    status["expert_center_model_selection"] = False
    status["governance_selection_validated"] = False


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
        status["accepted"] = True
        status["errors"] = []
        status["reason"] = ""

    packet: dict[str, Any] = {}
    if (root / "ticket.json").is_file():
        value = json.loads((root / "ticket.json").read_text(encoding="utf-8"))
        if isinstance(value, dict):
            packet = value

    if status.get("accepted") is True:
        plan = packet.get("governance_selection")
        if not isinstance(plan, Mapping):
            _reject(status, "governance_selection is required; local model selection is removed")
        elif total - recovery < 3:
            _reject(status, "expert team requires at least three initial calls")
        else:
            try:
                receipt = validate_governance_selection(
                    plan,
                    approved_total_calls=total,
                    approved_recovery_calls=recovery,
                )
            except Exception as exc:  # noqa: BLE001
                _reject(
                    status,
                    f"governance selection validation failed: {type(exc).__name__}: {exc}",
                )
            else:
                task_text = str(plan.get("task_text") or "").strip()
                (root / "governance-selection.json").write_text(
                    json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                (root / "governance-selection-validation.json").write_text(
                    json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n",
                    encoding="utf-8",
                )
                (root / "task.txt").write_text(task_text + "\n", encoding="utf-8")
                status.update(
                    {
                        "required_model_calls": len(plan["proposal"]["nodes"]),
                        "maximum_initial_calls": total - recovery,
                        "analysis_owner": "governance-selected-expert-execution",
                        "runtime_version": "v5-price-ranked-runtime-1",
                        "selection_authority": "decision-system-governance",
                        "selection_source_repository": plan["source_repository"],
                        "selection_source_commit": plan.get("source_commit", ""),
                        "selection_plan_sha256": plan["plan_sha256"],
                        "governance_selection_validated": True,
                        "expert_center_model_selection": False,
                        "expert_center_catalog_fetch": False,
                        "local_selection_fallback_allowed": False,
                        "claude_red_team_calls": 0,
                        "claude_mechanism_enabled": False,
                        "governance_model_calls": 0,
                        "call_policy": "approved-total-includes-experts-and-recovery-only",
                        "reason": (
                            "explicit command, governance-bound selection plan, task/hash/budget "
                            "validation, exact provider lock and fail-closed execution accepted"
                        ),
                    }
                )

    path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
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
            "专家团中心已接收由治理中心完成选模的唯一执行任务。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + identity
            + "- 选模权：`decision-system-governance`\n"
            + f"- 治理选模方案：`{status.get('selection_plan_sha256')}`\n"
            + "- 专家团中心选模：`已移除；0次`\n"
            + "- 专家团中心读取模型市场：`禁止`\n"
            + "- 本地选模fallback：`禁止；缺少或篡改方案即失败关闭`\n"
            + "- 组织：`治理中心既定DAG → 专家团中心校验 → 执行`\n"
            + "- Claude机制：`关闭；调用数0`\n"
            + f"- 模型调用总硬上限：`{status.get('calls')}`（专家与恢复合计）\n"
            + f"- 专家初始调用上限：`{status.get('maximum_initial_calls')}`\n"
            + f"- 恢复调用保留：`{status.get('maximum_recovery_calls')}`\n"
            + f"- 费用异常提示阈值：{anomaly_text}\n"
            + "- 专家外部工具：`禁止`\n"
            + "- Provider：`治理中心指定；精确单锁；禁止fallback`\n"
            + "- 跨任务历史：`不读取、不保存、不参与编组`\n"
            + run_line
        )
    elif args.phase == "rejected":
        text = (
            "## EXECUTION_REJECTED\n\n"
            f"票据未进入模型调用阶段：{status.get('reason', 'unknown')}。\n\n"
            "模型调用：`0`。专家团中心不会自行选模或降级到旧运行时。\n"
            + run_line
        )
    else:
        text = (
            "## EXECUTION_FAILED\n\n"
            + run_line
            + "最终状态由治理选模方案校验、执行审计、主Artifact和最终证明发布。\n"
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
