from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omni_modal.entity_extraction.schema import EntitySpan, TrainingExample
from omni_modal.entity_extraction.validation import (
    ExtractionValidationError,
    parse_entity_label,
    validate_entity_span,
)


def load_training_jsonl(path: Path) -> list[TrainingExample]:
    examples: list[TrainingExample] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            examples.append(training_example_from_record(payload))
        except (json.JSONDecodeError, ExtractionValidationError, TypeError) as exc:
            raise ExtractionValidationError(
                f"Invalid training example at line {line_number}: {exc}"
            ) from exc
    return examples


def training_example_from_record(record: dict[str, Any]) -> TrainingExample:
    example_id = _required_string(record, "id")
    text = _required_string(record, "text")
    raw_entities = record.get("entities")
    if not isinstance(raw_entities, list):
        raise ExtractionValidationError("entities must be a list.")

    entities = [_entity_from_record(entity) for entity in raw_entities]
    for entity in entities:
        validate_entity_span(entity, text)

    return TrainingExample(id=example_id, text=text, entities=entities)


def _entity_from_record(record: object) -> EntitySpan:
    if not isinstance(record, dict):
        raise ExtractionValidationError("entity must be an object.")

    return EntitySpan(
        label=parse_entity_label(record.get("label")),
        text=_required_string(record, "text"),
        normalized_value=str(record.get("normalized_value") or record.get("text")),
        start_char=_required_int(record, "start_char"),
        end_char=_required_int(record, "end_char"),
        confidence=float(record.get("confidence", 1.0)),
        evidence=_required_string(record, "evidence"),
        attributes=_attributes(record.get("attributes")),
    )


def _required_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExtractionValidationError(f"{key} is required.")
    return value


def _required_int(record: dict[str, Any], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int):
        raise ExtractionValidationError(f"{key} must be an integer.")
    return value


def _attributes(value: object) -> dict[str, str | int | float | bool]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ExtractionValidationError("attributes must be an object.")

    clean: dict[str, str | int | float | bool] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ExtractionValidationError("attribute keys must be strings.")
        if not isinstance(item, (str, int, float, bool)):
            raise ExtractionValidationError("attribute values must be scalar.")
        clean[key] = item
    return clean
