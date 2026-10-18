"""Tests for lakehouse.decontaminate. Deterministic; no I/O."""

import pytest

from lakehouse.decontaminate import (
    DecontaminationReport,
    NearDuplicateIndex,
    Removal,
    decontaminate,
    jaccard,
    minhash_jaccard,
    minhash_signature,
    normalize_text,
    shingles,
)


def row(rid, text):
    return {"id": rid, "text": text}


EVAL_SENTENCE = "the quick brown fox jumps over the lazy dog near the river bank"


# ------------------------------------------------------------- primitives
def test_normalize_text_collapses_case_and_whitespace():
    assert normalize_text("  Hello   WORLD\n") == "hello world"


def test_shingles_are_word_kgrams():
    sh = shingles("a b c d e f", k=3)
    assert sh == frozenset({"a b c", "b c d", "c d e", "d e f"})


def test_shingles_short_text_yields_single_shingle():
    assert shingles("hello world", k=5) == frozenset({"hello world"})


def test_shingles_bad_k_rejected():
    with pytest.raises(ValueError):
        shingles("a b c", k=0)


def test_jaccard_exact_and_disjoint():
    assert jaccard(shingles("a b c"), shingles("a b c")) == 1.0
    assert jaccard(shingles("a b c"), shingles("x y z")) == 0.0


def test_jaccard_empty_sets():
    assert jaccard(frozenset(), frozenset()) == 1.0
    assert jaccard(frozenset({"a"}), frozenset()) == 0.0


def test_minhash_signature_deterministic_across_calls():
    sh = shingles(EVAL_SENTENCE)
    assert minhash_signature(sh) == minhash_signature(sh)
    assert len(minhash_signature(sh, num_hashes=32)) == 32


def test_minhash_jaccard_approximates_true_jaccard():
    a = shingles(EVAL_SENTENCE)
    b = shingles("a quick brown fox jumps over the lazy dog near the river bank")
    est = minhash_jaccard(minhash_signature(a), minhash_signature(b))
    true = jaccard(a, b)
    assert abs(est - true) < 0.15


def test_minhash_jaccard_rejects_mismatched_signatures():
    with pytest.raises(ValueError):
        minhash_jaccard(minhash_signature(shingles("a b")), ())


# ------------------------------------------------------------- index
def test_index_finds_exact_match_case_insensitive():
    idx = NearDuplicateIndex()
    idx.add("e1", EVAL_SENTENCE)
    hits = idx.query("  THE Quick Brown Fox Jumps Over The Lazy Dog Near The River Bank ")
    assert hits == [("e1", 1.0)]


def test_index_finds_paraphrase_near_duplicate():
    idx = NearDuplicateIndex(threshold=0.8)
    idx.add("e1", EVAL_SENTENCE)
    hits = idx.query("a quick brown fox jumps over the lazy dog near the river bank")
    assert len(hits) == 1
    assert hits[0][0] == "e1"
    assert hits[0][1] == pytest.approx(0.8)


def test_index_ignores_distinct_text():
    idx = NearDuplicateIndex()
    idx.add("e1", EVAL_SENTENCE)
    assert idx.query("quantum entanglement enables instantaneous correlation") == []


def test_index_threshold_filters_weak_matches():
    idx = NearDuplicateIndex(threshold=0.95)
    idx.add("e1", EVAL_SENTENCE)
    # paraphrase scores 0.8 < 0.95 -> no hit
    assert idx.query("a quick brown fox jumps over the lazy dog near the river bank") == []


def test_index_bad_threshold_rejected():
    with pytest.raises(ValueError):
        NearDuplicateIndex(threshold=0.0)
    with pytest.raises(ValueError):
        NearDuplicateIndex(threshold=1.5)


# ------------------------------------------------------------- decontaminate
def test_decontaminate_removes_exact_leak():
    train = [row("t1", "Photosynthesis converts sunlight into energy"), row("t2", EVAL_SENTENCE)]
    evals = [row("e1", EVAL_SENTENCE.upper())]
    kept, report = decontaminate(train, evals)
    assert [r["id"] for r in kept] == ["t1"]
    assert len(report.removed) == 1
    rem = report.removed[0]
    assert (rem.train_id, rem.eval_id, rem.kind) == ("t2", "e1", "exact")
    assert rem.score == 1.0


def test_decontaminate_removes_paraphrase_leak():
    train = [
        row("t1", "a quick brown fox jumps over the lazy dog near the river bank"),
        row("t2", "completely unrelated sentence about databases"),
    ]
    evals = [row("e9", EVAL_SENTENCE)]
    kept, report = decontaminate(train, evals, threshold=0.8)
    assert [r["id"] for r in kept] == ["t2"]
    assert len(report.removed) == 1
    assert report.removed[0].kind == "near"
    assert report.removed[0].score >= 0.8


def test_decontaminate_removes_seeded_mixed_leak_fully():
    # planted leak: one exact copy + one paraphrase + clean rows
    train = [
        row("t1", "clean row about oak trees"),
        row("t2", EVAL_SENTENCE),  # exact leak
        row("t3", "a quick brown fox jumps over the lazy dog near the river bank"),  # near leak
        row("t4", "clean row about pine trees"),
    ]
    evals = [row("e1", EVAL_SENTENCE)]
    kept, report = decontaminate(train, evals)
    assert [r["id"] for r in kept] == ["t1", "t4"]
    assert {r.train_id for r in report.removed} == {"t2", "t3"}
    assert report.kept == 2
    assert report.train_total == 4 and report.eval_total == 1


def test_decontaminate_leaves_clean_train_untouched():
    train = [row("t1", "oak trees grow slowly"), row("t2", "rivers flow to the sea")]
    evals = [row("e1", EVAL_SENTENCE)]
    kept, report = decontaminate(train, evals)
    assert kept == train  # same dicts, same order
    assert report.removed == []


def test_decontaminate_cites_best_match_on_multiple_evals():
    train = [row("t1", EVAL_SENTENCE)]
    evals = [row("e1", "unrelated eval text here"), row("e2", EVAL_SENTENCE)]
    kept, report = decontaminate(train, evals)
    assert kept == []
    assert report.removed[0].eval_id == "e2"
    assert report.removed[0].score == 1.0


def test_decontaminate_custom_keys():
    train = [{"qid": "q1", "prompt": EVAL_SENTENCE}]
    evals = [{"qid": "q9", "prompt": EVAL_SENTENCE}]
    kept, report = decontaminate(train, evals, id_key="qid", text_key="prompt")
    assert kept == []
    assert report.removed[0].train_id == "q1"


def test_decontaminate_does_not_mutate_rows():
    train = [row("t1", EVAL_SENTENCE)]
    snapshot = [dict(r) for r in train]
    decontaminate(train, [row("e1", EVAL_SENTENCE)])
    assert train == snapshot


def test_decontaminate_empty_inputs():
    kept, report = decontaminate([], [row("e1", EVAL_SENTENCE)])
    assert kept == [] and report.removed == []
    kept, report = decontaminate([row("t1", "something")], [])
    assert [r["id"] for r in kept] == ["t1"]


def test_removal_dataclass_fields():
    r = Removal(train_id="t", eval_id="e", score=0.9, kind="near")
    assert (r.train_id, r.eval_id, r.score, r.kind) == ("t", "e", 0.9, "near")


def test_report_defaults():
    rep = DecontaminationReport()
    assert rep.removed == [] and rep.kept == 0


def test_full_shingle_overlap_without_equal_text_is_near_not_exact():
    # Cycled text shares every 5-shingle (Jaccard 1.0) but is not the same row.
    base = "alpha beta gamma delta epsilon"
    train = [{"id": "t1", "text": " ".join([base] * 3)}]
    evals = [{"id": "e1", "text": " ".join([base] * 2)}]
    _, report = decontaminate(train, evals)
    assert report.removed[0].score == 1.0
    assert report.removed[0].kind == "near"


def test_exact_kind_ignores_case_and_whitespace():
    train = [{"id": "t1", "text": "  The Quick  brown fox "}]
    evals = [{"id": "e1", "text": "the quick brown fox"}]
    _, report = decontaminate(train, evals)
    assert (report.removed[0].kind, report.removed[0].eval_id) == ("exact", "e1")
