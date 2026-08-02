from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one exact block in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


SOURCE = "open-model-market/v5_deterministic_answer_normalization.py"
TEST = "tests/test_v5_fact_provenance_semantic_normalization.py"

replace_once(
    SOURCE,
    '''def _inferential_relabel_allowed(task: str, body: str) -> bool:
    """Allow label-only repair only for task-anchored inferential synthesis."""
    if not _INFERENTIAL_FACT_RE.search(body):
        return False
    if normalized_quantities(body) - normalized_quantities(task):
        return False
    task_bigrams = _cjk_ngrams(task, 2)
    body_bigrams = _cjk_ngrams(body, 2)
    task_fourgrams = _cjk_ngrams(task, 4)
    body_fourgrams = _cjk_ngrams(body, 4)
    return (
        len(task_bigrams & body_bigrams) >= 6
        and len(task_fourgrams & body_fourgrams) >= 1
    )
''',
    '''_INFERENCE_CLAUSE_RE = re.compile(
    r"[，,；;。|、]+|(?:以及|并且|同时|和|与)",
    re.IGNORECASE,
)
_INFERENCE_GENERIC_ANCHOR_TERMS = (
    "当前",
    "存在",
    "风险",
    "隐患",
    "受限",
    "缺口",
    "不足",
    "严重",
    "可能",
    "潜在",
    "试图",
    "资源",
    "资产",
    "外部",
    "核心",
    "首要",
)
_INFERENCE_GENERIC_BIGRAMS = set().union(
    *(_cjk_ngrams(term, 2) for term in _INFERENCE_GENERIC_ANCHOR_TERMS)
)


def _inferential_relabel_allowed(task: str, body: str) -> bool:
    """Allow label-only repair only for task-anchored inferential synthesis."""
    if not _INFERENTIAL_FACT_RE.search(body):
        return False
    if normalized_quantities(body) - normalized_quantities(task):
        return False
    task_bigrams = _cjk_ngrams(task, 2)
    body_bigrams = _cjk_ngrams(body, 2)
    task_fourgrams = _cjk_ngrams(task, 4)
    body_fourgrams = _cjk_ngrams(body, 4)
    strict_overlap = (
        len(task_bigrams & body_bigrams) >= 6
        and len(task_fourgrams & body_fourgrams) >= 1
    )
    if strict_overlap:
        return True

    task_anchors = task_bigrams - _INFERENCE_GENERIC_BIGRAMS
    body_anchors = body_bigrams - _INFERENCE_GENERIC_BIGRAMS
    shared_anchors = task_anchors & body_anchors
    clauses = [
        clause.strip()
        for clause in _INFERENCE_CLAUSE_RE.split(str(body or ""))
        if clause.strip()
    ]
    anchored_clauses = sum(
        bool(
            (_cjk_ngrams(clause, 2) - _INFERENCE_GENERIC_BIGRAMS)
            & task_anchors
        )
        for clause in clauses
    )
    return len(shared_anchors) >= 3 and anchored_clauses >= 2
''',
)

replace_once(
    TEST,
    '''    def test_unrelated_external_claim_is_not_relabelled(self) -> None:
''',
    '''    def test_compact_task_anchored_risk_synthesis_is_relabelled(self) -> None:
        answer = (
            "- 事实：当前存在双侧出口隐患、外部未核验人员试图进入、"
            "资产记录缺口以及通信与照明资源受限。\\n"
        )
        normalized, audit = normalize_answer(
            TASK,
            answer,
            {},
            compile_task_constraints(TASK),
        )
        self.assertIn("- 推断：当前存在双侧出口隐患", normalized)
        self.assertNotIn("- 事实：当前存在双侧出口隐患", normalized)
        self.assertEqual(1, len(audit["inferential_fact_labels_relabelled"]))
        self.assertEqual([], validate_answer_evidence(TASK, normalized))
        self.assertFalse(audit["substantive_text_invented"])

    def test_unrelated_external_claim_is_not_relabelled(self) -> None:
''',
)
