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
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("test imports patched")
