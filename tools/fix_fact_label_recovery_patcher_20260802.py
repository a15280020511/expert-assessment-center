#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("apply_fact_label_recovery_fix_20260802.py")
text = path.read_text(encoding="utf-8")
old = '''    replace_once(
        path,
        ''' + "'''" + '''        metadata = dict(graph.get(\"metadata\") or {})
''' + "'''" + ''',
        ''' + "'''" + '''        total_recovery_options = sum(
            len(rows) for rows in recovery_pool.values()
        )
        if int(self.config.recovery_call_limit) > 0 and total_recovery_options <= 0:
            raise V5PlanningError(
                \"Recovery reserve is not executable under the absolute cost \"
                \"anomaly guard.\"
            )

        metadata = dict(graph.get(\"metadata\") or {})
''' + "'''" + ''',
    )
'''
new = '''    replace_once(
        path,
        ''' + "'''" + '''            if not progress:
                break

        metadata = dict(graph.get(\"metadata\") or {})
''' + "'''" + ''',
        ''' + "'''" + '''            if not progress:
                break

        total_recovery_options = sum(
            len(rows) for rows in recovery_pool.values()
        )
        if int(self.config.recovery_call_limit) > 0 and total_recovery_options <= 0:
            raise V5PlanningError(
                \"Recovery reserve is not executable under the absolute cost \"
                \"anomaly guard.\"
            )

        metadata = dict(graph.get(\"metadata\") or {})
''' + "'''" + ''',
    )
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one patcher block, found {text.count(old)}")
path.write_text(text.replace(old, new), encoding="utf-8")
