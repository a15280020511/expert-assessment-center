#!/usr/bin/env python3
"""Admission wrapper for governance-frozen top-20 reasoning pools.

The governance center freezes the live OpenRouter top-weekly reasoning pool and
qualifies selectable endpoints. This wrapper deterministically assigns four
primary and four recovery models inside that pool, then validates the resulting
immutable execution plan with the existing production contract.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping

import v5_governance_retry_state as governance_retry_state
import v5_issue_ticket as legacy
from v5_governance_model_plan import (
    GovernanceModelPlanError,
    validate_governance_model_plan,
)
from v5_top20_pool_selector import (
    Top20PoolSelectionError,
    materialize_top20_selection,
)

LEGACY_GOVERNANCE_RESERVE_REASON = (
    "approved recovery calls must leave at least one initial expert call "
    "after three governance calls"
)
governance_retry_state.patch(legacy)

DELEGATION_NOTICE = (
    "委托边界：治理中心每次实时冻结OpenRouter过去一周Token使用量最高的前20个推理模型，"
    "并完成模型、ZDR和精确Provider端点资格检查；专家团中心只能在该冻结候选池内，"
    "按不同公司和价格从低到高选择4个主模型与4个顺序替补。"
    "禁止越池选模、网络搜索、未经批准的替换或Provider fallback。"
    "NetworkX只负责验证和编排有限有向无环执行图。"
    "专家禁止外部工具；网页GPT只负责忠实提交、监控、取回和转述。"
)


def _rewrite_outputs(status: Mapping[str, Any]) -> None:
    legacy._rewrite_outputs(status)  # noqa: SLF001
    for key in (
        "cost_anomaly_usd",
        "model_plan_sha256",
        "candidate_pool_plan_sha256",
        "top20_reasoning_pool_sha256",
        "selected_expert_count",
        "selected_recovery_count",
        "model_selection_authority",
    ):
        value = status.get(key, "")
        legacy._write_output(  # noqa: SLF001
            key,
            "" if value is None else value,
        )


def _read_original_packet(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], argparse.Namespace]:
    sanitized_args = copy.copy(args)
    if args.event_path:
        event = json.loads(Path(args.event_path).read_text(encoding="utf-8"))
        issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
        packet = json.loads(str(issue.get("body") or ""))
        if not isinstance(packet, dict):
            raise ValueError("Issue body must be one JSON object")
        sanitized = dict(packet)
        sanitized.pop("governance_model_plan", None)
        sanitized_event = dict(event)
        sanitized_issue = dict(issue)
        sanitized_issue["body"] = json.dumps(
            sanitized, ensure_ascii=False, separators=(",", ":")
        )
        sanitized_event["issue"] = sanitized_issue
        path = root / "sanitized-admission-event.json"
        path.write_text(
            json.dumps(sanitized_event, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        sanitized_args.event_path = str(path)
        return packet, sanitized_args

    packet = json.loads(str(args.issue_body or ""))
    if not isinstance(packet, dict):
        raise ValueError("Issue body must be one JSON object")
    sanitized = dict(packet)
    sanitized.pop("governance_model_plan", None)
    sanitized_args.issue_body = json.dumps(
        sanitized, ensure_ascii=False, separators=(",", ":")
    )
    return packet, sanitized_args


def _task_text(packet: Mapping[str, Any]) -> str:
    sanitized = dict(packet)
    sanitized.pop("governance_model_plan", None)
    projected = legacy._substantive_task_text(sanitized)  # noqa: SLF001
    if projected.startswith(legacy.DELEGATION_NOTICE):
        projected = DELEGATION_NOTICE + projected[len(legacy.DELEGATION_NOTICE) :]
    return projected


def _accept_legacy_only_budget_rejection(
    status: Mapping[str, Any], total: int, recovery: int
) -> bool:
    errors = [str(value) for value in status.get("errors", [])]
    return bool(
        status.get("accepted") is not True
        and errors == [LEGACY_GOVERNANCE_RESERVE_REASON]
        and total - recovery >= 3
    )


def _postprocess(root: Path, packet: Mapping[str, Any]) -> dict[str, Any]:
    path = root / "ticket-status.json"
    status = json.loads(path.read_text(encoding="utf-8"))
    total = int(status.get("calls") or 0)
    recovery = int(status.get("maximum_recovery_calls") or 0)
    if _accept_legacy_only_budget_rejection(status, total, recovery):
        status["accepted"] = True
        status["errors"] = []
        status["reason"] = ""

    if status.get("accepted") is True:
        try:
            materialized_packet, receipt = materialize_top20_selection(packet)
            plan = validate_governance_model_plan(materialized_packet)
            if int(plan["expert_count"]) > total - recovery:
                raise GovernanceModelPlanError(
                    "selected expert count exceeds initial call capacity"
                )
            status.update(
                {
                    "required_model_calls": int(plan["expert_count"]),
                    "maximum_initial_calls": total - recovery,
                    "analysis_owner": "expert-center-top20-pool-selection-runtime",
                    "runtime_version": "v5-governance-top20-pool-runtime-1",
                    "claude_red_team_calls": 0,
                    "claude_mechanism_enabled": False,
                    "governance_model_calls": 0,
                    "model_selection_authority": (
                        "expert-assessment-center-from-governance-top20-pool"
                    ),
                    "candidate_pool_authority": "decision-system-governance",
                    "model_assignment_authority": "expert-assessment-center",
                    "model_plan_sha256": plan["plan_sha256"],
                    "candidate_pool_plan_sha256": plan[
                        "source_governance_pool_plan_sha256"
                    ],
                    "top20_reasoning_pool_sha256": plan[
                        "top20_reasoning_pool_sha256"
                    ],
                    "top20_reasoning_pool_size": plan[
                        "top20_reasoning_pool_size"
                    ],
                    "expert_selectable_candidate_count": plan[
                        "expert_selectable_candidate_count"
                    ],
                    "expert_center_selection_receipt_sha256": receipt[
                        "receipt_sha256"
                    ],
                    "selected_expert_count": plan["expert_count"],
                    "selected_recovery_count": plan["recovery_count"],
                    "expert_center_model_selection_allowed": True,
                    "expert_center_model_selection_scope": (
                        "frozen-governance-top20-reasoning-pool-only"
                    ),
                    "expert_center_model_reranking_allowed": False,
                    "model_substitution_allowed": False,
                    "provider_resolution_only": True,
                    "business_retry_limit": (
                        governance_retry_state.BUSINESS_RETRY_LIMIT
                    ),
                    "system_repair_retry_limit": (
                        governance_retry_state.SYSTEM_REPAIR_RETRY_LIMIT
                    ),
                    "call_policy": (
                        "approved-total-includes-experts-and-recovery-only"
                    ),
                    "reason": (
                        "explicit command, authorization, uniqueness, frozen top-20 "
                        "reasoning pool, expert-center in-pool 4+4 assignment, exact "
                        "provider lock, recovery reserve, and fail-closed policy accepted"
                    ),
                }
            )
            (root / "ticket.json").write_text(
                json.dumps(materialized_packet, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (root / "governance-model-plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (root / "expert-center-selection-receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            (root / "task.txt").write_text(_task_text(packet), encoding="utf-8")
        except (
            GovernanceModelPlanError,
            Top20PoolSelectionError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            status["accepted"] = False
            status["errors"] = [str(exc)]
            status["reason"] = str(exc)
            status["model_selection_authority"] = (
                "expert-assessment-center-from-governance-top20-pool"
            )
            status["candidate_pool_authority"] = "decision-system-governance"
            status["expert_center_model_selection_allowed"] = True

    path.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _rewrite_outputs(status)
    return status


def prepare(args: argparse.Namespace) -> int:
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    try:
        packet, sanitized_args = _read_original_packet(args, root)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        legacy.prepare(args)
        status_path = root / "ticket-status.json"
        status = json.loads(status_path.read_text(encoding="utf-8"))
        status["accepted"] = False
        status["errors"] = [str(exc)]
        status["reason"] = str(exc)
        status_path.write_text(
            json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _rewrite_outputs(status)
        return 0
    legacy.prepare(sanitized_args)
    _postprocess(root, packet)
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
            "GitHub Issue Runner 已接收治理中心冻结的推理周榜前20名候选池。\n\n"
            f"- Task ID：`{status.get('task_id')}`\n"
            f"- TASK_FINGERPRINT: `{status.get('task_fingerprint')}`\n"
            + identity
            + f"- 候选池计划SHA256：`{status.get('candidate_pool_plan_sha256')}`\n"
            + f"- 执行模型计划SHA256：`{status.get('model_plan_sha256')}`\n"
            + f"- 前20名候选池SHA256：`{status.get('top20_reasoning_pool_sha256')}`\n"
            + "- 候选池权：`decision-system-governance`\n"
            + "- 4主+4替补分配权：`expert-assessment-center`\n"
            + "- 专家团权限：`只能在冻结前20名合格候选内选择；禁止越池补选`\n"
            + "- Provider：`仅解析所选模型的精确兼容端点；精确单锁；禁止fallback`\n"
            + "- 组织：`并行独立分析 → 交叉审查 → 最终综合`\n"
            + "- Claude机制：`关闭；调用数0`\n"
            + f"- 模型调用总硬上限：`{status.get('calls')}`（专家与恢复合计）\n"
            + f"- 计划专家数：`{status.get('selected_expert_count')}`\n"
            + f"- 计划恢复模型数：`{status.get('selected_recovery_count')}`\n"
            + f"- 费用异常提示阈值：{anomaly_text}\n"
            + "- 专家外部工具：`禁止`\n"
            + "- 跨任务历史：`不读取、不保存、不参与执行`\n"
            + "- 失败策略：`候选池缺失、篡改、不足8个合格公司或模型不可执行即失败关闭`\n"
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
            + "最终状态由候选池、专家中心选择回执、独立审计、主Artifact和最终证明发布。\n"
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
