import json
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401
from omni_modal.entity_extraction import (
    EntityLabel,
    ExtractionValidationError,
    RuleBasedEnterpriseEntityExtractor,
    TrainingNotReadyError,
    load_training_jsonl,
    run_qlora_training,
    QLoRATrainingConfig,
)
from omni_modal.entity_extraction.storage import output_to_entity_records


class EntityExtractionTest(unittest.TestCase):
    def test_rule_based_extractor_returns_machine_readable_entities(self) -> None:
        text = (
            "ACME approved the renewal plan. "
            "Owner: Revenue Ops. "
            "Action item: follow up with security by Q3. "
            "Risk: SLA misses could increase churn by 12%."
        )

        output = RuleBasedEnterpriseEntityExtractor().extract(
            tenant_id="tenant",
            document_id="doc",
            chunk_id="chunk",
            text=text,
        )
        labels = {entity.label for entity in output.entities}
        payload = output.to_json_dict()

        self.assertIn(EntityLabel.COMPANY_ACRONYM, labels)
        self.assertIn(EntityLabel.ACTION_ITEM, labels)
        self.assertIn(EntityLabel.RISK, labels)
        self.assertEqual(payload["schema_version"], "enterprise_entities.v1")
        self.assertIsInstance(payload["entities"], list)

    def test_output_maps_to_database_entity_records(self) -> None:
        text = "Decision: approved FY27 ARR goal of 20%."
        output = RuleBasedEnterpriseEntityExtractor().extract(
            tenant_id="tenant",
            document_id="doc",
            text=text,
        )

        records = output_to_entity_records(output)

        self.assertGreaterEqual(len(records), 1)
        self.assertEqual(records[0].tenant_id, "tenant")
        self.assertEqual(records[0].document_id, "doc")

    def test_training_jsonl_validates_offsets_and_labels(self) -> None:
        text = "Owner: Platform team"
        record = {
            "id": "example-1",
            "text": text,
            "entities": [
                {
                    "label": "owner",
                    "text": "Owner: Platform team",
                    "normalized_value": "platform_team",
                    "start_char": 0,
                    "end_char": len(text),
                    "confidence": 1.0,
                    "evidence": text,
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            examples = load_training_jsonl(path)

        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0].entities[0].label, EntityLabel.OWNER)

    def test_training_loop_is_stubbed_when_data_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = QLoRATrainingConfig(
                base_model="local/model",
                train_jsonl=Path(directory) / "missing.jsonl",
                output_dir=Path(directory) / "out",
            )

            with self.assertRaises(TrainingNotReadyError):
                run_qlora_training(config)

    def test_invalid_training_label_is_rejected(self) -> None:
        record = {
            "id": "bad",
            "text": "Unknown label",
            "entities": [
                {
                    "label": "sentiment",
                    "text": "Unknown",
                    "start_char": 0,
                    "end_char": 7,
                    "confidence": 1.0,
                    "evidence": "Unknown label",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.jsonl"
            path.write_text(json.dumps(record) + "\n", encoding="utf-8")

            with self.assertRaises(ExtractionValidationError):
                load_training_jsonl(path)


if __name__ == "__main__":
    unittest.main()
