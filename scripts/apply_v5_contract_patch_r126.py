from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "open-model-market/task_semantic_compiler.py",
    "from model_market import RunConfig, TaskProfile\n",
    "from model_market import RunConfig, TaskProfile\n\nimport v5_task_delivery_contract as task_delivery_contract\n",
)
replace_once(
    "open-model-market/task_semantic_compiler.py",
    '''def _output_contract(operations: Mapping[str, float], structured: bool) -> dict[str, Any]:
    fields = {"conclusions", "assumptions", "uncertainties", "evidence_gaps"}
    extras = {
        "quantitative_modeling": {"variables", "formulas", "calculations", "sensitivity"},
        "forecasting": {"scenarios", "triggers", "forecast_horizon"},
        "adversarial_reasoning": {"failure_modes", "counterexamples", "rejection_conditions"},
        "implementation": {"dependencies", "steps", "acceptance_tests", "rollback_conditions"},
        "decision_comparison": {"options", "criteria", "tradeoffs", "ranking"},
        "synthesis": {"agreements", "disagreements", "conflict_resolution", "final_recommendation"},
        "evidence_validation": {"validated_claims", "unsupported_claims", "verification_limits"},
    }
    for operation in operations:
        fields |= extras.get(operation, set())
    return {"required_fields": sorted(fields), "machine_readable_required": structured,
            "must_separate_fact_assumption_inference": True}
''',
    '''def _output_contract(task: str, operations: Mapping[str, float], structured: bool) -> dict[str, Any]:
    fields = {"conclusions", "assumptions", "uncertainties", "evidence_gaps"}
    extras = {
        "quantitative_modeling": {"variables", "formulas", "calculations", "sensitivity"},
        "forecasting": {"scenarios", "triggers", "forecast_horizon"},
        "adversarial_reasoning": {"failure_modes", "counterexamples", "rejection_conditions"},
        "implementation": {"dependencies", "steps", "acceptance_tests", "rollback_conditions"},
        "decision_comparison": {"options", "criteria", "tradeoffs", "ranking"},
        "synthesis": {"agreements", "disagreements", "conflict_resolution", "final_recommendation"},
        "evidence_validation": {"validated_claims", "unsupported_claims", "verification_limits"},
    }
    for operation in operations:
        fields |= extras.get(operation, set())
    base = {"required_fields": sorted(fields), "machine_readable_required": structured,
            "must_separate_fact_assumption_inference": True}
    return task_delivery_contract.apply_explicit_contract(task, operations, base)
''',
)
replace_once(
    "open-model-market/task_semantic_compiler.py",
    "output_contract=_output_contract(operations, structured),",
    "output_contract=_output_contract(task, operations, structured),",
)

replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    "import v5_executor\n",
    "import v5_executor\nimport v5_task_delivery_contract as task_delivery_contract\n",
)
replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    '''    "required_fields",
)
''',
    '''    "required_fields",
    "exact_top_level_fields",
    "nested_exact_fields",
    "nested_values_must_be_objects",
    "explicit_user_contract",
)
''',
)
replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    '''    compact_rule = _compact_delivery_rule(fields) if _compact_mode_enabled() else ""
    if node.output_contract.get("machine_readable_required"):
''',
    '''    compact_rule = _compact_delivery_rule(fields) if _compact_mode_enabled() else ""
    explicit_rule = task_delivery_contract.delivery_rule(node.output_contract)
    if node.output_contract.get("machine_readable_required"):
''',
)
replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    '''            f"{separation_rule}"
            "内容必须精炼，避免重复；在篇幅受限时优先保证所有必填键存在且JSON语法完整闭合。"
''',
    '''            f"{explicit_rule}{separation_rule}"
            "内容必须精炼，避免重复；在篇幅受限时优先保证所有必填键存在且JSON语法完整闭合。"
''',
)
replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    '''    if metadata and len(missing) == len(required):
        _append_reason(reasons, "contract-metadata-echo")

    if missing or "contract-metadata-echo" in reasons:
        passed = False
        score = min(float(score), 0.35)
''',
    '''    if metadata and len(missing) == len(required):
        _append_reason(reasons, "contract-metadata-echo")
    explicit_violations = task_delivery_contract.validate_parsed_contract(
        parsed, node.output_contract
    )
    for violation in explicit_violations:
        _append_reason(reasons, violation)

    if missing or "contract-metadata-echo" in reasons or explicit_violations:
        passed = False
        score = min(float(score), 0.35)
''',
)

replace_once(
    "open-model-market/v5_runtime.py",
    "import v5_output_contract_delivery as output_contract\n",
    "import v5_output_contract_delivery as output_contract\nimport v5_task_delivery_contract as task_delivery_contract\n",
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        complete = not required or all(populated(field) for field in required)
        canonical = json.dumps(standard, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
''',
    '''        explicit_violations = task_delivery_contract.validate_parsed_contract(
            parsed, node.output_contract
        )
        complete = (
            (not required or all(populated(field) for field in required))
            and not explicit_violations
        )
        canonical = json.dumps(standard, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''            "compression_used": False,
            **standard,
''',
    '''            "compression_used": False,
            "contract_violations": explicit_violations,
            **standard,
''',
)
