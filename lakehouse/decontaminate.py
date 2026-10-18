"""Eval-set decontamination: exact + near-duplicate detection.

Training on eval data inflates benchmark scores and hides generalization
failures. This module finds train rows that match eval rows — exactly or
near-duplicately (paraphrases, rewordings) — strips them, and reports every
removal with a match score and kind.

Near-duplicate detection is hand-rolled MinHash (stdlib only): word
k-shingles → md5-seeded hash functions → signatures. Candidates pass a
MinHash similarity prefilter and are confirmed with exact Jaccard
similarity on shingle sets; the reported score is always the exact
Jaccard. md5 (not builtin ``hash()``) keeps signatures deterministic
across processes.

Typical use with a DeltaTable::

    train_rows = train_table.read()
    eval_rows = eval_table.read()
    clean, report = decontaminate(train_rows, eval_rows, text_key="prompt")
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Tuple

_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace for comparison purposes."""
    return _WS_RE.sub(" ", text.strip().lower())


def shingles(text: str, k: int = 5) -> frozenset:
    """Word k-shingles of the normalized text.

    Texts shorter than k words yield a single shingle (the whole text),
    so short rows still participate in matching.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k!r}")
    words = normalize_text(text).split(" ")
    words = [w for w in words if w]
    if len(words) <= k:
        return frozenset([" ".join(words)]) if words else frozenset()
    return frozenset(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


def _hash_int(data: str) -> int:
    return int(hashlib.md5(data.encode("utf-8")).hexdigest(), 16)


def minhash_signature(
    shingle_set: frozenset, num_hashes: int = 64, seed: int = 0
) -> Tuple[int, ...]:
    """MinHash signature: per hash function, the min hash over shingles.

    Deterministic across processes (md5-based, not builtin hash()).
    An empty shingle set yields an all-max signature.
    """
    if num_hashes < 1:
        raise ValueError(f"num_hashes must be >= 1, got {num_hashes!r}")
    if not shingle_set:
        return tuple([2**128 - 1] * num_hashes)
    sig = []
    for i in range(num_hashes):
        best = min(_hash_int(f"{seed}:{i}:{s}") for s in shingle_set)
        sig.append(best)
    return tuple(sig)


def jaccard(a: frozenset, b: frozenset) -> float:
    """Exact Jaccard similarity. Both empty → 1.0; one empty → 0.0."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def minhash_jaccard(sig_a: Tuple[int, ...], sig_b: Tuple[int, ...]) -> float:
    """Estimated Jaccard: fraction of equal MinHash components."""
    if len(sig_a) != len(sig_b) or not sig_a:
        raise ValueError("signatures must be non-empty and the same length")
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)


class NearDuplicateIndex:
    """Lookup index over reference texts for near-duplicate queries.

    Query flow: MinHash signature prefilter (estimated similarity must
    clear ``threshold - slack``) → exact Jaccard confirmation. Only
    confirmed matches at or above ``threshold`` are returned.
    """

    def __init__(
        self,
        k: int = 5,
        num_hashes: int = 64,
        threshold: float = 0.8,
        seed: int = 0,
    ):
        if not 0.0 < threshold <= 1.0:
            raise ValueError(f"threshold must be in (0, 1], got {threshold!r}")
        self.k = k
        self.num_hashes = num_hashes
        self.threshold = threshold
        self.seed = seed
        self._entries: List[Tuple[Any, frozenset, Tuple[int, ...]]] = []

    def add(self, item_id: Any, text: str) -> None:
        sh = shingles(text, self.k)
        sig = minhash_signature(sh, self.num_hashes, self.seed)
        self._entries.append((item_id, sh, sig))

    def query(self, text: str) -> List[Tuple[Any, float]]:
        """Return [(item_id, exact_jaccard)] for matches ≥ threshold.

        Sorted by score descending. An exact normalized-text match always
        scores 1.0.
        """
        sh = shingles(text, self.k)
        sig = minhash_signature(sh, self.num_hashes, self.seed)
        hits: List[Tuple[Any, float]] = []
        for item_id, ref_sh, ref_sig in self._entries:
            if minhash_jaccard(sig, ref_sig) < self.threshold - 0.2:
                continue  # prefilter: cannot plausibly clear the threshold
            score = jaccard(sh, ref_sh)
            if score >= self.threshold:
                hits.append((item_id, score))
        hits.sort(key=lambda h: h[1], reverse=True)
        return hits


@dataclass
class Removal:
    """One train row stripped for matching an eval row."""

    train_id: Any
    eval_id: Any
    score: float  # exact Jaccard similarity of the shingle sets
    kind: str  # "exact" (same normalized text) or "near" (shingle Jaccard >= threshold)


@dataclass
class DecontaminationReport:
    """Summary of a decontaminate() run."""

    removed: List[Removal] = field(default_factory=list)
    train_total: int = 0
    eval_total: int = 0

    @property
    def kept(self) -> int:
        return self.train_total - len(self.removed)


def decontaminate(
    train_rows: List[Dict[str, Any]],
    eval_rows: List[Dict[str, Any]],
    id_key: str = "id",
    text_key: str = "text",
    threshold: float = 0.8,
    k: int = 5,
    seed: int = 0,
) -> Tuple[List[Dict[str, Any]], DecontaminationReport]:
    """Strip train rows matching eval rows (exact or near-duplicate).

    Returns (kept_rows, report). A train row whose normalized text equals an
    eval row is an "exact" removal. Otherwise it is checked against every eval
    row (no LSH banding, so the cost is O(train x eval) signature compares) and
    the highest-scoring match is cited as "near".
    Rows are never mutated — kept rows are the original dicts.
    """
    index = NearDuplicateIndex(k=k, threshold=threshold, seed=seed)
    exact: Dict[str, Any] = {}
    for row in eval_rows:
        index.add(row[id_key], str(row[text_key]))
        exact.setdefault(normalize_text(str(row[text_key])), row[id_key])

    kept: List[Dict[str, Any]] = []
    report = DecontaminationReport(
        train_total=len(train_rows), eval_total=len(eval_rows)
    )
    for row in train_rows:
        text = str(row[text_key])
        norm = normalize_text(text)
        if norm in exact:
            # Same text after normalization. A shingle Jaccard of 1.0 alone
            # is not enough: repeated or cycled text can share every shingle.
            report.removed.append(Removal(row[id_key], exact[norm], 1.0, "exact"))
            continue
        hits = index.query(text)
        if not hits:
            kept.append(row)
            continue
        eval_id, score = hits[0]
        report.removed.append(Removal(row[id_key], eval_id, score, "near"))
    return kept, report
