"""Retrieval and citation evaluation on the labelled corpus in ``eval/corpus.json``.

Runs with no database, network or API key: documents go through the real
chunker into the in-memory store, and questions through the real retriever
and the extractive answer path.

For each embedding backend and retrieval mode it reports:

- recall@1 and recall@5: a relevant document is the first result / in the first five;
- MRR: mean of 1 / rank of the first relevant result;
- supported: the answer cites a relevant document whose quoted snippet
  contains the labelled fact (what a reader checking the citation would see).

Usage (from services/api):

    python -m omni_modal.benchmark.eval                      # hashing backend
    python -m omni_modal.benchmark.eval --backends hashing,sentence-transformers
    python -m omni_modal.benchmark.eval --output eval/results.json
    python -m omni_modal.benchmark.eval --check eval/results.json   # CI regression gate
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from omni_modal.ingestion.chunking import DeterministicChunker
from omni_modal.ingestion.models import ExtractedTextSegment, IngestionResult, SourceReference
from omni_modal.qa.in_memory_store import InMemoryChunkPersistence, InMemoryChunkRetriever, InMemoryVectorStore
from omni_modal.qa.models import QueryRequest
from omni_modal.qa.synthesis import ExtractiveAnswerSynthesizer

CORPUS = Path(__file__).resolve().parents[3] / "eval" / "corpus.json"
TENANT = "eval-tenant"
MODES = ("vector", "hybrid")


@dataclass
class Scores:
    backend: str
    mode: str
    questions: int
    recall_at_1: float
    recall_at_5: float
    mrr: float
    supported: float
    by_type: dict


def load_corpus(path: Path = CORPUS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _provider(backend: str):
    if backend == "hashing":
        from omni_modal.qa.embeddings import HashingQueryEmbeddingProvider  # noqa: PLC0415

        return HashingQueryEmbeddingProvider()
    if backend == "sentence-transformers":
        from omni_modal.qa.embedding_factory import select_embedding_provider  # noqa: PLC0415

        selection = select_embedding_provider(env={"EMBEDDING_BACKEND": "sentence-transformers"})
        if selection.backend != "sentence-transformers":
            raise RuntimeError(selection.reason or "sentence-transformers is not available")
        return selection.provider
    raise ValueError(f"Unknown backend {backend!r}")


def build_store(corpus: dict, provider) -> InMemoryVectorStore:
    store = InMemoryVectorStore()
    persistence = InMemoryChunkPersistence(store, provider)
    chunker = DeterministicChunker()
    for doc in corpus["documents"]:
        segment = ExtractedTextSegment(doc["text"], SourceReference(f"eval://{doc['id']}", "note"))
        chunks = chunker.chunk(TENANT, doc["id"], [segment])
        persistence.persist(IngestionResult(
            tenant_id=TENANT, document_id=doc["id"], owner_id="eval", title=doc["title"],
            source_kind="note", status="ready", chunks=chunks, metadata={},
        ))
    return store


def _summary(rows: list[dict]) -> dict:
    n = len(rows) or 1
    return {
        "questions": len(rows),
        "recall_at_1": round(sum(r["hit1"] for r in rows) / n, 3),
        "recall_at_5": round(sum(r["hit5"] for r in rows) / n, 3),
        "mrr": round(sum(r["rr"] for r in rows) / n, 3),
        "supported": round(sum(r["supported"] for r in rows) / n, 3),
    }


def evaluate(corpus: dict, backend: str, mode: str, provider=None) -> tuple[Scores, list[dict]]:
    provider = provider or _provider(backend)
    store = build_store(corpus, provider)
    retriever = InMemoryChunkRetriever(provider, store, mode=mode)
    synthesizer = ExtractiveAnswerSynthesizer()
    rows = []
    for q in corpus["questions"]:
        relevant = set(q["relevant"])
        request = QueryRequest(TENANT, "eval", q["question"], top_k=5)
        chunks = retriever.retrieve(request)
        ranked = [c.document_id for c in chunks]
        first = next((i for i, d in enumerate(ranked, start=1) if d in relevant), None)
        answer = synthesizer.synthesize(request, chunks)
        fact = q["fact"].lower()
        supported = any(c.document_id in relevant and c.cited and fact in (c.snippet or "").lower()
                        for c in answer.citations)
        rows.append({
            "id": q["id"], "type": q["type"], "ranked": ranked,
            "hit1": first == 1, "hit5": first is not None and first <= 5,
            "rr": 1.0 / first if first else 0.0, "supported": supported,
        })
    overall = _summary(rows)
    types = sorted({r["type"] for r in rows})
    scores = Scores(
        backend=backend, mode=mode, questions=overall["questions"],
        recall_at_1=overall["recall_at_1"], recall_at_5=overall["recall_at_5"],
        mrr=overall["mrr"], supported=overall["supported"],
        by_type={t: _summary([r for r in rows if r["type"] == t]) for t in types},
    )
    return scores, rows


def run(backends: list[str], corpus: dict | None = None) -> dict:
    corpus = corpus or load_corpus()
    results, skipped = [], {}
    for backend in backends:
        try:
            provider = _provider(backend)
        except Exception as exc:  # noqa: BLE001 - an optional backend may be missing
            skipped[backend] = str(exc)
            continue
        for mode in MODES:
            scores, _rows = evaluate(corpus, backend, mode, provider)
            results.append(asdict(scores))
    return {
        "corpus": {"documents": len(corpus["documents"]), "questions": len(corpus["questions"])},
        "measured_at": time.strftime("%Y-%m-%d", time.gmtime()),
        "results": results,
        "skipped": skipped,
    }


def check(current: dict, baseline: dict, margin: float) -> list[str]:
    """Regressions of more than ``margin`` against a committed results file."""
    problems = []
    base = {(r["backend"], r["mode"]): r for r in baseline["results"]}
    for r in current["results"]:
        old = base.get((r["backend"], r["mode"]))
        if old is None:
            continue
        for metric in ("recall_at_1", "recall_at_5", "mrr", "supported"):
            if r[metric] < old[metric] - margin:
                problems.append(f"{r['backend']}/{r['mode']} {metric}: {r[metric]} < {old[metric]} - {margin}")
    return problems


def _table(report: dict) -> str:
    lines = ["| Embeddings | Mode | Recall@1 | Recall@5 | MRR | Supported citation |", "|---|---|---|---|---|---|"]
    for r in report["results"]:
        lines.append(f"| {r['backend']} | {r['mode']} | {r['recall_at_1']:.3f} | {r['recall_at_5']:.3f} "
                     f"| {r['mrr']:.3f} | {r['supported']:.3f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--backends", default="hashing", help="comma-separated: hashing,sentence-transformers")
    parser.add_argument("--output", help="write the JSON report here")
    parser.add_argument("--check", help="fail on a regression against this committed report")
    parser.add_argument("--margin", type=float, default=0.02)
    args = parser.parse_args(argv)

    report = run([b.strip() for b in args.backends.split(",") if b.strip()])
    print(_table(report))
    for backend, reason in report["skipped"].items():
        print(f"skipped {backend}: {reason}")
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.check:
        problems = check(report, json.loads(Path(args.check).read_text(encoding="utf-8")), args.margin)
        for p in problems:
            print("REGRESSION", p)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
