from __future__ import annotations

from uuid import uuid5, NAMESPACE_URL

from omni_modal.data_access import EntityRecord
from omni_modal.entity_extraction.schema import ExtractionOutput


def output_to_entity_records(output: ExtractionOutput) -> list[EntityRecord]:
    records: list[EntityRecord] = []
    for index, entity in enumerate(output.entities):
        stable_id = uuid5(
            NAMESPACE_URL,
            ":".join(
                [
                    output.tenant_id,
                    output.document_id,
                    output.chunk_id or "",
                    str(index),
                    entity.label.value,
                    entity.text,
                ]
            ),
        )
        records.append(
            EntityRecord(
                id=str(stable_id),
                document_id=output.document_id,
                tenant_id=output.tenant_id,
                label=entity.label.value,
                value=entity.text,
                confidence=entity.confidence,
            )
        )
    return records
