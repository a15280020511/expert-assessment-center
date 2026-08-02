from __future__ import annotations

from pathlib import Path

path = Path("open-model-market/v5_task_constraints.py")
text = path.read_text(encoding="utf-8")
old = '''        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(
                set(row["quantities"]) | set(row["contextual_quantities"])
            )
        ]
        if any(
'''
new = '''        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(
                set(row["quantities"]) | set(row["contextual_quantities"])
            )
        ]
        if generic_quantities and not _quantity_bindings_supported(fragment, task):
            return False
        if any(
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one generic-compatible block, found {count}")
text = text.replace(old, new, 1)
old_late = '''        if generic_quantities and _quantity_bindings_supported(fragment, task):
            continue

'''
count = text.count(old_late)
if count != 1:
    raise SystemExit(f"expected one late quantity binding block, found {count}")
path.write_text(text.replace(old_late, "", 1), encoding="utf-8")
