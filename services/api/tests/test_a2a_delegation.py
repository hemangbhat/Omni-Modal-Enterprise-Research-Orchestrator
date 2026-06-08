import json
import unittest

import _path  # noqa: F401
from omni_modal.orchestration import build_a2a_request, parse_a2a_response


class A2ADelegationTest(unittest.TestCase):
    def test_request_message_excludes_internal_content(self) -> None:
        request = build_a2a_request(
            tenant_id="tenant",
            user_id="user",
            question="What public evidence explains procurement delay?",
            reason="Internal retrieval did not return enough evidence.",
            internal_status="insufficient",
        )

        message = request.to_message()

        self.assertEqual(message["method"], "research.answer_external")
        self.assertFalse(message["metadata"]["contains_internal_content"])
        self.assertNotIn("document_chunks", repr(message))

    def test_parse_a2a_response_returns_findings(self) -> None:
        raw = json.dumps(
            {
                "result": {
                    "summary": "External research found one public signal.",
                    "findings": [
                        {
                            "claim": "Public sources mention vendor review delays.",
                            "source_title": "Public source",
                            "source_url": "https://example.com/source",
                            "confidence": 0.8,
                        }
                    ],
                }
            }
        )

        response = parse_a2a_response("req-1", raw)

        self.assertEqual(response.status, "ok")
        self.assertEqual(len(response.findings), 1)
        self.assertEqual(response.findings[0].source_title, "Public source")

    def test_parse_a2a_response_handles_invalid_json(self) -> None:
        response = parse_a2a_response("req-1", "{")

        self.assertEqual(response.status, "failed")
        self.assertEqual(response.findings, [])
        self.assertIn("Invalid A2A JSON", response.error_message or "")


if __name__ == "__main__":
    unittest.main()
