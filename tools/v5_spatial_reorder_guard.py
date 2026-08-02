from __future__ import annotations

from pathlib import Path

path = Path("open-model-market/v5_task_constraints.py")
text = path.read_text(encoding="utf-8")
old = '''        if any(
            SequenceMatcher(None, normalized, str(row["normalized"])).ratio() >= 0.72
            for row in generic_compatible
        ):
            continue

        claim_quantities = generic_quantities
'''
new = '''        if any(
            SequenceMatcher(None, normalized, str(row["normalized"])).ratio() >= 0.72
            for row in generic_compatible
        ):
            continue
        claim_skeleton = _quantity_skeleton(fragment)
        if any(
            bool(claim_skeleton)
            and bool(source_skeleton := str(row["quantity_skeleton"]))
            and (
                claim_skeleton in source_skeleton
                or source_skeleton in claim_skeleton
                or (
                    _ngram_coverage(claim_skeleton, source_skeleton, 2) >= 0.72
                    and _ngram_coverage(claim_skeleton, source_skeleton, 3) >= 0.42
                )
            )
            for row in generic_compatible
        ):
            continue

        claim_quantities = generic_quantities
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one generic similarity block, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
