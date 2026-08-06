#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"{label} insertion point not found")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


requirements = ROOT / "requirements-runtime.txt"
lines = [line for line in requirements.read_text(encoding="utf-8").splitlines() if line.strip()]
if not any(line.startswith("ortools==") for line in lines):
    lines.append("ortools==9.15.6755")
requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")

replace_once(
    ROOT / "open-model-market/v5_governance_model_plan.py",
    '''    if plan_value.get("selected_from_top20_reasoning_pool_only") is True:\n        _validate_top20_contract(plan_value, selected, recoveries)\n    else:\n        _validate_live_flagship_contract(plan_value, selected, recoveries)\n''',
    '''    if plan_value.get("selected_from_top50_reasoning_pool_only") is True:\n        from v5_top50_plan_validation import (\n            Top50PlanValidationError,\n            validate_top50_contract,\n        )\n\n        try:\n            validate_top50_contract(plan_value, selected, recoveries)\n        except Top50PlanValidationError as exc:\n            raise GovernanceModelPlanError(str(exc)) from exc\n    elif plan_value.get("selected_from_top20_reasoning_pool_only") is True:\n        _validate_top20_contract(plan_value, selected, recoveries)\n    else:\n        _validate_live_flagship_contract(plan_value, selected, recoveries)\n''',
    "top50 validator",
)

issue = ROOT / "open-model-market/v5_price_ranked_issue_ticket.py"
replace_once(
    issue,
    '''from v5_top20_pool_selector import (\n    Top20PoolSelectionError,\n    materialize_top20_selection,\n)\n''',
    '''from v5_top50_pool_optimizer import (\n    Top50PoolOptimizationError as Top20PoolSelectionError,\n    materialize_candidate_pool_selection as materialize_top20_selection,\n)\n''',
    "top50 issue import",
)
text = issue.read_text(encoding="utf-8")
for old, new in (
    ("governance-frozen top-20 reasoning pools", "governance-frozen top-50 reasoning pools"),
    ("live OpenRouter top-weekly reasoning pool", "live OpenRouter top-weekly reasoning top-50 pool"),
    ("前20个推理模型", "前50个推理模型"),
    (
        "按不同公司和价格从低到高选择4个主模型与4个顺序替补。",
        "由OR-Tools CP-SAT在不同公司约束下计算4个主模型、4个热替补，并保留其余模型为顺序替补。",
    ),
    (
        "未经批准的替换或Provider fallback。",
        "未经批准的模型替换；Provider只允许同一模型的合格端点自动fallback。",
    ),
    (
        "NetworkX只负责验证和编排有限有向无环执行图。",
        "OR-Tools只负责候选池组合优化；NetworkX只负责验证和编排有限有向无环执行图。",
    ),
    ("expert-center-top20-pool-selection-runtime", "expert-center-top50-ortools-runtime"),
    ("v5-governance-top20-pool-runtime-1", "v5-governance-top50-ortools-runtime-1"),
    (
        "expert-assessment-center-from-governance-top20-pool",
        "expert-assessment-center-ortools-from-governance-top50-pool",
    ),
    (
        "frozen-governance-top20-reasoning-pool-only",
        "frozen-governance-top50-reasoning-pool-only",
    ),
    ("推理周榜前20名候选池", "推理周榜前50名候选池"),
    ("前20名候选池SHA256", "前50名候选池SHA256"),
    ("4主+4替补分配权", "4主+4热替补及顺序替补分配权"),
    (
        "只能在冻结前20名合格候选内选择；禁止越池补选",
        "只能在冻结前50名合格候选内优化；其余合格模型全部保留为顺序替补",
    ),
    (
        "仅解析所选模型的精确兼容端点；精确单锁；禁止fallback",
        "固定首选合格端点；首选故障时允许同一模型的其他合格端点fallback",
    ),
):
    text = text.replace(old, new)
issue.write_text(text, encoding="utf-8")

replace_once(
    issue,
    '''                    "top20_reasoning_pool_size": plan[\n                        "top20_reasoning_pool_size"\n                    ],\n                    "expert_selectable_candidate_count": plan[\n''',
    '''                    "top20_reasoning_pool_size": plan[\n                        "top20_reasoning_pool_size"\n                    ],\n                    "top50_reasoning_pool_sha256": plan[\n                        "top50_reasoning_pool_sha256"\n                    ],\n                    "top50_reasoning_pool_size": plan[\n                        "top50_reasoning_pool_size"\n                    ],\n                    "top50_reasoning_pool_period": plan[\n                        "top50_reasoning_pool_period"\n                    ],\n                    "top50_expert_selectable_candidate_count": plan[\n                        "top50_expert_selectable_candidate_count"\n                    ],\n                    "optimizer": plan.get("optimizer"),\n                    "optimizer_optimality_proven": plan.get(\n                        "optimizer_audit", {}\n                    ).get("optimality_proven"),\n                    "expert_selectable_candidate_count": plan[\n''',
    "top50 status",
)
replace_once(
    issue,
    '''        "top20_reasoning_pool_sha256",\n        "selected_expert_count",\n''',
    '''        "top20_reasoning_pool_sha256",\n        "top50_reasoning_pool_sha256",\n        "top50_reasoning_pool_size",\n        "optimizer",\n        "optimizer_optimality_proven",\n        "selected_expert_count",\n''',
    "top50 output keys",
)

replace_once(
    ROOT / "open-model-market/v5_proposal_materializer.py",
    '''        "provider": {\n            "only": [str(endpoint["provider"])],\n            "order": [str(endpoint["provider"])],\n            "allow_fallbacks": False,\n            "require_parameters": True,\n        }\n''',
    '''        "provider": {\n            "order": [str(endpoint["provider"])],\n            "allow_fallbacks": True,\n            "require_parameters": True,\n        }\n''',
    "safe provider fallback",
)
replace_once(
    ROOT / "open-model-market/v5_price_ranked_support.py",
    '''        only = provider.get("only")\n        if isinstance(only, list) and only:\n            value = str(only[0]).strip()\n            if value:\n                providers.add(value)\n''',
    '''        only = provider.get("only")\n        order = provider.get("order")\n        preferred = only if isinstance(only, list) and only else order\n        if isinstance(preferred, list) and preferred:\n            value = str(preferred[0]).strip()\n            if value:\n                providers.add(value)\n''',
    "provider audit",
)

config_path = ROOT / "open-model-market/config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config.setdefault("catalog", {})["sorts"] = [
    "most-popular",
    "intelligence-high-to-low",
]
config["catalog"]["maximum_models"] = 50
config["catalog"]["popularity_period"] = "week"
config["selection_authority"] = "expert-center-ortools-from-governance-top50"
config["local_scoring_allowed"] = True
config["optimizer_allowed"] = True
config.setdefault("provider", {})["allow_fallbacks"] = True
config["provider"]["explicit_provider_lock_required"] = False
config["provider"]["same_model_qualified_fallback_only"] = True
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

(ROOT / "tools/apply_top50_ortools_upgrade.py").unlink(missing_ok=True)
(ROOT / ".github/workflows/apply-top50-ortools-upgrade.yml").unlink(missing_ok=True)
