"""Where extracted entities are kept: Postgres when configured, else memory.

Each document's entities are replaced as a whole when it is (re)processed.
Rows written here use ``extracted_entities.label`` (migration 0013) for the
extractor's own labels (decision, risk, action_item, owner, deadline, goal,
kpi, company_acronym); the table's older ``type`` enum is set to 'other'.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from omni_modal.data_access import EntityRecord


class InMemoryEntityStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[tuple[str, str], list[Any]] = {}

    def replace(self, tenant_id: str, document_id: str, records: list[Any]) -> None:
        with self._lock:
            self._records[(tenant_id, document_id)] = list(records)

    def get(self, tenant_id: str, document_id: str) -> list[Any]:
        with self._lock:
            return list(self._records.get((tenant_id, document_id), []))

    def delete(self, tenant_id: str, document_id: str) -> None:
        with self._lock:
            self._records.pop((tenant_id, document_id), None)

    def for_documents(self, tenant_id: str, document_ids: list[str]) -> list[Any]:
        with self._lock:
            return [r for d in document_ids for r in self._records.get((tenant_id, d), [])]


class PostgresEntityStore:
    def __init__(self, pool) -> None:
        self._pool = pool

    def replace(self, tenant_id: str, document_id: str, records: list[Any]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM extracted_entities WHERE tenant_id = %s AND document_id::text = %s",
                        (tenant_id, document_id))
            for r in records:
                cur.execute(
                    """INSERT INTO extracted_entities (id, tenant_id, document_id, type, value, confidence, label)
                       SELECT %s, %s, d.id, 'other', %s, %s, %s FROM documents d
                       WHERE d.id::text = %s AND d.tenant_id = %s
                       ON CONFLICT (id) DO NOTHING""",
                    (r.id, tenant_id, r.value[:2000], int(round(float(r.confidence or 0) * 100)), r.label,
                     document_id, tenant_id),
                )

    def _rows(self, tenant_id: str, document_ids: list[str]) -> list[Any]:
        if not document_ids:
            return []
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id::text, document_id::text, tenant_id, COALESCE(label, type::text), value,
                          COALESCE(confidence, 0)
                   FROM extracted_entities
                   WHERE tenant_id = %s AND document_id::text = ANY(%s)
                   ORDER BY created_at, id""",
                (tenant_id, list(document_ids)),
            )
            rows = cur.fetchall()
        return [EntityRecord(id=r[0], document_id=r[1], tenant_id=r[2], label=r[3], value=r[4],
                             confidence=float(r[5]) / 100.0) for r in rows]

    def get(self, tenant_id: str, document_id: str) -> list[Any]:
        return self._rows(tenant_id, [document_id])

    def delete(self, tenant_id: str, document_id: str) -> None:
        pass  # ON DELETE CASCADE from documents

    def for_documents(self, tenant_id: str, document_ids: list[str]) -> list[Any]:
        return self._rows(tenant_id, document_ids)


def select_entity_store():
    if os.environ.get("DATABASE_URL"):
        try:
            from omni_modal.db.pool import get_connection_pool  # noqa: PLC0415

            return PostgresEntityStore(get_connection_pool())
        except Exception as exc:  # noqa: BLE001
            from omni_modal.observability import observability  # noqa: PLC0415

            observability.capture_message(
                f"DATABASE_URL is set but entities cannot be stored in Postgres ({exc}); keeping them in memory.",
                operation="entities.store.select", level="error",
            )
    return InMemoryEntityStore()
