from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one exact block in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


PATH = "open-model-market/v5_task_constraints.py"
TEST = "tests/test_v5_fact_provenance_semantic_normalization.py"

replace_once(
    PATH,
    '''_QUANTITY_MAJOR_FRAGMENT_RE = re.compile(
''',
    '''_SPATIAL_ANCHOR_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("east", ("东侧", "东边", "东门", "东口", "东部")),
    ("west", ("西侧", "西边", "西门", "西口", "西部")),
    ("south", ("南侧", "南边", "南门", "南口", "南部")),
    ("north", ("北侧", "北边", "北门", "北口", "北部")),
    ("inside", ("门内", "室内", "内部", "场内")),
    ("outside", ("门外", "室外", "外部", "场外")),
    ("upstairs", ("楼上", "上层", "上游")),
    ("downstairs", ("楼下", "下层", "下游")),
    ("left", ("左侧", "左边")),
    ("right", ("右侧", "右边")),
)


def _spatial_anchors(value: str) -> set[str]:
    rendered = str(value or "")
    return {
        name
        for name, variants in _SPATIAL_ANCHOR_GROUPS
        if any(variant in rendered for variant in variants)
    }


def _spatially_compatible(claim: str, source: str) -> bool:
    claim_anchors = _spatial_anchors(claim)
    if not claim_anchors:
        return True
    source_anchors = _spatial_anchors(source)
    return claim_anchors.issubset(source_anchors)


_QUANTITY_MAJOR_FRAGMENT_RE = re.compile(
''',
)

replace_once(
    PATH,
    '''            "quantity_skeleton": _quantity_skeleton(fragment),
''',
    '''            "quantity_skeleton": _quantity_skeleton(fragment),
            "spatial_anchors": _spatial_anchors(fragment),
''',
)

replace_once(
    PATH,
    '''        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(set(row["quantities"]))
        ]
''',
    '''        claim_spatial_anchors = _spatial_anchors(fragment)
        generic_compatible = [
            row
            for row in compatible
            if (
                not generic_quantities
                or generic_quantities.issubset(set(row["quantities"]))
            )
            and (
                not claim_spatial_anchors
                or claim_spatial_anchors.issubset(set(row["spatial_anchors"]))
            )
        ]
''',
)

replace_once(
    PATH,
    '''                for row in compatible
            )
''',
    '''                for row in compatible
                if _spatially_compatible(fragment, str(row["raw"]))
            )
''',
)

replace_once(
    PATH,
    '''def _quantity_contexts_match(
    claim_contexts: Sequence[str],
    source_contexts: Sequence[str],
) -> bool:
''',
    '''def _quantity_contexts_match(
    claim_contexts: Sequence[str],
    source_contexts: Sequence[str],
) -> bool:
''',
)

replace_once(
    PATH,
    '''        for source_context in source_contexts:
            if not source_context:
                continue
''',
    '''        for source_context in source_contexts:
            if not source_context:
                continue
            if not _spatially_compatible(claim_context, source_context):
                continue
''',
)

replace_once(
    TEST,
    '''    def test_task_anchored_risk_synthesis_is_relabelled_as_inference(self) -> None:
''',
    '''    def test_spatial_anchor_swap_is_rejected(self) -> None:
        rejected = (
            "东侧出口外有不明液体",
            "西侧出口外地面干燥但有玻璃碎片",
            "门内有2名设备巡检人员",
        )
        for claim in rejected:
            with self.subTest(claim=claim):
                self.assertFalse(fact_claim_supported(TASK, claim))
                self.assertTrue(
                    validate_answer_evidence(TASK, f"- 事实：{claim}。\\n")
                )

    def test_task_anchored_risk_synthesis_is_relabelled_as_inference(self) -> None:
''',
)
