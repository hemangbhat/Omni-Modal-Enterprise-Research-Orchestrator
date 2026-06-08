import unittest

import _path  # noqa: F401
from omni_modal.mcp import (
    ChunkSummary,
    DocumentDetail,
    EntitySummary,
    InMemoryMcpRepository,
    McpServer,
    McpToolRouter,
    ToolContext,
)


def _repository() -> InMemoryMcpRepository:
    return InMemoryMcpRepository(
        documents=[
            DocumentDetail(
                id="doc-1",
                title="Enterprise renewal review",
                source_type="pdf",
                status="ready",
                owner_id="user-1",
                source_uri="s3://internal/doc-1.pdf",
                metadata={"department": "sales"},
            )
        ],
        chunks=[
            ChunkSummary(
                id="chunk-1",
                document_id="doc-1",
                title="Enterprise renewal review",
                chunk_index=0,
                content="Security review risk may delay renewal approval.",
                metadata={"page_number": 2},
            )
        ],
        entities=[
            EntitySummary(
                id="entity-1",
                document_id="doc-1",
                chunk_id="chunk-1",
                label="risk",
                value="Security review risk",
                normalized_value="security_review_risk",
                confidence=0.84,
            )
        ],
    )


class McpToolsTest(unittest.TestCase):
    def test_lists_expected_tools(self) -> None:
        repository = _repository()
        router = McpToolRouter(repository, repository)

        names = {tool["name"] for tool in router.list_tools()}

        self.assertEqual(
            names,
            {
                "search_documents",
                "get_document",
                "search_chunks",
                "get_entities",
                "get_audit_logs",
            },
        )

    def test_search_documents_returns_structured_result_and_audit(self) -> None:
        repository = _repository()
        router = McpToolRouter(repository, repository)
        context = ToolContext(
            tenant_id="tenant",
            actor_user_id="user-1",
            roles=("researcher",),
        )

        result = router.call_tool(
            context,
            "search_documents",
            {"query": "renewal", "limit": 5},
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data["documents"][0]["id"], "doc-1")
        self.assertIsNotNone(result.audit_id)
        self.assertEqual(repository.audit_logs[0].action, "mcp.search_documents")

    def test_audit_logs_require_auditor_or_admin(self) -> None:
        repository = _repository()
        router = McpToolRouter(repository, repository)
        context = ToolContext(
            tenant_id="tenant",
            actor_user_id="user-1",
            roles=("researcher",),
        )

        result = router.call_tool(context, "get_audit_logs", {"limit": 5})

        self.assertEqual(result.status, "denied")
        self.assertIn("requires one of", result.error or "")
        self.assertEqual(repository.audit_logs[0].metadata["status"], "denied")

    def test_get_entities_filters_labels(self) -> None:
        repository = _repository()
        router = McpToolRouter(repository, repository)
        context = ToolContext(
            tenant_id="tenant",
            actor_user_id="auditor-1",
            roles=("auditor",),
        )

        result = router.call_tool(
            context,
            "get_entities",
            {"document_id": "doc-1", "labels": ["risk"]},
        )

        self.assertEqual(result.status, "ok")
        self.assertEqual(result.data["entities"][0]["label"], "risk")

    def test_server_handles_tools_call_json_rpc(self) -> None:
        repository = _repository()
        server = McpServer(McpToolRouter(repository, repository))

        response = server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "search_chunks",
                    "context": {
                        "tenant_id": "tenant",
                        "actor_user_id": "user-1",
                        "roles": ["researcher"],
                    },
                    "arguments": {"query": "security", "limit": 3},
                },
            }
        )

        self.assertEqual(response["id"], 1)
        self.assertFalse(response["result"]["isError"])
        content = response["result"]["content"][0]["json"]
        self.assertEqual(content["data"]["chunks"][0]["id"], "chunk-1")


if __name__ == "__main__":
    unittest.main()
