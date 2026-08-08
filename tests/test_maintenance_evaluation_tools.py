import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "open-model-market" / "maintenance-evaluation-tools.json"
RUNTIME = ROOT / "requirements-runtime.txt"


def test_maintenance_tools_never_enter_production_runtime():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert data["schema_version"] == "expert-maintenance-evaluation-tools-v1"
    assert data["production_runtime_integration"] is False
    assert data["production_model_calls_enabled_by_this_registry"] is False
    assert data["tools"]
    for tool in data["tools"]:
        assert tool["production_runtime"] is False
        if "production_tool_use" in tool:
            assert tool["production_tool_use"] is False


def test_constitutional_runtime_dependency_set_is_unchanged():
    runtime = {
        line.split("==", 1)[0].strip()
        for line in RUNTIME.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert runtime == {"jsonschema", "networkx", "ortools", "optuna"}


def test_only_evaluation_tools_are_approved_for_maintenance():
    data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {item["id"]: item for item in data["tools"]}
    assert by_id["inspect-ai"]["status"] == "APPROVED_MAINTENANCE_EVALUATION_CANDIDATE"
    assert by_id["promptfoo"]["status"] == "APPROVED_MAINTENANCE_REDTEAM_CANDIDATE"
    assert by_id["instructor"]["status"] == "RADAR_ONLY_RUNTIME_DUPLICATE"
    assert by_id["litellm"]["status"] == "RADAR_ONLY_ROUTING_DUPLICATE"
