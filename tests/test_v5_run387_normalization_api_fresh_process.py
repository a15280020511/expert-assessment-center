from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"


class Run387NormalizationFreshProcessTests(unittest.TestCase):
    def test_hardening_normalizer_has_fresh_process_compatibility_surface(self) -> None:
        code = r'''
import sys
sys.path.insert(0, r"%s")
from v5_run387_hardening import hardened_normalize_answer
from v5_task_constraints import compile_task_constraints

task = "已知A购置价4800元，维护费600元，3年，每年0.9次，单次损失1800元。请计算总成本，不得补充外部数据。"
answer = "## 核心判断\nA：4800+600+3×0.9×1800=10260元。\n## 关键依据\n计算来自题面。\n## 不确定性与反例\n仅基于题面。\n## 可执行结论\n采用题面计算结果。"
normalized, audit = hardened_normalize_answer(
    task,
    answer,
    {"required_fields": ["核心判断", "关键依据", "不确定性与反例", "可执行结论"]},
    compile_task_constraints(task),
)
assert "10260" in normalized
assert audit["preserved_derived_quantity_line_count"] >= 1
''' % MARKET
        completed = subprocess.run(
            [sys.executable, "-c", code],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            0,
            completed.returncode,
            msg=f"stdout={completed.stdout}\nstderr={completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
