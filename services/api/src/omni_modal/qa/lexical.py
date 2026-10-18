"""Keyword ranking and rank fusion for hybrid retrieval.

Vector search finds passages that mean the same thing; keyword search finds
passages that use the same rare words (names, codes, figures). Hybrid mode
runs both and merges the two rankings with reciprocal rank fusion, which needs
no score calibration between them.

``RETRIEVAL_MODE`` picks the mode: ``hybrid`` (the default, chosen by the
evaluation in ``omni_modal.benchmark.eval``) or ``vector``.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Iterable, Sequence

RRF_K = 60
_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a about above after again all also am an and any are as at be because been before being below between "
    "both but by can could did do does doing down during each few for from further had has have having he her "
    "here hers him his how i if in into is it its itself just me more most my no nor not now of off on once "
    "only or other our ours out over own same she should so some such than that the their them then there "
    "these they this those through to too under until up very was we were what when where which while who "
    "whom why will with would you your yours".split()
)


def retrieval_mode(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    mode = (env.get("RETRIEVAL_MODE") or "hybrid").strip().lower()
    return mode if mode in ("vector", "hybrid") else "hybrid"


def stem(word: str) -> str:
    """A light suffix strip so "approved", "approves" and "approval" meet."""
    for suffix in ("ations", "ation", "ings", "ing", "edly", "ies", "ied", "als", "es", "ed", "ly", "al", "s"):
        if len(word) - len(suffix) >= 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word


def terms(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower()) if w not in STOPWORDS]


def bm25_scores(query: str, documents: Sequence[str], *, k1: float = 1.2, b: float = 0.75) -> list[float]:
    """Okapi BM25 score of each document for the query (0.0 when no term matches)."""
    q_terms = set(terms(query))
    if not q_terms or not documents:
        return [0.0] * len(documents)
    tokenised = [terms(d) for d in documents]
    n = len(tokenised)
    avg_len = sum(len(t) for t in tokenised) / n or 1.0
    df = Counter(term for t in tokenised for term in set(t) if term in q_terms)
    idf = {term: math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5)) for term in df}
    scores = []
    for tokens in tokenised:
        counts = Counter(tokens)
        score = 0.0
        for term, weight in idf.items():
            tf = counts.get(term, 0)
            if tf:
                score += weight * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / avg_len))
        scores.append(score)
    return scores


def tsquery_text(query: str) -> str | None:
    """An OR query for Postgres ``to_tsquery``: any matching word counts, rarer words rank higher.

    Only ``[a-z0-9]`` words go in, so the result is always valid tsquery syntax.
    """
    words = sorted({w for w in _WORD.findall(query.lower()) if len(w) > 1 and w not in STOPWORDS})
    return " | ".join(words) if words else None


def reciprocal_rank_fusion(rankings: Iterable[Sequence[str]], *, k: int = RRF_K) -> list[str]:
    """Merge rankings of ids: each id scores the sum of 1 / (k + rank) over the lists it is in."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, len(first_seen))
    return sorted(scores, key=lambda item: (-scores[item], first_seen[item]))


def candidate_depth(top_k: int) -> int:
    """How many results each ranking contributes before fusion."""
    return max(20, 4 * max(1, top_k))
