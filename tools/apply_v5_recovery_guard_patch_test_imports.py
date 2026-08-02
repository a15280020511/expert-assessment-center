from pathlib import Path

path = Path("tests/test_v5_critical_delivery_reliability.py")
text = path.read_text(encoding="utf-8")
old = '''from execution_graph import ExecutionGraph  # noqa: E402
from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_runtime import BudgetController, RuntimeConfig  # noqa: E402
'''
new = '''from v5_cross_endpoint_planner import CrossEndpointPlannerPolicy  # noqa: E402
from v5_planner import V5PlanningError  # noqa: E402
from v5_runtime import RuntimeConfig  # noqa: E402
'''
if text.count(old) != 1:
    raise SystemExit("expected exactly one legacy import block")
text = text.replace(old, new, 1)
old_assertion = '        self.assertIn("qwen/qwen-plus", models)\n'
new_assertion = '        self.assertIn("z-ai/glm", models)\n'
if text.count(old_assertion) != 1:
    raise SystemExit("expected exactly one stale cross-company assertion")
text = text.replace(old_assertion, new_assertion, 1)
path.write_text(text, encoding="utf-8")
print("test imports and company assertions patched")
