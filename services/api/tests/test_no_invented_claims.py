"""The API must not emit facts it does not have.

Earlier versions decorated document-derived views with constants presented as
data (an encryption label, a retention period, a classification, a project
priority). Nothing in the system computes or enforces those, so they must not
come back.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import _path  # noqa: F401

SRC = Path(__file__).resolve().parents[1] / "src" / "omni_modal"

BANNED = (
    "AES-256",
    "7 years",
    '"classification": "Internal"',
    '"priority": "Medium"',
    '"status": "Active"',
    '"Recently"',
)


class NoInventedClaimsTests(unittest.TestCase):
    def test_backend_source_has_no_invented_constants(self) -> None:
        offenders = [
            f"{path.relative_to(SRC)}: {phrase}"
            for path in sorted(SRC.rglob("*.py"))
            for phrase in BANNED
            if phrase in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
