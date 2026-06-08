from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from omni_modal.orchestration.a2a import A2AResearchResponse
from omni_modal.qa.models import QueryRequest, QueryResponse, RetrievedChunk


NodeName = Literal[
    "validate_request",
    "retrieve_internal_evidence",
    "detect_missing_data",
    "delegate_external_research",
    "merge_external_evidence",
    "synthesize_answer",
    "controlled_fallback",
]


@dataclass(frozen=True)
class GraphNodeTrace:
    node: NodeName
    status: Literal["ok", "skipped", "failed"]
    detail: str


@dataclass
class AgentGraphState:
    request: QueryRequest
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    missing_data: bool = False
    external_response: A2AResearchResponse | None = None
    external_delegation_attempted: bool = False
    response: QueryResponse | None = None
    error_message: str | None = None
    trace: list[GraphNodeTrace] = field(default_factory=list)

    def add_trace(
        self,
        node: NodeName,
        status: Literal["ok", "skipped", "failed"],
        detail: str,
    ) -> None:
        self.trace.append(GraphNodeTrace(node=node, status=status, detail=detail))

    def to_trace_json(self) -> list[dict[str, str]]:
        return [asdict(item) for item in self.trace]
