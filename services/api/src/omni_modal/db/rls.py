"""Row-Level Security helper — bind the active tenant to a DB transaction.

Migration ``0008_rls.sql`` enables RLS policies keyed on the ``app.tenant_id``
GUC. This module sets that GUC so the database enforces tenant isolation as a
second layer behind the application's own ``WHERE tenant_id = ...`` filters.

Usage (inside a transaction that runs tenant-scoped queries):

    with pool.connection() as conn:
        with conn.transaction():
            set_tenant(conn, tenant_id)      # SET LOCAL — scoped to this txn
            conn.execute(...)                # now RLS-enforced

``set_config(..., is_local=True)`` scopes the setting to the current
transaction, so a pooled connection never leaks one tenant's binding into the
next checkout.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator


def rls_enabled() -> bool:
    """True when DB-enforced tenant isolation should be applied.

    Off by default so enabling it is a deliberate, ops-controlled step (the
    ``0008_rls.sql`` policies must be applied first). Set ``RLS_ENFORCEMENT=true``
    to turn it on.
    """
    return os.environ.get("RLS_ENFORCEMENT", "false").lower() == "true"


def set_tenant(conn, tenant_id: str, *, local: bool = True) -> None:
    """Bind ``app.tenant_id`` for RLS enforcement on this connection/transaction.

    Uses the parameterised ``set_config`` function (a plain ``SET`` cannot take
    bound parameters), so the tenant id is never string-interpolated into SQL.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, %s)", (tenant_id, local))


def apply_tenant(conn, tenant_id: str) -> None:
    """Bind the tenant GUC only when RLS enforcement is enabled (no-op otherwise).

    Call this as the first statement inside the transaction that runs
    tenant-scoped queries. When ``RLS_ENFORCEMENT`` is unset, this does nothing,
    so the default behaviour is byte-for-byte unchanged.
    """
    if rls_enabled() and tenant_id:
        set_tenant(conn, tenant_id, local=True)


@contextmanager
def tenant_scope(conn, tenant_id: str) -> Iterator[None]:
    """Open a transaction with ``app.tenant_id`` set for its duration."""
    with conn.transaction():
        set_tenant(conn, tenant_id, local=True)
        yield
