import unittest

import _path  # noqa: F401
from omni_modal.config import BackendSettings
from omni_modal.orchestration import Phase1Orchestrator


class Phase1OrchestratorTest(unittest.TestCase):
    def test_health_snapshot_is_redacted(self) -> None:
        settings = BackendSettings(
            environment="test",
            database_url_configured=True,
            sentry_dsn_configured=True,
            whisper_model_path_configured=False,
            qlora_entity_model_path_configured=False,
            adk_project_id_configured=False,
            a2a_delegation_endpoint_configured=False,
            gemini_interactions_endpoint_configured=False,
        )

        snapshot = Phase1Orchestrator(settings).health()
        serialized = repr(snapshot)

        self.assertEqual(snapshot["phase"], 1)
        self.assertEqual(snapshot["status"], "ok")
        self.assertIn("components", snapshot)
        self.assertNotIn("postgresql://", serialized)
        self.assertNotIn("DATABASE_URL", serialized)


if __name__ == "__main__":
    unittest.main()
