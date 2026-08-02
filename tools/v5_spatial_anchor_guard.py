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
    ("upstairs", ("楼上",)),
    ("downstairs", ("楼下",)),
    ("left", ("左侧", "左边")),
    ("right", ("右侧", "右边")),
)
_MAJOR_EVIDENCE_FRAGMENT_RE = re.compile(r"[。！？!?；;|\\n]+")


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
    return claim_anchors.issubset(_spatial_anchors(source))


def _major_evidence_fragments(value: str) -> list[str]:
    return [
        fragment.strip()
        for fragment in _MAJOR_EVIDENCE_FRAGMENT_RE.split(str(value or ""))
        if fragment.strip()
    ]


_QUANTITY_MAJOR_FRAGMENT_RE = re.compile(
''',
)

replace_once(
    PATH,
    '''        for source_context in source_contexts:
            if not source_context:
                continue
            if claim_context in source_context:
''',
    '''        for source_context in source_contexts:
            if not source_context:
                continue
            if not _spatially_compatible(claim_context, source_context):
                continue
            if claim_context in source_context:
''',
)

old_claim_supported = '''def _claim_supported(claim: str, task: str) -> bool:
    source_fragments = _evidence_fragments(task, include_whole=True)
    source_rows = [
        {
            "raw": fragment,
            "normalized": _normalize_claim(fragment),
            "polarity": _negation_polarity(fragment),
            "quantities": normalized_quantities(fragment),
            "quantity_skeleton": _quantity_skeleton(fragment),
        }
        for fragment in source_fragments
    ]
    for fragment in _evidence_fragments(claim, include_whole=False):
        normalized = _normalize_claim(fragment)
        if not normalized:
            continue
        polarity = _negation_polarity(fragment)
        compatible = [
            row
            for row in source_rows
            if row["normalized"] and polarity == row["polarity"]
        ]
        generic_quantities = normalized_quantities(fragment)
        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(set(row["quantities"]))
        ]
        if any(
            normalized in str(row["normalized"])
            for row in generic_compatible
        ):
            continue
        if any(
            SequenceMatcher(None, normalized, str(row["normalized"])).ratio() >= 0.72
            for row in generic_compatible
        ):
            continue

        claim_quantities = generic_quantities
        if claim_quantities and _quantity_bindings_supported(fragment, task):
            continue

        if polarity in {"unknown", "absence", "negative"}:
            claim_core = _semantic_core(fragment, polarity)
            polarity_supported = any(
                bool(claim_core)
                and bool(source_core := _semantic_core(str(row["raw"]), polarity))
                and (
                    claim_core in source_core
                    or source_core in claim_core
                    or SequenceMatcher(None, claim_core, source_core).ratio() >= 0.82
                )
                for row in compatible
            )
            if polarity_supported:
                continue
        return False
    return True
'''

new_claim_supported = '''def _source_evidence_rows(task: str) -> list[dict[str, Any]]:
    """Build clause-local rows so split fragments inherit only their sentence context."""
    rows: list[dict[str, Any]] = []
    for context in _major_evidence_fragments(task):
        contextual_normalized = _normalize_claim(context)
        contextual_quantities = normalized_quantities(context)
        contextual_skeleton = _quantity_skeleton(context)
        contextual_anchors = _spatial_anchors(context)
        for fragment in _evidence_fragments(context, include_whole=True):
            rows.append(
                {
                    "raw": fragment,
                    "context_raw": context,
                    "normalized": _normalize_claim(fragment),
                    "contextual_normalized": contextual_normalized,
                    "polarity": _negation_polarity(fragment),
                    "quantities": normalized_quantities(fragment),
                    "contextual_quantities": contextual_quantities,
                    "quantity_skeleton": _quantity_skeleton(fragment),
                    "contextual_quantity_skeleton": contextual_skeleton,
                    "spatial_anchors": contextual_anchors or _spatial_anchors(fragment),
                }
            )
    return rows


def _semantic_reorder_supported(fragment: str, row: Mapping[str, Any]) -> bool:
    if not _spatially_compatible(fragment, str(row.get("context_raw", ""))):
        return False
    claim_skeleton = _quantity_skeleton(fragment)
    if not claim_skeleton:
        return False
    source_skeletons = tuple(
        dict.fromkeys(
            value
            for value in (
                str(row.get("quantity_skeleton", "")),
                str(row.get("contextual_quantity_skeleton", "")),
            )
            if value
        )
    )
    return any(
        claim_skeleton in source_skeleton
        or source_skeleton in claim_skeleton
        or SequenceMatcher(None, claim_skeleton, source_skeleton).ratio() >= 0.72
        or (
            _ngram_coverage(claim_skeleton, source_skeleton, 2) >= 0.72
            and _ngram_coverage(claim_skeleton, source_skeleton, 3) >= 0.42
        )
        for source_skeleton in source_skeletons
    )


def _claim_supported(claim: str, task: str) -> bool:
    source_rows = _source_evidence_rows(task)
    for fragment in _evidence_fragments(claim, include_whole=False):
        normalized = _normalize_claim(fragment)
        if not normalized:
            continue
        polarity = _negation_polarity(fragment)
        compatible = [
            row
            for row in source_rows
            if row["normalized"]
            and polarity == row["polarity"]
            and _spatially_compatible(fragment, str(row["context_raw"]))
        ]
        generic_quantities = normalized_quantities(fragment)
        generic_compatible = [
            row
            for row in compatible
            if not generic_quantities
            or generic_quantities.issubset(
                set(row["quantities"]) | set(row["contextual_quantities"])
            )
        ]
        if any(
            normalized in str(row["normalized"])
            or normalized in str(row["contextual_normalized"])
            for row in generic_compatible
        ):
            continue
        if any(
            SequenceMatcher(None, normalized, candidate).ratio() >= 0.72
            for row in generic_compatible
            for candidate in (
                str(row["normalized"]),
                str(row["contextual_normalized"]),
            )
            if candidate
        ):
            continue
        if any(
            _semantic_reorder_supported(fragment, row)
            for row in generic_compatible
        ):
            continue

        if generic_quantities and _quantity_bindings_supported(fragment, task):
            continue

        if polarity in {"unknown", "absence", "negative"}:
            claim_core = _semantic_core(fragment, polarity)
            polarity_supported = any(
                bool(claim_core)
                and bool(source_core := _semantic_core(str(row["raw"]), polarity))
                and (
                    claim_core in source_core
                    or source_core in claim_core
                    or SequenceMatcher(None, claim_core, source_core).ratio() >= 0.82
                )
                for row in compatible
            )
            if polarity_supported:
                continue
        return False
    return True
'''
replace_once(PATH, old_claim_supported, new_claim_supported)

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

    def test_clause_context_preserves_safe_word_reordering(self) -> None:
        task = "门外有2名无法核验身份、自称维修人员的人要求进入。"
        claim = "门外有自称维修人员要求进入且无法核验身份"
        self.assertTrue(fact_claim_supported(task, claim))
        self.assertEqual(
            [],
            validate_answer_evidence(task, f"- 事实：{claim}。\\n"),
        )

    def test_task_anchored_risk_synthesis_is_relabelled_as_inference(self) -> None:
''',
)
