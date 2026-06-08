"""Property 17: SecretRef String Representation Never Reveals Secret Value
Validates: Requirements 9.5
"""
from __future__ import annotations

import re
import unittest

import _path  # noqa: F401
from hypothesis import given, settings
import hypothesis.strategies as st

from omni_modal.security.secrets import SecretRef

# The repr template is: SecretRef(name=<name_repr>, value=<redacted>)
# Only the name (via its repr) and the literal template text should appear.
_REPR_PATTERN = re.compile(
    r"^SecretRef\(name=.*,\s*value=<redacted>\)$"
)

# Env-var-style names: uppercase letters, digits, underscores — no secret leakage
# ambiguity possible via repr() escaping.
_ENVVAR_NAME = st.text(
    alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789",
    min_size=1,
    max_size=50,
)


class TestSecretRefNoLeakProperty(unittest.TestCase):
    """Property 17: SecretRef String Representation Never Reveals Secret Value
    Validates: Requirements 9.5"""

    @given(name=_ENVVAR_NAME)
    @settings(max_examples=200)
    def test_str_matches_redacted_pattern(self, name: str) -> None:
        """str(SecretRef) must match the redaction template exactly.

        The output must be of the form:
            SecretRef(name=<name_repr>, value=<redacted>)
        This ensures no extraneous data (like an actual secret value) can
        sneak into the string representation.
        """
        ref = SecretRef(name=name)
        result = str(ref)
        self.assertRegex(
            result,
            _REPR_PATTERN,
            f"str(SecretRef) does not match redaction pattern: {result!r}",
        )

    @given(name=_ENVVAR_NAME)
    @settings(max_examples=200)
    def test_repr_matches_redacted_pattern(self, name: str) -> None:
        """repr(SecretRef) must match the redaction template exactly."""
        ref = SecretRef(name=name)
        result = repr(ref)
        self.assertRegex(
            result,
            _REPR_PATTERN,
            f"repr(SecretRef) does not match redaction pattern: {result!r}",
        )

    @given(name=_ENVVAR_NAME)
    @settings(max_examples=200)
    def test_str_contains_redacted_literal(self, name: str) -> None:
        """str(SecretRef) must contain the literal string '<redacted>'."""
        ref = SecretRef(name=name)
        self.assertIn("<redacted>", str(ref))

    @given(name=_ENVVAR_NAME)
    @settings(max_examples=200)
    def test_repr_contains_redacted_literal(self, name: str) -> None:
        """repr(SecretRef) must contain the literal string '<redacted>'."""
        ref = SecretRef(name=name)
        self.assertIn("<redacted>", repr(ref))

    @given(name=_ENVVAR_NAME)
    @settings(max_examples=200)
    def test_name_is_visible_in_str(self, name: str) -> None:
        """The name must be visible in str() to aid debugging."""
        ref = SecretRef(name=name)
        self.assertIn(name, str(ref))

    # ---------- example-based baseline tests ----------

    def test_str_contains_redacted_marker(self) -> None:
        """str() must contain '<redacted>' to confirm redaction is active."""
        ref = SecretRef(name="API_KEY")
        self.assertIn("<redacted>", str(ref))

    def test_repr_contains_redacted_marker(self) -> None:
        ref = SecretRef(name="API_KEY")
        self.assertIn("<redacted>", repr(ref))

    def test_name_visible_in_str(self) -> None:
        """The name field should still be visible for debugging purposes."""
        ref = SecretRef(name="DATABASE_URL")
        self.assertIn("DATABASE_URL", str(ref))


if __name__ == "__main__":
    unittest.main()
