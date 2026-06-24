"""Email adapter: console/log by default, Resend if RESEND_API_KEY is set.

The console adapter records every "sent" message in memory and prints it, so
invite/onboarding flows are fully testable offline. The Resend adapter uses the
stdlib ``urllib`` (no new dependency) and only activates when the API key is
present.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class SentEmail:
    to: str
    subject: str
    body: str
    backend: str


class EmailAdapter(Protocol):
    backend: str

    def send(self, *, to: str, subject: str, body: str) -> SentEmail: ...


class ConsoleEmailAdapter:
    """Logs emails to stderr and keeps a record for tests/UI."""

    backend = "console"

    def __init__(self) -> None:
        self.sent: list[SentEmail] = []

    def send(self, *, to: str, subject: str, body: str) -> SentEmail:
        msg = SentEmail(to=to, subject=subject, body=body, backend=self.backend)
        self.sent.append(msg)
        print(f"[email:console] to={to!r} subject={subject!r}", file=sys.stderr)
        return msg


class ResendEmailAdapter:
    """Sends real email via Resend's REST API using stdlib urllib."""

    backend = "resend"
    _ENDPOINT = "https://api.resend.com/emails"

    def __init__(self) -> None:
        self._api_key = os.environ["RESEND_API_KEY"]
        self._from = os.environ.get("RESEND_FROM", "OMERO <onboarding@resend.dev>")

    def send(self, *, to: str, subject: str, body: str) -> SentEmail:
        import urllib.request  # noqa: PLC0415

        payload = json.dumps(
            {"from": self._from, "to": [to], "subject": subject, "html": body}
        ).encode()
        req = urllib.request.Request(
            self._ENDPOINT,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):  # noqa: S310
            pass
        return SentEmail(to=to, subject=subject, body=body, backend=self.backend)


def select_email_adapter() -> EmailAdapter:
    if os.environ.get("RESEND_API_KEY"):
        try:
            return ResendEmailAdapter()
        except Exception as exc:  # pragma: no cover
            print(
                f"[email] RESEND_API_KEY set but Resend adapter unavailable ({exc}); "
                f"falling back to console email.",
                file=sys.stderr,
            )
    return ConsoleEmailAdapter()
