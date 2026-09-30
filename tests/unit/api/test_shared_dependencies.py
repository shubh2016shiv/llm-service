"""Tests for typed application-state dependency access."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request

from app.adapters.cache import RedisCache
from app.api.shared_dependencies import (
    get_credential_writer,
    get_inference_authorization_cache,
    optional_app_state,
    require_app_state,
)
from app.auth.authorization import AuthorizationGrantCache
from app.services.credential_encoding import CredentialWriter


class FakeCredentialWriter:
    """Minimal structurally typed credential writer for state-boundary tests."""

    async def write_secret(
        self, path: str, *, tenant_id: str, fields: dict[str, str | None]
    ) -> str:
        return path

    async def delete_secret(self, secret_reference: str, *, tenant_id: str) -> None:
        return None


def _request(app: FastAPI) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "app": app,
        }
    )


def test_require_app_state_rejects_wrong_runtime_type() -> None:
    """A wiring mistake fails at the dependency boundary with useful context."""
    app = FastAPI()
    app.state.example = "not-an-integer"

    with pytest.raises(RuntimeError, match="has type str; expected int"):
        require_app_state(_request(app), "example", int, hint="Initialize example.")


def test_require_app_state_returns_typed_value() -> None:
    """Correctly wired lifespan state passes through unchanged."""
    app = FastAPI()
    app.state.example = 42

    assert require_app_state(_request(app), "example", int, hint="Initialize example.") == 42


def test_optional_app_state_returns_none_when_resource_is_unconfigured() -> None:
    """An intentionally absent optional adapter remains a supported state."""
    app = FastAPI()

    assert (
        optional_app_state(
            _request(app), "credential_writer", CredentialWriter, hint="Configure it."
        )
        is None
    )

    assert get_credential_writer(_request(app)) is None


def test_optional_app_state_returns_structurally_typed_resource() -> None:
    """Runtime-checkable protocols accept compatible backend implementations."""
    app = FastAPI()
    writer = FakeCredentialWriter()
    app.state.credential_writer = writer

    assert (
        optional_app_state(
            _request(app), "credential_writer", CredentialWriter, hint="Configure it."
        )
        is writer
    )

    assert get_credential_writer(_request(app)) is writer


def test_optional_app_state_rejects_wrong_runtime_type() -> None:
    """A configured optional resource still must satisfy its boundary contract."""
    app = FastAPI()
    app.state.redis_cache = "not-a-cache"

    with pytest.raises(RuntimeError, match="expected RedisCache or None"):
        optional_app_state(_request(app), "redis_cache", RedisCache, hint="Configure it.")


def test_authorization_cache_dependency_does_not_use_nontransactional_grants() -> None:
    """REQ: committed permission changes cannot leave a cached allow grant usable."""
    app = FastAPI()

    without_backend = get_inference_authorization_cache(_request(app))
    app.state.redis_cache = object.__new__(RedisCache)
    with_backend = get_inference_authorization_cache(_request(app))

    assert isinstance(without_backend, AuthorizationGrantCache)
    assert isinstance(with_backend, AuthorizationGrantCache)
    assert without_backend._backend is None
    assert with_backend._backend is None
