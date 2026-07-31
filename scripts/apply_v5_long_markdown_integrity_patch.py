from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "open-model-market/v5_runtime.py",
    '''        for line in answer.splitlines():
            match = re.match(r"^\\s{0,3}#{1,6}\\s+(.+?)\\s*#*\\s*$", line)
            if match:
                heading = cls._normalized_contract_field(match.group(1))
                current = next(
                    (
                        original
                        for normalized, original in required_by_name.items()
                        if heading == normalized or heading.startswith(normalized + "_")
                    ),
                    None,
                )
                if current is not None:
                    sections.setdefault(current, [])
                continue
            if current is not None:
                sections[current].append(line)
''',
    '''        for line in answer.splitlines():
            match = re.match(r"^\\s{0,3}(#{1,6})\\s+(.+?)\\s*#*\\s*$", line)
            if match and len(match.group(1)) == 2:
                heading = cls._normalized_contract_field(match.group(2))
                current = next(
                    (
                        original
                        for normalized, original in required_by_name.items()
                        if heading == normalized or heading.startswith(normalized + "_")
                    ),
                    None,
                )
                if current is not None:
                    sections.setdefault(current, [])
                continue
            if current is not None:
                sections[current].append(line)
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''        explicit_violations = task_delivery_contract.validate_parsed_contract(
            parsed, node.output_contract
        )
        complete = (
            (not required or all(populated(field) for field in required))
            and not explicit_violations
        )
''',
    '''        explicit_violations = task_delivery_contract.validate_parsed_contract(
            parsed, node.output_contract
        )
        markdown_violations = task_delivery_contract.validate_markdown_contract(
            answer or "", node.output_contract
        )
        contract_violations = list(dict.fromkeys(
            [*explicit_violations, *markdown_violations]
        ))
        complete = (
            (not required or all(populated(field) for field in required))
            and not contract_violations
        )
''',
)
replace_once(
    "open-model-market/v5_runtime.py",
    '''            "contract_violations": explicit_violations,
''',
    '''            "contract_violations": contract_violations,
''',
)

replace_once(
    "open-model-market/v5_execution_auditor_integrity.py",
    '''    if result["status"] == "PASS":
        result["primary_failure"] = {
            "code": "NONE",
            "stage": "completed",
            "message": "",
            "retryable": False,
        }
    elif result["failures"]:
''',
    '''    if result["status"] == "PASS":
        result["primary_failure"] = {
            "code": "NONE",
            "stage": "completed",
            "message": "",
            "retryable": False,
        }
    elif result["status"] == "DEGRADED":
        result["primary_failure"] = {
            "code": "DEGRADED_SUCCESS",
            "stage": "quality-integrity",
            "message": result["degradations"][0] if result["degradations"] else "bounded degradation",
            "retryable": False,
        }
    elif result["failures"]:
''',
)
