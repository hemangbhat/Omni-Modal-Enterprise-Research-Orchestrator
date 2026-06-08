from __future__ import annotations

ENDPOINT_ROLES: dict[str, frozenset[str]] = {
    "/query":          frozenset({"researcher", "admin"}),
    "/query/stream":   frozenset({"researcher", "admin"}),
    "/ingest/local":   frozenset({"researcher", "admin"}),
    "/ingest/upload":  frozenset({"researcher", "admin"}),
    "/documents":      frozenset({"researcher", "admin", "auditor"}),
    "/projects":       frozenset({"researcher", "admin", "auditor"}),
    "/archives":       frozenset({"researcher", "admin", "auditor"}),
    # /entities/:id is a prefix route — checked by prefix match in assert_endpoint_roles
}

# Prefix-based role requirements for dynamic path patterns
ENDPOINT_ROLE_PREFIXES: dict[str, frozenset[str]] = {
    "/entities/":  frozenset({"researcher", "admin", "auditor"}),
    "/archives/":  frozenset({"researcher", "admin", "auditor"}),
}


class RbacError(Exception):
    pass


def assert_endpoint_roles(path: str, roles: tuple[str, ...]) -> None:
    """Raise RbacError if the caller's roles don't satisfy the endpoint's requirements."""
    # Exact match first
    required = ENDPOINT_ROLES.get(path)
    if required is None:
        # Prefix match for dynamic routes like /entities/:id
        for prefix, prefix_roles in ENDPOINT_ROLE_PREFIXES.items():
            if path.startswith(prefix):
                required = prefix_roles
                break
    if required is None:
        return  # unknown path — 404 will be returned by the normal handler
    if not frozenset(roles) & required:
        raise RbacError(
            f"Endpoint {path} requires one of: {', '.join(sorted(required))}."
        )
