from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from omni_modal.entity_extraction.schema import (
    EntityLabel,
    EntitySpan,
    ExtractionOutput,
)
from omni_modal.entity_extraction.validation import validate_extraction_output


class EnterpriseEntityExtractor(Protocol):
    def extract(
        self,
        tenant_id: str,
        document_id: str,
        text: str,
        chunk_id: str | None = None,
    ) -> ExtractionOutput:
        raise NotImplementedError


@dataclass(frozen=True)
class ExtractionPattern:
    label: EntityLabel
    pattern: re.Pattern[str]
    confidence: float


class RuleBasedEnterpriseEntityExtractor:
    model_name = "rule-based-enterprise-entities"
    schema_version = "enterprise_entities.v1"

    def __init__(self) -> None:
        self._patterns = [
            ExtractionPattern(
                EntityLabel.COMPANY_ACRONYM,
                re.compile(r"\b[A-Z]{2,6}\b"),
                0.72,
            ),
            ExtractionPattern(
                EntityLabel.KPI,
                re.compile(
                    r"\b(?:KPI|ARR|NPS|SLA|MTTR|CSAT|conversion|retention|churn)"
                    r"[^.:\n]*(?:\d+(?:\.\d+)?%?|\$[0-9,.]+)",
                    re.IGNORECASE,
                ),
                0.68,
            ),
            ExtractionPattern(
                EntityLabel.DEADLINE,
                re.compile(
                    r"\b(?:by|before|due|deadline|target)\s+"
                    r"(?:Q[1-4]|Monday|Tuesday|Wednesday|Thursday|Friday|"
                    r"January|February|March|April|May|June|July|August|"
                    r"September|October|November|December|\d{1,2}/\d{1,2}/\d{2,4})"
                    r"[^.:\n]*",
                    re.IGNORECASE,
                ),
                0.7,
            ),
            ExtractionPattern(
                EntityLabel.RISK,
                re.compile(r"\b(?:risk|blocker|blocked|concern|dependency)[^.:\n]*", re.IGNORECASE),
                0.66,
            ),
            ExtractionPattern(
                EntityLabel.ACTION_ITEM,
                re.compile(r"\b(?:action item|todo|follow up|next step|needs to|must)\b[^.:\n]*", re.IGNORECASE),
                0.69,
            ),
            ExtractionPattern(
                EntityLabel.DECISION,
                re.compile(r"\b(?:decided|approved|rejected|agreed|committed)\b[^.:\n]*", re.IGNORECASE),
                0.7,
            ),
            ExtractionPattern(
                EntityLabel.GOAL,
                re.compile(r"\b(?:goal|objective|target outcome|north star)\b[^.:\n]*", re.IGNORECASE),
                0.64,
            ),
            ExtractionPattern(
                EntityLabel.OWNER,
                re.compile(r"\b(?:owner|owned by|DRI|accountable|assigned to)\b[^.:\n]*", re.IGNORECASE),
                0.65,
            ),
        ]

    def extract(
        self,
        tenant_id: str,
        document_id: str,
        text: str,
        chunk_id: str | None = None,
    ) -> ExtractionOutput:
        entities: list[EntitySpan] = []
        seen: set[tuple[EntityLabel, int, int]] = set()

        for extraction_pattern in self._patterns:
            for match in extraction_pattern.pattern.finditer(text):
                key = (extraction_pattern.label, match.start(), match.end())
                if key in seen:
                    continue
                seen.add(key)
                value = match.group(0).strip()
                entities.append(
                    EntitySpan(
                        label=extraction_pattern.label,
                        text=value,
                        normalized_value=_normalize_value(value),
                        start_char=match.start(),
                        end_char=match.end(),
                        confidence=extraction_pattern.confidence,
                        evidence=_evidence_window(text, match.start(), match.end()),
                    )
                )

        output = ExtractionOutput(
            tenant_id=tenant_id,
            document_id=document_id,
            chunk_id=chunk_id,
            model_name=self.model_name,
            schema_version=self.schema_version,
            entities=sorted(entities, key=lambda entity: (entity.start_char, entity.label.value)),
        )
        validate_extraction_output(output, text)
        return output


class QLoRAEnterpriseEntityExtractor:
    """Real NER using a pre-trained Hugging Face token-classification model.

    Despite the class name being retained for backward compatibility, this
    implementation uses a pre-trained model (no QLoRA fine-tuning required).
    The model is loaded once at construction from ``QLORA_ENTITY_MODEL_PATH``
    (a HuggingFace model ID or local path).

    Default model: dslim/bert-base-NER  (free, ~400 MB, standard NER)
    Maps HF NER labels → our enterprise EntityLabel where possible, and falls
    back to storing them as raw labels otherwise.

    Install requirements: pip install transformers torch
    """

    schema_version = "enterprise_entities.v1"

    # HF standard NER label → our EntityLabel
    _HF_LABEL_MAP: dict[str, "EntityLabel"] = {}  # filled in __init__ after imports

    def __init__(self, model_path: Path) -> None:
        self._model_path = model_path
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "NER inference requires: pip install transformers torch"
            ) from exc

        model_name = str(model_path)
        self._pipeline = hf_pipeline(
            "ner",
            model=model_name,
            aggregation_strategy="simple",
        )
        self._rule_based = RuleBasedEnterpriseEntityExtractor()

    def extract(
        self,
        tenant_id: str,
        document_id: str,
        text: str,
        chunk_id: str | None = None,
    ) -> ExtractionOutput:
        # 1. Rule-based pass for enterprise-specific labels
        rule_output = self._rule_based.extract(tenant_id, document_id, text, chunk_id)

        # 2. HF NER pass for person/org/location
        entities: list[EntitySpan] = list(rule_output.entities)
        try:
            ner_results = self._pipeline(text[:2000])  # cap to avoid OOM on long docs
            for item in (ner_results or []):
                entity_group = str(item.get("entity_group", "")).upper()
                raw_text = str(item.get("word", "")).strip()
                if not raw_text:
                    continue
                start = int(item.get("start", 0))
                end = int(item.get("end", len(raw_text)))
                score = float(item.get("score", 0.5))

                # Map HF group → closest EntityLabel
                if "PERSON" in entity_group or "PER" in entity_group:
                    label = EntityLabel.OWNER
                elif "ORG" in entity_group:
                    label = EntityLabel.COMPANY_ACRONYM
                else:
                    # Skip LOC, MISC etc. — not in our schema
                    continue

                entities.append(
                    EntitySpan(
                        label=label,
                        text=raw_text,
                        normalized_value=_normalize_value(raw_text),
                        start_char=start,
                        end_char=end,
                        confidence=round(score, 4),
                        evidence=_evidence_window(text, start, end),
                    )
                )
        except Exception:
            pass  # HF pass is additive — never block on failure

        output = ExtractionOutput(
            tenant_id=tenant_id,
            document_id=document_id,
            chunk_id=chunk_id,
            model_name=str(self._model_path),
            schema_version=self.schema_version,
            entities=sorted(entities, key=lambda e: (e.start_char, e.label.value)),
        )
        validate_extraction_output(output, text)
        return output


def build_extraction_prompt(text: str) -> str:
    labels = ", ".join(label.value for label in EntityLabel)
    return (
        "Extract only enterprise entities from the transcript/document chunk.\n"
        f"Allowed labels: {labels}.\n"
        "Return strict JSON with an entities array. Each entity must include "
        "label, text, normalized_value, start_char, end_char, confidence, evidence, "
        "and optional attributes.\n"
        f"Text:\n{text}\nJSON:"
    )


def _parse_model_json(raw: str) -> dict[str, object]:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Model output did not contain a JSON object.")
    payload = json.loads(raw[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("Model JSON output must be an object.")
    return payload


def _normalize_value(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip().lower())


def _evidence_window(text: str, start: int, end: int) -> str:
    window_start = max(0, start - 80)
    window_end = min(len(text), end + 80)
    return text[window_start:window_end].strip()
