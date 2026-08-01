#!/usr/bin/env python3
"""Apply the V5 closed-world numeric prompt hardening atomically."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
TESTS = ROOT / "tests"


def replace_once(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one marker in {path}, got {count}: {old[:80]!r}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def patch_constraints() -> None:
    path = MARKET / "v5_task_constraints.py"
    marker = '''    return values


def _normalize_claim(value: str) -> str:
'''
    replacement = '''    return values


def closed_world_numeric_prompt(
    task: str,
    constraints: TaskConstraints | Mapping[str, Any] | None = None,
) -> str:
    """Render an operational numeric policy from the immutable task evidence."""
    policy = constraints or compile_task_constraints(task)
    if isinstance(policy, Mapping):
        precise_allowed = bool(
            policy.get("unsupported_precise_quantities_allowed", True)
        )
    else:
        precise_allowed = policy.unsupported_precise_quantities_allowed
    if precise_allowed:
        return ""

    allowed = sorted(
        normalized_quantities(task),
        key=lambda row: (row[2], float(row[0]), float(row[1] or row[0])),
    )
    tokens = [
        f"{lo}{('-' + hi) if hi else ''}:{unit}"
        for lo, hi, unit in allowed
    ]
    rendered = "[" + ", ".join(tokens) + "]"
    return (
        "封闭世界精确数量规则（不可覆盖）：允许出现的‘数值+单位’仅限"
        f"以下规范化集合：{rendered}。除该集合外，禁止输出任何带单位的"
        "精确数量，包括算术中间结果、示例值、替代月份或年份、敏感性阈值、"
        "预测值和派生情景。校验题面给定结果时，只能写由清单内数量组成、且"
        "等式结果也已在清单中的直接等式；不得展开或报告新的中间数值。"
        "反转条件若题面未给数值阈值，只能定性表述。"
    )


def _normalize_claim(value: str) -> str:
'''
    replace_once(path, marker, replacement)


def patch_runtime() -> None:
    path = MARKET / "v5_constitutional_runtime.py"
    replace_once(
        path,
        '''from v5_task_constraints import (
    TaskConstraints,
    compile_task_constraints,
    validate_answer_evidence,
)
''',
        '''from v5_task_constraints import (
    TaskConstraints,
    closed_world_numeric_prompt,
    compile_task_constraints,
    validate_answer_evidence,
)
''',
    )
    replace_once(
        path,
        '''        constraints = compile_task_constraints(original_task)
        messages = payload.get("messages")
''',
        '''        constraints = compile_task_constraints(original_task)
        numeric_policy = closed_world_numeric_prompt(original_task, constraints)
        messages = payload.get("messages")
''',
    )
    replace_once(
        path,
        '''                    + "\\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                    "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
                ),
''',
        '''                    + "\\n题面是唯一用户事实源。模型推断必须标为推断或假设；"
                    "不得把上游模型判断改标为事实；不得引入题面没有的精确数量。"
                    + (("\\n" + numeric_policy) if numeric_policy else "")
                ),
''',
    )


def patch_regressions() -> None:
    path = TESTS / "test_v5_v4_contract_isolation.py"
    replace_once(
        path,
        '''import v5_task_delivery_contract as contracts  # noqa: E402
''',
        '''import v5_task_delivery_contract as contracts  # noqa: E402
from v5_task_constraints import closed_world_numeric_prompt  # noqa: E402
''',
    )
    replace_once(
        path,
        ''')


def node(contract, functions=("analysis",)):
''',
        ''')
PAID_TASK = (
    "仅依据题面完成封闭世界决策。方案A一次性投入1200元、每月300元；"
    "方案B一次性投入300元、每月450元；评估期24个月。"
    "题面给定并要求校验：盈亏平衡点为第6个月，方案A的24个月总成本为"
    "8400元，方案B为11100元，差额为2700元。不得调用外部工具、不得联网、"
    "不得引入题面外事实或其他精确数量。"
)


def node(contract, functions=("analysis",)):
''',
    )
    replace_once(
        path,
        '''        for heading in HEADINGS:
            self.assertNotIn(heading, internal_user)
            self.assertIn(heading, final_user)

    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
''',
        '''        for heading in HEADINGS:
            self.assertNotIn(heading, internal_user)
            self.assertIn(heading, final_user)

        numeric = closed_world_numeric_prompt(TASK)
        self.assertIn("1200:yuan", numeric)
        self.assertIn("300:yuan", numeric)
        self.assertIn("450:yuan", numeric)
        self.assertIn("24:month", numeric)
        self.assertIn("算术中间结果", numeric)
        self.assertIn("直接等式", numeric)
        self.assertIn("定性表述", numeric)
        self.assertNotIn("2550:yuan", numeric)
        for payload in (internal_payload, final_payload):
            system = payload["messages"][0]["content"]
            self.assertIn(numeric, system)

    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
''',
    )
    old_method = '''    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
        answer = (
            "若评估期为3个月，方案A为2100元，方案B为1650元。"
        )
        violations = validate_scope_boundaries(TASK, answer)
        rendered = ";".join(violations)
        self.assertIn("3:month", rendered)
        self.assertIn("2100:yuan", rendered)
        self.assertIn("1650:yuan", rendered)

'''
    new_method = '''    def test_closed_world_rejects_v4_novel_month_and_currency_values(self):
        rejected = (
            "若评估期为5个月，阈值为2550元；另以12个月、150元、3000元、"
            "7200元和10800元作为中间结果或派生情景。"
        )
        rendered = ";".join(validate_scope_boundaries(PAID_TASK, rejected))
        for token in (
            "5:month",
            "12:month",
            "150:yuan",
            "2550:yuan",
            "3000:yuan",
            "7200:yuan",
            "10800:yuan",
        ):
            self.assertIn(token, rendered)

        compliant = (
            "## 题面事实\\n"
            "方案A一次性投入1200元、每月300元；方案B一次性投入300元、"
            "每月450元；评估期24个月。\\n"
            "## 计算与校验\\n"
            "1200元 + 300元 × 24个月 = 8400元。\\n"
            "300元 + 450元 × 24个月 = 11100元。\\n"
            "11100元 - 8400元 = 2700元。\\n"
            "1200元 + 300元 × 6个月 = 300元 + 450元 × 6个月。\\n"
            "## 推断与未知\\n"
            "题面未给其他情景参数，不作数值外推。\\n"
            "## 结论与反转条件\\n"
            "按题面总成本选择方案A；反转条件仅作定性表述。"
        )
        self.assertEqual([], validate_scope_boundaries(PAID_TASK, compliant))

'''
    replace_once(path, old_method, new_method)


def main() -> int:
    patch_constraints()
    patch_runtime()
    patch_regressions()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
