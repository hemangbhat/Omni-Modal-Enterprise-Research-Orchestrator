"""Turn a follow-up question into a standalone search query.

"And headcount?" retrieves nothing useful on its own. Before retrieval, the
earlier turns are used to rewrite it ("Q3 headcount growth"). The model does
the rewrite when one is configured; otherwise, or if the model fails or
returns something unusable, the previous question is prepended, which keeps
the earlier topic's words in the search.

Only retrieval uses the rewrite. The answer step still sees the user's own
words plus the conversation history.
"""

from __future__ import annotations

from omni_modal.observability import observability
from omni_modal.qa.llm import ChatClient

REWRITE_PROMPT = (
    "Rewrite the user's latest question as one standalone search query that "
    "can be understood without the conversation. Keep names, dates and numbers. "
    "Reply with the query only: no quotes, no explanation."
)
MAX_QUERY_CHARS = 300


class QueryRewriter:
    def __init__(self, client: ChatClient | None = None) -> None:
        self._client = client

    def rewrite(self, question: str, history: list[tuple[str, str]]) -> str:
        previous = [content for role, content in history if role == "user"]
        if not previous:
            return question
        heuristic = f"{previous[-1].strip()} {question.strip()}"
        if self._client is None:
            return heuristic
        try:
            reply = self._client.complete(
                REWRITE_PROMPT,
                [{"role": "user", "content": _transcript(history, question)}],
                max_tokens=80,
            )
        except Exception as exc:  # noqa: BLE001 - a failed rewrite must not fail the query
            observability.capture_message(
                f"Follow-up rewrite failed ({exc}); using the previous question as context.",
                operation="research.followup.rewrite", level="warning",
            )
            return heuristic
        query = reply.strip().splitlines()[0].strip().strip('"\'') if reply.strip() else ""
        if not query or len(query) > MAX_QUERY_CHARS:
            return heuristic
        return query


def _transcript(history: list[tuple[str, str]], question: str) -> str:
    lines = []
    for role, content in history[-6:]:
        text = content.split("## Sources", 1)[0].replace("## Answer", "", 1).strip()
        lines.append(f"{role.capitalize()}: {text[:600]}")
    lines.append(f"Latest question: {question}")
    return "\n".join(lines)
