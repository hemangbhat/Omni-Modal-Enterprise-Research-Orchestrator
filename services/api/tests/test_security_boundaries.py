import os
import unittest
from unittest.mock import patch

import _path  # noqa: F401
from omni_modal.security.secrets import DATABASE_URL_SECRET, EnvSecretStore, SecretRef


class SecretBoundaryTest(unittest.TestCase):
    def test_secret_ref_string_is_redacted(self) -> None:
        secret = SecretRef("DATABASE_URL")

        self.assertIn("<redacted>", repr(secret))
        self.assertNotIn("postgresql://", repr(secret))

    def test_env_secret_store_reads_only_by_reference(self) -> None:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://secret-user:secret-pass@example/db"},
            clear=True,
        ):
            value = EnvSecretStore().get(DATABASE_URL_SECRET)

        self.assertEqual(value, "postgresql://secret-user:secret-pass@example/db")
        self.assertNotIn("secret-pass", repr(DATABASE_URL_SECRET))


if __name__ == "__main__":
    unittest.main()
