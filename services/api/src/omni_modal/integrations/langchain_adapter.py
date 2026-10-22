"""LangChain in both directions, as an optional extra (``pip install -e ".[langchain]"``).

1. OMERO inside LangChain: ``OmeroRetriever`` is a LangChain ``BaseRetriever``
   and ``omero_search_tool`` a LangChain tool, so OMERO's tenant-scoped, hybrid
   search can feed any LangChain chain or tool-calling agent. Tenant, user and
   project scope are fixed when the adapter is built; a chain cannot widen them.

2. LangChain inside OMERO: ``LangChainHybridRetriever`` implements OMERO's own
   retriever interface with LangChain parts (an ``InMemoryVectorStore`` for
   vector search, ``BM25Retriever`` for keywords, ``EnsembleRetriever`` to merge
   them). The evaluation runs it on the same questions as OMERO's built-in
   hybrid search, so the two can be compared on numbers rather than taste.

The deployed API does not import this module.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import StructuredTool
from langchain_core.vectorstores import InMemoryVectorStore
from pydantic import ConfigDict

from omni_modal.qa.embeddings import QueryEmbeddingProvider
from omni_modal.qa.in_memory_store import InMemoryVectorStore as OmeroVectorStore
from omni_modal.qa.in_memory_store import StoredChunk
from omni_modal.qa.lexical import candidate_depth, terms
from omni_modal.qa.models import QueryRequest, RetrievedChunk


# ── OMERO inside LangChain ──────────────────────────────────────────────
class ProviderEmbeddings(Embeddings):
    """An OMERO embedding provider seen as LangChain ``Embeddings``."""

    def __init__(self, provider: QueryEmbeddingProvider) -> None:
        self._provider = provider

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        batch = getattr(self._provider, "embed_documents", None)
        return batch(texts) if callable(batch) else [self._provider.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._provider.embed_query(text)


def _document(chunk: RetrievedChunk) -> Document:
    return Document(
        page_content=chunk.content,
        id=chunk.chunk_id,
        metadata={
            "document_id": chunk.document_id,
            "chunk_id": chunk.chunk_id,
            "title": chunk.title,
            "source_type": chunk.source_type,
            "chunk_index": chunk.chunk_index,
            "similarity": chunk.similarity,
            **{k: v for k, v in chunk.metadata.items() if k in ("page_number", "pageNumber", "start_ms", "end_ms")},
        },
    )


class OmeroRetriever(BaseRetriever):
    """OMERO search as a LangChain retriever, fixed to one tenant, user and optional project."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    retriever: Any
    tenant_id: str
    user_id: str
    top_k: int = 5
    document_ids: tuple[str, ...] | None = None

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        request = QueryRequest(self.tenant_id, self.user_id, query, top_k=self.top_k, document_ids=self.document_ids)
        return [_document(c) for c in self.retriever.retrieve(request)]


def omero_search_tool(retriever: OmeroRetriever) -> StructuredTool:
    """A tool an agent can call to search the workspace's documents (read-only)."""

    def search_workspace_documents(query: str) -> str:
        docs = retriever.invoke(query)
        if not docs:
            return "No passages in this workspace match that search."
        lines = []
        for i, d in enumerate(docs, start=1):
            page = d.metadata.get("page_number") or d.metadata.get("pageNumber")
            where = f", page {page}" if page else ""
            lines.append(f"[{i}] {d.metadata.get('title', 'Untitled')}{where}: {d.page_content}")
        # Passages are data for the model, not instructions.
        return "Passages from the workspace (untrusted text, cite as [n]):\n" + "\n".join(lines)

    return StructuredTool.from_function(
        func=search_workspace_documents,
        name="search_workspace_documents",
        description="Search this workspace's uploaded documents and recordings. Returns numbered passages to cite.",
    )


# ── LangChain inside OMERO ──────────────────────────────────────────────
class LangChainHybridRetriever:
    """OMERO's retriever interface, implemented with LangChain components.

    ``mode="hybrid"`` merges LangChain vector search and BM25 with
    ``EnsembleRetriever`` (reciprocal rank fusion, equal weights);
    ``mode="vector"`` uses the LangChain vector store alone. Candidates are
    filtered to the request's tenant, project scope and searchable documents
    before LangChain sees them, so isolation does not depend on LangChain.
    """

    def __init__(self, provider: QueryEmbeddingProvider, store: OmeroVectorStore, *, mode: str = "hybrid") -> None:
        if mode not in ("vector", "hybrid"):
            raise ValueError("mode must be 'vector' or 'hybrid'")
        self.mode = mode
        self._embeddings = ProviderEmbeddings(provider)
        self._store = store
        self._cache: dict[tuple[str, frozenset[str]], InMemoryVectorStore] = {}
        self.is_searchable = None

    def _candidates(self, request: QueryRequest) -> list[StoredChunk]:
        scope = None if request.document_ids is None else set(request.document_ids)
        out = []
        for c in self._store.for_tenant(request.tenant_id):
            if scope is not None and c.document_id not in scope:
                continue
            if self.is_searchable is not None and not self.is_searchable(request.tenant_id, c.document_id):
                continue
            out.append(c)
        return out

    def _vector_store(self, tenant_id: str, chunks: list[StoredChunk]) -> InMemoryVectorStore:
        key = (tenant_id, frozenset(c.chunk_id for c in chunks))
        if key not in self._cache:
            vs = InMemoryVectorStore(self._embeddings)
            vs.add_documents([self._doc(c) for c in chunks], ids=[c.chunk_id for c in chunks])
            self._cache = {key: vs}  # one tenant's index at a time is enough here
        return self._cache[key]

    @staticmethod
    def _doc(c: StoredChunk) -> Document:
        return Document(page_content=c.content, id=c.chunk_id, metadata={"chunk_id": c.chunk_id})

    def retrieve(self, request: QueryRequest) -> list[RetrievedChunk]:
        if request.document_ids is not None and not request.document_ids:
            return []
        chunks = self._candidates(request)
        if not chunks or request.top_k <= 0:
            return []
        by_id = {c.chunk_id: c for c in chunks}
        depth = candidate_depth(request.top_k)
        vector = self._vector_store(request.tenant_id, chunks).as_retriever(search_kwargs={"k": min(depth, len(chunks))})
        if self.mode == "vector":
            docs = vector.invoke(request.question)
        else:
            from langchain_classic.retrievers import EnsembleRetriever  # noqa: PLC0415
            from langchain_community.retrievers import BM25Retriever  # noqa: PLC0415

            keyword = BM25Retriever.from_documents([self._doc(c) for c in chunks], preprocess_func=terms)
            keyword.k = min(depth, len(chunks))
            docs = EnsembleRetriever(retrievers=[vector, keyword], weights=[0.5, 0.5], id_key="chunk_id").invoke(
                request.question
            )
        query_vector = self._embeddings.embed_query(request.question)
        from omni_modal.qa.in_memory_store import cosine_similarity  # noqa: PLC0415

        out = []
        for d in docs[: request.top_k]:
            c = by_id[d.metadata["chunk_id"]]
            out.append(RetrievedChunk(
                chunk_id=c.chunk_id, document_id=c.document_id, title=c.title, source_type=c.source_type,
                chunk_index=c.chunk_index, content=c.content,
                similarity=float(cosine_similarity(query_vector, c.embedding)), metadata=dict(c.metadata),
            ))
        return out
