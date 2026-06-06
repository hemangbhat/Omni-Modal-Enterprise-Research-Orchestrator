from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SecretRef:
    name: str

    def __repr__(self) -> str:
        return f"SecretRef(name={self.name!r}, value=<redacted>)"

    def __str__(self) -> str:
        return self.__repr__()


class SecretStore(Protocol):
    def get(self, ref: SecretRef) -> str:
        """Return a secret value for infrastructure code only."""


class EnvSecretStore:
    def get(self, ref: SecretRef) -> str:
        value = os.environ.get(ref.name)
        if not value:
            raise RuntimeError(f"Required secret is not configured: {ref.name}")
        return value


DATABASE_URL_SECRET = SecretRef("DATABASE_URL")
