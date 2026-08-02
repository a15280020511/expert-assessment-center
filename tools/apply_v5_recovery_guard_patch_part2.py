from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[0]


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one regex match, found {count}")
    return updated


# 3. Runtime must re-use the frozen recovery risk multiplier before a paid call.
path = "open-model-market/v5_runtime.py"
text = read(path)
old = '''    def reserve(self, kind: str, estimated_cost_usd: float, node_id: str) -> tuple[bool, str]:\n        estimated = max(0.0, float(estimated_cost_usd))\n        risk = estimated * float(self.config.cost_risk_multiplier)\n'''
new = '''    def reserve(\n        self,\n        kind: str,\n        estimated_cost_usd: float,\n        node_id: str,\n        *,\n        risk_multiplier: float | None = None,\n    ) -> tuple[bool, str]:\n        estimated = max(0.0, float(estimated_cost_usd))\n        multiplier = (\n            float(self.config.cost_risk_multiplier)\n            if risk_multiplier is None\n            else max(1.0, float(risk_multiplier))\n        )\n        risk = estimated * multiplier\n'''
text = replace_once(text, old, new, "extend budget reserve")
old = '''                        "estimated_cost_usd": round(estimated, 8),\n                        "reason": reason,\n'''
new = '''                        "estimated_cost_usd": round(estimated, 8),\n                        "risk_multiplier": round(multiplier, 8),\n                        "risk_adjusted_cost_usd": round(risk, 8),\n                        "reason": reason,\n'''
text = replace_once(text, old, new, "record budget denial risk")
old = '''        allowed, _ = budget.reserve(kind, node.estimated_cost, selected_node_id)\n'''
new = '''        risk_multiplier = None\n        if kind == "replacement":\n            try:\n                risk_multiplier = float(\n                    node.parameter_profile.get(\n                        "recovery_cost_risk_multiplier",\n                        self.config.cost_risk_multiplier,\n                    )\n                )\n            except (TypeError, ValueError):\n                risk_multiplier = float(self.config.cost_risk_multiplier)\n        allowed, _ = budget.reserve(\n            kind,\n            node.estimated_cost,\n            selected_node_id,\n            risk_multiplier=risk_multiplier,\n        )\n'''
text = replace_once(text, old, new, "apply frozen recovery risk")
write(path, text)


# 4. Closed-world prompt must preserve user-facing units, not internal aliases.
path = "open-model-market/v5_task_constraints.py"
text = read(path)
marker = '''\ndef closed_world_numeric_prompt(\n'''
helper = '''\ndef original_quantity_tokens(text: str) -> list[str]:\n    """Return de-duplicated quantities exactly as written for prompt display."""\n    values: list[str] = []\n    for match in _QUANTITY_RE.finditer(str(text or "")):\n        token = re.sub(r"\\s+", "", match.group(0)).strip()\n        if token and token not in values:\n            values.append(token)\n    return values\n\n\ndef closed_world_numeric_prompt(\n'''
text = replace_once(text, marker, helper, "insert original quantity display")
old = '''    allowed = sorted(\n        normalized_quantities(task),\n        key=lambda row: (row[2], float(row[0]), float(row[1] or row[0])),\n    )\n    tokens = [\n        f"{lo}{('-' + hi) if hi else ''}:{unit}"\n        for lo, hi, unit in allowed\n    ]\n    rendered = "[" + ", ".join(tokens) + "]"\n    return (\n        "封闭世界精确数量规则（不可覆盖）：允许出现的‘数值+单位’仅限"\n        f"以下规范化集合：{rendered}。除该集合外，禁止输出任何带单位的"\n        "精确数量，包括算术中间结果、示例值、替代月份或年份、敏感性阈值、"\n        "预测值和派生情景。校验题面给定结果时，只能写由清单内数量组成、且"\n        "等式结果也已在清单中的直接等式；不得展开或报告新的中间数值。"\n        "反转条件若题面未给数值阈值，只能定性表述。"\n    )\n'''
new = '''    rendered = "[" + "，".join(original_quantity_tokens(task)) + "]"\n    return (\n        "封闭世界精确数量规则（不可覆盖）：允许出现的‘数值+单位’仅限"\n        f"题面原样集合：{rendered}。回答必须保留题面原始单位，不得把中文"\n        "量词替换为内部归一化标签；例如不得用 people、item 或 times 代替"\n        "人/名/位、件/顶或次。除该集合外，禁止输出任何带单位的精确数量，"\n        "包括算术中间结果、示例值、替代月份或年份、敏感性阈值、预测值和"\n        "派生情景。校验题面给定结果时，只能写由清单内数量组成、且等式结果"\n        "也已在清单中的直接等式；不得展开或报告新的中间数值。反转条件若"\n        "题面未给数值阈值，只能定性表述。"\n    )\n'''
text = replace_once(text, old, new, "preserve display units")
write(path, text)


# 5. Remove duplicate upstream raw mirrors and enforce completion-first delivery.
path = "open-model-market/v5_constitutional_runtime.py"
text = read(path)
marker = '''class ConstitutionalPromptPolicy:\n    """Build provider-locked requests without using the legacy executor module."""\n\n'''
helper = '''class ConstitutionalPromptPolicy:\n    """Build provider-locked requests without using the legacy executor module."""\n\n    @staticmethod\n    def _compact_upstream_contract(\n        contract: Mapping[str, Any],\n    ) -> dict[str, Any]:\n        """Remove only the duplicate raw mirror; preserve every substantive field."""\n        return {\n            str(key): value\n            for key, value in contract.items()\n            if str(key) != "raw_fields"\n        }\n\n'''
text = replace_once(text, marker, helper, "insert upstream compaction")
old = '''                        "answer": json.dumps(\n                            contract,\n                            ensure_ascii=False,\n                            separators=(",", ":"),\n                            default=str,\n                        ),\n'''
new = '''                        "answer": json.dumps(\n                            self._compact_upstream_contract(contract),\n                            ensure_ascii=False,\n                            separators=(",", ":"),\n                            default=str,\n                        ),\n'''
text = replace_once(text, old, new, "compact upstream contract")
old = '''        numeric_policy = closed_world_numeric_prompt(original_task, constraints)\n        messages = payload.get("messages")\n'''
new = '''        numeric_policy = closed_world_numeric_prompt(original_task, constraints)\n        delivery_discipline = ""\n        if bool(node.output_contract.get("explicit_markdown_contract")):\n            delivery_discipline = (\n                "\\n显式长篇合同交付纪律：先按顺序生成全部指定H2标题并确保每节非空，"\n                "再补充细节。若输出空间紧张，压缩重复事实、表格和修饰语，"\n                "不得遗漏标题、改变顺序、增加其他H2或用冗长复述耗尽输出。"\n            )\n        messages = payload.get("messages")\n'''
text = replace_once(text, old, new, "add completion-first discipline")
old = '''                    + (("\\n" + numeric_policy) if numeric_policy else "")\n                ),\n'''
new = '''                    + (("\\n" + numeric_policy) if numeric_policy else "")\n                    + delivery_discipline\n                ),\n'''
text = replace_once(text, old, new, "append delivery discipline")
write(path, text)
