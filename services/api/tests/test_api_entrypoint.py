"""`python -m omni_modal.api` must pick up the repository .env, like the stdlib
reference server does, and must do so only when launched as the entry point."""

from __future__ import annotations

import unittest
from unittest import mock

import _path  # noqa: F401

from omni_modal.api import __main__ as entrypoint


class ApiEntrypointTests(unittest.TestCase):
    def test_main_loads_dotenv_before_starting_uvicorn(self) -> None:
        calls: list[str] = []
        with mock.patch("omni_modal.env_loader.load_dotenv", side_effect=lambda: calls.append("dotenv")), \
             mock.patch("uvicorn.run", side_effect=lambda *a, **k: calls.append("uvicorn")):
            entrypoint.main()
        self.assertEqual(calls, ["dotenv", "uvicorn"])


if __name__ == "__main__":
    unittest.main()
