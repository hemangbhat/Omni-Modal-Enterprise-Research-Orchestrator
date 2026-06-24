"""In-app notification center.

Thread-safe in-memory store of notifications scoped to a tenant (and optionally
to a specific user). Notifications are created by backend events (ingestion
finished, invite sent, plan limit approaching, etc.) and surfaced in the UI's
notification center. Email delivery is a separate concern handled by the
optional email adapter.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass

# Notification severity / kind hints (free-form, used for UI iconography).
KIND_INFO = "info"
KIND_SUCCESS = "success"
KIND_WARNING = "warning"
KIND_ERROR = "error"


@dataclass
class Notification:
    id: str
    tenant_id: str
    user_id: str | None
    kind: str
    title: str
    body: str
    read: bool
    created_at: float

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "kind": self.kind,
            "title": self.title,
            "body": self.body,
            "read": self.read,
            "created_at": self.created_at,
        }


class NotificationStore:
    """Thread-safe notification store with a bounded ring per tenant."""

    def __init__(self, max_per_tenant: int = 200) -> None:
        self._lock = threading.RLock()
        self._by_tenant: dict[str, list[Notification]] = {}
        self._max = max_per_tenant
        self._seq = itertools.count(1)

    def add(
        self,
        *,
        tenant_id: str,
        title: str,
        body: str = "",
        kind: str = KIND_INFO,
        user_id: str | None = None,
    ) -> Notification:
        with self._lock:
            note = Notification(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                user_id=user_id,
                kind=kind,
                title=title,
                body=body,
                read=False,
                created_at=time.time(),
            )
            bucket = self._by_tenant.setdefault(tenant_id, [])
            bucket.append(note)
            # Trim oldest beyond the cap.
            if len(bucket) > self._max:
                del bucket[: len(bucket) - self._max]
            return note

    def list_for(
        self, tenant_id: str, *, user_id: str | None = None, unread_only: bool = False
    ) -> list[Notification]:
        with self._lock:
            items = list(self._by_tenant.get(tenant_id, []))
        # tenant-wide notifications (user_id None) are visible to everyone;
        # user-scoped notifications only to that user.
        result = [
            n
            for n in items
            if (n.user_id is None or user_id is None or n.user_id == user_id)
            and (not unread_only or not n.read)
        ]
        result.sort(key=lambda n: n.created_at, reverse=True)
        return result

    def unread_count(self, tenant_id: str, *, user_id: str | None = None) -> int:
        return len(self.list_for(tenant_id, user_id=user_id, unread_only=True))

    def mark_read(self, tenant_id: str, notification_id: str) -> bool:
        with self._lock:
            for n in self._by_tenant.get(tenant_id, []):
                if n.id == notification_id:
                    n.read = True
                    return True
            return False

    def mark_all_read(self, tenant_id: str, *, user_id: str | None = None) -> int:
        count = 0
        with self._lock:
            for n in self._by_tenant.get(tenant_id, []):
                if not n.read and (n.user_id is None or user_id is None or n.user_id == user_id):
                    n.read = True
                    count += 1
        return count
