"""Compile Markdown reports from saved material only.

A report is assembled, not written by a model: every sentence comes from a
saved finding, note, research answer or extracted entity. Each finding keeps
its own ``[n]`` citation numbers; ``renumber_citations`` maps them into one
numbered source list for the whole report, so ``[3]`` means the same source
everywhere in it.
"""

from __future__ import annotations

import re
import time

from omni_modal.qa.models import format_timestamp

_MARKER = re.compile(r"[ \t]*(?<![\w\]])\[(\d{1,3}(?:[ \t]*,[ \t]*\d{1,3})*)\]")

INSIGHT_SECTIONS = (
    ("decision", "Decisions"),
    ("risk", "Risks"),
    ("action_item", "Action items"),
    ("owner", "Owners"),
    ("deadline", "Deadlines"),
)


def answer_text(markdown: str) -> str:
    """The answer body without its "## Answer" heading and "## Sources" list."""
    body = markdown.split("\n## Sources", 1)[0]
    body = re.sub(r"^\s*## Answer\s*", "", body)
    return body.strip()


def _key(citation: dict) -> tuple[str, str]:
    return (str(citation.get("document_id", "")), str(citation.get("chunk_id", "")))


def renumber_citations(body: str, citations: list[dict], registry: list[dict]) -> str:
    """Rewrite ``[n]`` (local to ``citations``) into positions in the shared ``registry``.

    Markers with no matching citation are removed.
    """
    def _replace(match: re.Match[str]) -> str:
        numbers = []
        for raw in re.findall(r"\d+", match.group(1)):
            n = int(raw)
            if not 1 <= n <= len(citations):
                continue
            citation = citations[n - 1]
            key = _key(citation)
            index = next((i for i, c in enumerate(registry) if _key(c) == key), None)
            if index is None:
                registry.append(citation)
                index = len(registry) - 1
            if index + 1 not in numbers:
                numbers.append(index + 1)
        if not numbers:
            return ""
        leading = match.group(0)[: len(match.group(0)) - len(match.group(0).lstrip())]
        return f"{leading}[{', '.join(str(n) for n in numbers)}]"

    return _MARKER.sub(_replace, body)


def location(citation: dict) -> str | None:
    if citation.get("page_number") is not None:
        return f"page {citation['page_number']}"
    if citation.get("start_ms") is not None:
        start = format_timestamp(int(citation["start_ms"]))
        end = citation.get("end_ms")
        return f"{start}–{format_timestamp(int(end))}" if end is not None else start
    if citation.get("segment_index") is not None:
        return f"segment {citation['segment_index']}"
    return None


def sources_section(registry: list[dict]) -> str:
    if not registry:
        return ""
    lines = ["## Sources", ""]
    for i, c in enumerate(registry, start=1):
        where = location(c)
        label = f"{c.get('title') or 'Untitled document'}" + (f", {where}" if where else "")
        snippet = str(c.get("snippet") or "").strip()
        lines.append(f'{i}. {label}: "{snippet}"' if snippet else f"{i}. {label}")
    return "\n".join(lines)


def _date(epoch: float) -> str:
    return time.strftime("%d %B %Y", time.gmtime(epoch)).lstrip("0")


def project_report(project, findings, notes, insights: dict, document_titles: list[str], *, now: float) -> str:
    registry: list[dict] = []
    parts = [f"# {project.name}", ""]
    summary = (
        f"Report generated on {_date(now)} from {len(findings)} saved finding{'s' if len(findings) != 1 else ''}"
        f" and {len(document_titles)} document{'s' if len(document_titles) != 1 else ''}."
    )
    parts += [summary, ""]
    if project.description.strip():
        parts += [project.description.strip(), ""]

    parts += ["## Findings", ""]
    if findings:
        for i, item in enumerate(findings, start=1):
            parts += [f"### {i}. {item.title}", "", renumber_citations(item.body, item.citations, registry), ""]
    else:
        parts += ["No findings have been saved to this project yet.", ""]

    for label, heading in INSIGHT_SECTIONS:
        entries = insights.get(label) or []
        if entries:
            parts += [f"## {heading}", ""]
            parts += [f"- {e['value']} (from *{e['document_title']}*)" for e in entries]
            parts.append("")

    if notes:
        parts += ["## Notes", ""]
        for note in notes:
            text = f"**{note.title}**" + (f": {note.body.strip()}" if note.body.strip() else "")
            parts.append(f"- {text}")
        parts.append("")

    if document_titles:
        parts += ["## Documents in this project", ""]
        parts += [f"- {title}" for title in document_titles]
        parts.append("")

    sources = sources_section(registry)
    if sources:
        parts.append(sources)
    return "\n".join(parts).rstrip() + "\n"


def session_report(title: str, messages, *, now: float) -> str:
    registry: list[dict] = []
    parts = [f"# {title}", "", f"Research session report generated on {_date(now)}.", ""]
    number = 0
    question: str | None = None
    for message in messages:
        if message.role == "user":
            question = message.content
            continue
        if question is None:
            continue
        number += 1
        parts += [f"## {number}. {question}", ""]
        body = answer_text(message.content)
        if message.status == "no_data":
            parts += [body or "The documents did not contain enough information to answer this.", ""]
        else:
            parts += [renumber_citations(body, list(message.citations or []), registry), ""]
        question = None
    if number == 0:
        parts += ["This session has no answered questions yet.", ""]
    sources = sources_section(registry)
    if sources:
        parts.append(sources)
    return "\n".join(parts).rstrip() + "\n"
