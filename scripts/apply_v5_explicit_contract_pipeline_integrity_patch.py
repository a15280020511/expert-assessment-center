from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "open-model-market/v5_task_delivery_contract.py",
    "import json\nimport re\n",
    "import json\nimport re\nfrom hashlib import sha256\n",
)
replace_once(
    "open-model-market/v5_task_delivery_contract.py",
    '''def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def _normalized_heading(value: str) -> str:
''',
    '''def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Mapping):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return bool(value)
    return True


def explicit_contract_kind(contract: Mapping[str, Any]) -> str:
    if contract.get("explicit_user_contract"):
        return "exact-json"
    if contract.get("explicit_markdown_contract"):
        return "exact-markdown"
    return "generic"


def contract_digest(contract: Mapping[str, Any]) -> str:
    """Hash the full semantic contract; list order remains significant."""
    canonical = json.dumps(
        dict(contract),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def contract_integrity_profile(
    contract: Mapping[str, Any],
    source_work_ids: Sequence[str],
) -> dict[str, Any]:
    kind = explicit_contract_kind(contract)
    return {
        "output_contract_integrity_required": True,
        "output_contract_integrity_sha256": contract_digest(contract),
        "output_contract_kind": kind,
        "explicit_output_contract_expected": kind in {"exact-json", "exact-markdown"},
        "output_contract_source_work_ids": sorted({str(value) for value in source_work_ids}),
    }


def validate_contract_integrity(
    contract: Mapping[str, Any],
    parameter_profile: Mapping[str, Any],
) -> list[str]:
    """Detect any contract loss or reordering across planning/runtime layers."""
    required = bool(parameter_profile.get("output_contract_integrity_required"))
    expected_digest = str(
        parameter_profile.get("output_contract_integrity_sha256") or ""
    )
    if not required and not expected_digest:
        return []
    violations: list[str] = []
    if not expected_digest:
        violations.append("output-contract-integrity-digest-missing")
        return violations

    expected_kind = str(parameter_profile.get("output_contract_kind") or "")
    actual_kind = explicit_contract_kind(contract)
    if expected_kind and expected_kind != actual_kind:
        violations.append(
            f"output-contract-kind-mismatch:{expected_kind}:{actual_kind}"
        )
    if parameter_profile.get("explicit_output_contract_expected") and actual_kind == "generic":
        violations.append("explicit-output-contract-metadata-stripped")
    if contract_digest(contract) != expected_digest:
        violations.append("output-contract-integrity-sha256-mismatch")

    required_fields = [str(value) for value in contract.get("required_fields", [])]
    if actual_kind == "exact-json":
        exact = [str(value) for value in contract.get("exact_top_level_fields", [])]
        if required_fields != exact:
            violations.append("exact-json-required-field-order-or-content-mismatch")
    elif actual_kind == "exact-markdown":
        exact = [str(value) for value in contract.get("exact_markdown_headings", [])]
        if required_fields != exact:
            violations.append("exact-markdown-required-heading-order-or-content-mismatch")
    return list(dict.fromkeys(violations))


def _normalized_heading(value: str) -> str:
''',
)

replace_once(
    "open-model-market/v5_planner.py",
    "from openrouter_api import OpenRouterRequestError, request_json\n",
    "from openrouter_api import OpenRouterRequestError, request_json\nimport v5_task_delivery_contract as task_delivery_contract\n",
)
replace_once(
    "open-model-market/v5_planner.py",
    '''def _merge_output_contract(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = sorted({field for work in works for field in work.get("output_contract", {}).get("required_fields", [])})
    return {
        "required_fields": fields,
        "machine_readable_required": any(bool(work.get("output_contract", {}).get("machine_readable_required")) for work in works),
        "must_separate_fact_assumption_inference": True,
    }
''',
    '''def _merge_output_contract(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contracts = [
        dict(work.get("output_contract", {}))
        for work in works
        if isinstance(work.get("output_contract", {}), Mapping)
    ]
    explicit = [
        contract
        for contract in contracts
        if task_delivery_contract.explicit_contract_kind(contract) != "generic"
    ]
    if explicit:
        reference = explicit[0]
        reference_digest = task_delivery_contract.contract_digest(reference)
        for contract in explicit[1:]:
            if task_delivery_contract.contract_digest(contract) != reference_digest:
                raise V5PlanningError(
                    "Conflicting explicit user output contracts cannot be bundled."
                )
        merged = json.loads(json.dumps(reference, ensure_ascii=False))
        kind = task_delivery_contract.explicit_contract_kind(merged)
        if kind == "exact-json":
            merged["required_fields"] = list(merged.get("exact_top_level_fields", []))
            merged["machine_readable_required"] = True
        elif kind == "exact-markdown":
            merged["required_fields"] = list(merged.get("exact_markdown_headings", []))
            merged["machine_readable_required"] = False
        merged["must_separate_fact_assumption_inference"] = any(
            bool(contract.get("must_separate_fact_assumption_inference"))
            for contract in contracts
        )
        return merged

    fields: list[str] = []
    for contract in contracts:
        for field in contract.get("required_fields", []):
            value = str(field)
            if value not in fields:
                fields.append(value)
    return {
        "required_fields": fields,
        "machine_readable_required": any(
            bool(contract.get("machine_readable_required"))
            for contract in contracts
        ),
        "must_separate_fact_assumption_inference": any(
            bool(contract.get("must_separate_fact_assumption_inference"))
            for contract in contracts
        ),
    }
''',
)
replace_once(
    "open-model-market/v5_planner.py",
    '''    parameters = _parameter_profile(endpoint, works, reasoning)
    assigned = tuple(sorted(str(work["work_id"]) for work in works))
''',
    '''    parameters = _parameter_profile(endpoint, works, reasoning)
    output_contract = _merge_output_contract(works)
    parameters = {
        **parameters,
        **task_delivery_contract.contract_integrity_profile(
            output_contract,
            [str(work["work_id"]) for work in works],
        ),
    }
    assigned = tuple(sorted(str(work["work_id"]) for work in works))
''',
)
replace_once(
    "open-model-market/v5_planner.py",
    "        output_contract=_merge_output_contract(works),\n",
    "        output_contract=output_contract,\n",
)

replace_once(
    "open-model-market/v5_output_contract_delivery.py",
    '''    passed, score, reasons = _ORIGINAL_QUALITY_GATE(node, response, answer)
    markdown_violations = task_delivery_contract.validate_markdown_contract(
        answer, node.output_contract
    )
''',
    '''    passed, score, reasons = _ORIGINAL_QUALITY_GATE(node, response, answer)
    integrity_violations = task_delivery_contract.validate_contract_integrity(
        node.output_contract, node.parameter_profile
    )
    for violation in integrity_violations:
        _append_reason(reasons, violation)
    if integrity_violations:
        passed = False
        score = min(float(score), 0.0)
    markdown_violations = task_delivery_contract.validate_markdown_contract(
        answer, node.output_contract
    )
''',
)

replace_once(
    "open-model-market/v5_runtime.py",
    '''        contract_violations = list(dict.fromkeys(
            [*explicit_violations, *markdown_violations]
        ))
''',
    '''        integrity_violations = task_delivery_contract.validate_contract_integrity(
            node.output_contract, node.parameter_profile
        )
        contract_violations = list(dict.fromkeys(
            [*integrity_violations, *explicit_violations, *markdown_violations]
        ))
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''    def _preflight(self, graph: ExecutionGraph) -> dict[str, Any]:
        risk_cost = graph.estimated_total_cost * self.config.cost_risk_multiplier
        providers: dict[str, int] = {}
        for node in graph.nodes:
            providers[self._provider(node)] = providers.get(self._provider(node), 0) + 1
        blockers = []
        if (
            self.config.cost_anomaly_usd is not None
            and risk_cost > self.config.cost_anomaly_usd + 1e-12
        ):
            blockers.append("preflight-risk-adjusted-cost-above-anomaly-limit")
        return {
            "status": "rejected" if blockers else "pass",
            "estimated_initial_cost_usd": graph.estimated_total_cost,
            "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
            "cost_anomaly_usd": self.config.cost_anomaly_usd,
            "provider_counts": providers,
            "blockers": blockers,
            "policy": "native-runtime-preflight-before-first-call",
        }
''',
    '''    def _preflight(self, graph: ExecutionGraph) -> dict[str, Any]:
        risk_cost = graph.estimated_total_cost * self.config.cost_risk_multiplier
        providers: dict[str, int] = {}
        blockers: list[str] = []
        contract_integrity: dict[str, list[str]] = {}
        for node in graph.nodes:
            providers[self._provider(node)] = providers.get(self._provider(node), 0) + 1
            violations = task_delivery_contract.validate_contract_integrity(
                node.output_contract, node.parameter_profile
            )
            if violations:
                contract_integrity[node.node_id] = violations
                blockers.append(f"output-contract-integrity:{node.node_id}")
        recovery_pool = graph.metadata.get("recovery_pool", {})
        if isinstance(recovery_pool, Mapping):
            for selected_id, rows in recovery_pool.items():
                if not isinstance(rows, list):
                    continue
                for index, row in enumerate(rows):
                    if not isinstance(row, Mapping):
                        continue
                    violations = task_delivery_contract.validate_contract_integrity(
                        row.get("output_contract", {}),
                        row.get("parameter_profile", {}),
                    )
                    if violations:
                        key = f"recovery:{selected_id}:{index}"
                        contract_integrity[key] = violations
                        blockers.append(f"output-contract-integrity:{key}")
        if (
            self.config.cost_anomaly_usd is not None
            and risk_cost > self.config.cost_anomaly_usd + 1e-12
        ):
            blockers.append("preflight-risk-adjusted-cost-above-anomaly-limit")
        return {
            "status": "rejected" if blockers else "pass",
            "estimated_initial_cost_usd": graph.estimated_total_cost,
            "risk_adjusted_cost_upper_usd": round(risk_cost, 8),
            "cost_anomaly_usd": self.config.cost_anomaly_usd,
            "provider_counts": providers,
            "output_contract_integrity": contract_integrity,
            "blockers": list(dict.fromkeys(blockers)),
            "policy": "native-runtime-preflight-before-first-call",
        }
''',
)
