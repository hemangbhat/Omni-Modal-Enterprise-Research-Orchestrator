"""Re-embed every stored chunk with the configured embedding model.

Use after changing EMBEDDING_BACKEND (for example from hashing to fastembed).
Chunks are not re-extracted: their stored text is embedded again and the
vector and model name are updated in place, in small batches. It is safe to
stop and run again; only chunks made by another model are touched. Until a
chunk is re-embedded, search still finds it by keywords.

    DATABASE_URL=... EMBEDDING_BACKEND=fastembed python scripts/reembed.py --dry-run
    DATABASE_URL=... EMBEDDING_BACKEND=fastembed python scripts/reembed.py
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "api" / "src"))

BATCH = 32


def pending(cur, model_id: str) -> int:
    cur.execute("SELECT count(*) FROM embeddings WHERE embedding_model <> %s", (model_id,))
    return int(cur.fetchone()[0])


def reembed(conn, provider, model_id: str, dimensions: int, *, batch: int = BATCH, log=print) -> int:
    done = 0
    while True:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT e.id, c.content FROM embeddings e
                   JOIN document_chunks c ON c.id = e.chunk_id
                   WHERE e.embedding_model <> %s ORDER BY e.id LIMIT %s""",
                (model_id, batch),
            )
            rows = cur.fetchall()
            if not rows:
                return done
            vectors = provider.embed_documents([r[1] for r in rows])
            for (row_id, _text), vector in zip(rows, vectors):
                literal = "[" + ",".join(str(v) for v in vector) + "]"
                cur.execute(
                    "UPDATE embeddings SET embedding = %s::vector, embedding_model = %s, dimensions = %s WHERE id = %s",
                    (literal, model_id, dimensions, row_id),
                )
        conn.commit()
        done += len(rows)
        log(f"re-embedded {done} chunks")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="only count the chunks that would change")
    args = parser.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("DATABASE_URL is required.")
        return 2
    from omni_modal.qa.embedding_factory import select_embedding_provider  # noqa: PLC0415

    selection = select_embedding_provider()
    if selection.fell_back:
        print(f"Refusing to re-embed: {selection.reason}")
        return 2
    dims = int(getattr(selection.provider, "dimensions", 0) or 384)
    if dims != 384:
        print(f"The embeddings column is vector(384); {selection.model_id} makes {dims}-dim vectors.")
        return 2

    import psycopg  # noqa: PLC0415

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            count = pending(cur, selection.model_id)
        print(f"{count} chunks were made by another model; target model: {selection.model_id}")
        if args.dry_run or count == 0:
            return 0
        start = time.time()
        total = reembed(conn, selection.provider, selection.model_id, dims)
        print(f"Done: {total} chunks in {time.time() - start:.0f}s. Restart the API to clear its query cache.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
