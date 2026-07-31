#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: maintenance_validate_v5_fix.sh <worktree>" >&2
  exit 2
fi

worktree="$(cd "$1" && pwd)"
cd "$worktree"

python -m py_compile open-model-market/*.py tests/*.py tools/*.py
python -m pip install --disable-pip-version-check --no-input -r requirements-dev.txt
python -m pip check
python -c "from ortools.sat.python import cp_model; print(cp_model.CpModel())"
python -m ruff check --select E9,F63,F7,F82 open-model-market tests tools
python -m unittest discover -s tests -v

set -o pipefail
python tools/run_v5_p0_regressions.py 2>&1 | tee /tmp/p0-regressions.log
grep -q "REGISTERED V5QualityStatusIntegrityTests: 6" /tmp/p0-regressions.log
grep -q "REGISTERED TOTAL: 25" /tmp/p0-regressions.log
grep -q "P0 REGRESSION RESULT: run=25, passed=25, failures=0, errors=0, skipped=0" /tmp/p0-regressions.log

python open-model-market/v5_pipeline.py \
  --task "比较三个城市公共投资方案，完成财务建模、政策与法律合规、证据核验、预测推演、独立红队反证和最终决策。" \
  --catalog-file tests/fixtures/models.json \
  --endpoint-file tests/fixtures/endpoints.json \
  --dry-run \
  --maximum-total-calls 16 \
  --maximum-recovery-calls 2 \
  --quality-tier quality \
  --output-dir validation-artifacts

test -s validation-artifacts/v5-execution-graph.json
test -s validation-artifacts/v5-planning-benchmark.json
test -s validation-artifacts/artifact-manifest.json

python tools/repository_audit.py --root . --output-dir audit-artifacts --fail-on high
