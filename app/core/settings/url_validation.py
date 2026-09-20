"""URL validation shared by environment-backed settings models.

Architecture:
    raw environment string -> validate_service_url -> typed settings -> adapter

Pydantic URL objects are excellent contracts but many client libraries expect
plain strings. These validators keep that caller-friendly type while rejecting
wrong schemes, relative addresses, and malformed hosts at startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Collection


def validate_service_url(
    value: str,
    *,
    field_name: str,
    allowed_schemes: Collection[str],
) -> str:
    """Require an absolute URL whose scheme belongs to one adapter family."""
    parsed = urlsplit(value)
    if parsed.scheme not in allowed_schemes or not parsed.hostname:
        raise ValueError(
            f"{field_name} must be an absolute URL using one of "
            f"{sorted(allowed_schemes)}, got {value!r}"
        )
    return value


def validate_browser_origin(value: str) -> str:
    """Require an HTTP(S) origin without credentials, paths, queries, or fragments."""
    validate_service_url(
        value,
        field_name="cors_allowed_origins entry",
        allowed_schemes={"http", "https"},
    )
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.path not in {"", "/"}:
        raise ValueError(f"CORS value must be an origin only, got {value!r}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"CORS origin cannot contain a query or fragment, got {value!r}")
    return value.rstrip("/")


def validate_provider_endpoint_url(value: str) -> str:
    """Require a clean HTTP(S) base URL suitable for outbound provider calls.

    Private network hosts remain allowed because self-hosted vLLM is a supported
    deployment. Embedded credentials and fragments are never legitimate base
    endpoint configuration and are rejected to reduce leakage and ambiguity.
    """
    validate_service_url(
        value,
        field_name="api_endpoint_url",
        allowed_schemes={"http", "https"},
    )
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValueError("api_endpoint_url cannot contain embedded credentials")
    if parsed.fragment:
        raise ValueError("api_endpoint_url cannot contain a URL fragment")
    return value.rstrip("/")
