#!/usr/bin/env python3
"""Fail-closed expert-report and artifact completeness gate."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
from typing import Any
class ReportIntegrityError(ValueError): pass

def validate(report: dict[str,Any], artifact_dir: Path) -> dict[str,Any]:
    required=['task_id','final_status','judge_report','call_ledger','expert_outputs','manifest']
    missing=[k for k in required if not report.get(k)]
    if missing: raise ReportIntegrityError(f'missing report fields: {missing}')
    if report['final_status']!='EXPERT_TEAM_COMPLETED': raise ReportIntegrityError('business completion status is not complete')
    if not isinstance(report['expert_outputs'],list) or len(report['expert_outputs'])!=3 or any(not str(v).strip() for v in report['expert_outputs']): raise ReportIntegrityError('requires 3/3 non-empty expert outputs')
    if not str(report['judge_report']).strip(): raise ReportIntegrityError('judge report empty')
    if not isinstance(report['call_ledger'],list) or len(report['call_ledger'])<4: raise ReportIntegrityError('call ledger incomplete')
    manifest=report['manifest']; files=manifest.get('files') if isinstance(manifest,dict) else None
    if not isinstance(files,list): raise ReportIntegrityError('manifest files missing')
    verified=0
    for row in files:
        path=artifact_dir/str(row['path'])
        if not path.is_file(): raise ReportIntegrityError(f'artifact missing: {row["path"]}')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if digest!=row['sha256']: raise ReportIntegrityError(f'artifact hash mismatch: {row["path"]}')
        verified+=1
    return {'schema_version':'expert-report-integrity-v1','status':'PASS','expert_count':3,'call_count':len(report['call_ledger']),'verified_artifact_files':verified}
