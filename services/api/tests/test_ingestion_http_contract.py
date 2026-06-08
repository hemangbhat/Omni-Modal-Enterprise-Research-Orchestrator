import unittest

import _path  # noqa: F401
from omni_modal.ingestion.http_contract import (
    IngestionContractError,
    ingestion_request_from_payload,
)


class IngestionHttpContractTest(unittest.TestCase):
    def test_payload_converts_to_ingestion_request(self) -> None:
        request = ingestion_request_from_payload(
            {
                "tenant_id": "tenant",
                "document_id": "doc",
                "owner_id": "user",
                "file_path": "sample.pdf",
                "source_kind": "pdf",
                "title": "Sample",
            }
        )

        self.assertEqual(request.tenant_id, "tenant")
        self.assertEqual(request.document_id, "doc")
        self.assertEqual(request.owner_id, "user")
        self.assertEqual(request.file_path.name, "sample.pdf")
        self.assertEqual(request.source_kind, "pdf")
        self.assertEqual(request.title, "Sample")

    def test_payload_rejects_missing_required_fields(self) -> None:
        with self.assertRaises(IngestionContractError):
            ingestion_request_from_payload({"tenant_id": "tenant"})

    def test_payload_rejects_unsupported_source_kind(self) -> None:
        with self.assertRaises(IngestionContractError):
            ingestion_request_from_payload(
                {
                    "tenant_id": "tenant",
                    "document_id": "doc",
                    "owner_id": "user",
                    "file_path": "sample.txt",
                    "source_kind": "txt",
                }
            )


if __name__ == "__main__":
    unittest.main()
