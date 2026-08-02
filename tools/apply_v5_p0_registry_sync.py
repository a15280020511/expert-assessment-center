#!/usr/bin/env python3
from pathlib import Path

path = Path('.github/workflows/validate.yml')
text = path.read_text(encoding='utf-8')
replacements = [
    (
        '            tests/test_v5_independent_artifact_revalidation.py\n',
        '            tests/test_v5_independent_artifact_revalidation.py\n'
        '            tests/test_v5_failure_evidence_persistence.py\n',
    ),
    (
        '          grep -q "REGISTERED IndependentArtifactRevalidationTests: 3" validation-logs/p0-regressions.log\n',
        '          grep -q "REGISTERED IndependentArtifactRevalidationTests: 4" validation-logs/p0-regressions.log\n'
        '          grep -q "REGISTERED FailureEvidencePersistenceTests: 1" validation-logs/p0-regressions.log\n',
    ),
    (
        '          grep -q "REGISTERED TOTAL: 50" validation-logs/p0-regressions.log\n',
        '          grep -q "REGISTERED TOTAL: 52" validation-logs/p0-regressions.log\n',
    ),
    (
        '          grep -q "P0 REGRESSION RESULT: run=50, passed=50, failures=0, errors=0, skipped=0" \\\n',
        '          grep -q "P0 REGRESSION RESULT: run=52, passed=52, failures=0, errors=0, skipped=0" \\\n',
    ),
]
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f'expected exactly one match, found {count}: {old!r}')
    text = text.replace(old, new)
path.write_text(text, encoding='utf-8')
