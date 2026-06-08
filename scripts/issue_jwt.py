"""Issue a short-lived HS256 JWT for local development / demos.

Usage (from repo root, with JWT_SECRET set in your environment or .env):

    python scripts/issue_jwt.py --tenant acme --user u-123 --roles researcher,admin

Prints a token you can use as:  Authorization: Bearer <token>

The signing secret comes from the JWT_SECRET environment variable and MUST
match the backend's JWT_SECRET, otherwise verification will fail.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

# Allow running directly from the repo root without installing the package.
_API_SRC = os.path.join(
    os.path.dirname(__file__), "..", "services", "api", "src"
)
sys.path.insert(0, os.path.abspath(_API_SRC))

from omni_modal.security.auth import _make_jwt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Issue a local HS256 JWT.")
    parser.add_argument("--tenant", default="demo-tenant", help="tenant_id claim")
    parser.add_argument("--user", default="demo-user", help="user_id claim")
    parser.add_argument(
        "--roles",
        default="researcher",
        help="comma-separated roles (e.g. 'researcher,admin')",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=3600,
        help="token lifetime in seconds (default 3600)",
    )
    args = parser.parse_args()

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        print(
            "ERROR: JWT_SECRET is not set. Export it (and use the same value in "
            "the backend) before issuing a token.",
            file=sys.stderr,
        )
        return 1

    roles = [r.strip() for r in args.roles.split(",") if r.strip()]
    exp = int(time.time()) + args.ttl
    token = _make_jwt(args.tenant, args.user, roles, exp, secret)
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
