from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EntityLabel(str, Enum):
    COMPANY_ACRONYM = "company_acronym"
    GOAL = "goal"
    KPI = "kpi"
    RISK = "risk"
    DEADLINE = "deadline"
    OWNER = "owner"
    ACTION_ITEM = "action_item"
    DECISION = "decision"


LABEL_DESCRIPTIONS: dict[EntityLabel, str] = {
    EntityLabel.COMPANY_ACRONYM: "Short enterprise or business-unit acronyms.",
    EntityLabel.GOAL: "Stated strategic or operational outcomes.",
    EntityLabel.KPI: "Quantified metrics or tracked performance indicators.",
    EntityLabel.RISK: "Business, execution, security, compliance, or delivery risks.",
    EntityLabel.DEADLINE: "Dates, quarters, or explicit due dates.",
    EntityLabel.OWNER: "Named accountable people, teams, roles, or functions.",
    EntityLabel.ACTION_ITEM: "Concrete follow-up tasks or assigned next steps.",
    EntityLabel.DECISION: "Explicit decisions, approvals, rejections, or commitments.",
}


@dataclass(frozen=True)
class EntitySpan:
    label: EntityLabel
    text: str
    normalized_value: str
    start_char: int
    end_char: int
    confidence: float
    evidence: str
    attributes: dict[str, str | int | float | bool] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionOutput:
    tenant_id: str
    document_id: str
    chunk_id: str | None
    model_name: str
    schema_version: str
    entities: list[EntitySpan]

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entities"] = [
            {**asdict(entity), "label": entity.label.value} for entity in self.entities
        ]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_json_dict(), sort_keys=True)


@dataclass(frozen=True)
class TrainingExample:
    id: str
    text: str
    entities: list[EntitySpan]

    def to_jsonl_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "entities": [
                {**asdict(entity), "label": entity.label.value}
                for entity in self.entities
            ],
        }
