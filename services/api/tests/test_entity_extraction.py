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

    def test_decisions_risks_and_actions_are_whole_sentences(self) -> None:
        text = (
            "Revenue grew. The board approved a budget of 2.4 million dollars. "
            "The board flagged hiring in the security team as the main open risk. "
            "Action item: Priya Shah to present a hiring plan by November 15."
        )
        output = RuleBasedEnterpriseEntityExtractor().extract(tenant_id="t", document_id="d", text=text)
        by_label = {}
        for e in output.entities:
            by_label.setdefault(e.label.value, []).append(e.text)
        self.assertEqual(by_label["decision"], ["The board approved a budget of 2.4 million dollars."])
        self.assertEqual(by_label["risk"], ["The board flagged hiring in the security team as the main open risk."])
        self.assertEqual(by_label["action_item"], ["Action item: Priya Shah to present a hiring plan by November 15."])
        self.assertIn("by November 15", by_label["deadline"])

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
