from __future__ import annotations

from typing import Any

from omni_modal.qa.models import QueryRequest


class QueryContractError(ValueError):
    pass


def query_request_from_payload(payload: dict[str, Any]) -> QueryRequest:
    tenant_id = _required_string(payload, "tenant_id")
    user_id = _required_string(payload, "user_id")
    question = _required_string(payload, "question")
    top_k = _bounded_int(payload.get("top_k", 5), "top_k", minimum=1, maximum=20)
    min_similarity = _bounded_float(
        payload.get("min_similarity", 0.0),
        "min_similarity",
        minimum=0.0,
        maximum=1.0,
    )
    stream = bool(payload.get("stream", False))

    return QueryRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        question=question,
        top_k=top_k,
        min_similarity=min_similarity,
        stream=stream,
    )


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise QueryContractError(f"{key} is required.")
    return value.strip()


def _bounded_int(value: object, key: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise QueryContractError(f"{key} must be an integer.")
    if value < minimum or value > maximum:
        raise QueryContractError(f"{key} must be between {minimum} and {maximum}.")
    return value


def _bounded_float(value: object, key: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QueryContractError(f"{key} must be a number.")
    numeric = float(value)
    if numeric < minimum or numeric > maximum:
        raise QueryContractError(f"{key} must be between {minimum} and {maximum}.")
    return numeric
