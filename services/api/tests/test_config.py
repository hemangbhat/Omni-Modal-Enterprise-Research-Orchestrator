import os
import unittest
from unittest.mock import patch

import _path  # noqa: F401
from omni_modal.config import load_settings


class BackendSettingsTest(unittest.TestCase):
    def test_redacted_settings_do_not_include_secret_values(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "test",
                "DATABASE_URL": "postgresql://secret-user:secret-pass@example/db",
                "SENTRY_DSN": "https://secret@example",
            },
            clear=True,
        ):
            settings = load_settings()

        redacted = settings.redacted()
        serialized = repr(redacted)

        self.assertTrue(redacted["database_url_configured"])
        self.assertTrue(redacted["sentry_dsn_configured"])
        self.assertNotIn("secret-user", serialized)
        self.assertNotIn("secret-pass", serialized)
        self.assertNotIn("https://secret@example", serialized)


if __name__ == "__main__":
    unittest.main()
