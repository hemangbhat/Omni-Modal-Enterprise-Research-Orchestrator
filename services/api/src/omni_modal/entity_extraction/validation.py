from __future__ import annotations

from typing import Any

from omni_modal.entity_extraction.schema import EntityLabel, EntitySpan, ExtractionOutput


class ExtractionValidationError(ValueError):
    pass


def validate_entity_span(entity: EntitySpan, source_text: str) -> None:
    if not entity.text.strip():
        raise ExtractionValidationError("Entity text cannot be empty.")
    if entity.start_char < 0 or entity.end_char <= entity.start_char:
        raise ExtractionValidationError("Entity character offsets are invalid.")
    if entity.end_char > len(source_text):
        raise ExtractionValidationError("Entity end offset exceeds source text length.")
    if source_text[entity.start_char : entity.end_char] != entity.text:
        raise ExtractionValidationError("Entity text does not match source offsets.")
    if not 0 <= entity.confidence <= 1:
        raise ExtractionValidationError("Entity confidence must be between 0 and 1.")


def validate_extraction_output(output: ExtractionOutput, source_text: str) -> None:
    if not output.tenant_id:
        raise ExtractionValidationError("tenant_id is required.")
    if not output.document_id:
        raise ExtractionValidationError("document_id is required.")
    if output.schema_version != "enterprise_entities.v1":
        raise ExtractionValidationError("Unsupported extraction schema version.")

    for entity in output.entities:
        validate_entity_span(entity, source_text)


def parse_entity_label(value: Any) -> EntityLabel:
    try:
        return EntityLabel(str(value))
    except ValueError as exc:
        raise ExtractionValidationError(f"Unsupported entity label: {value}") from exc
