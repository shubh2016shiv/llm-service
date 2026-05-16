"""
app/infrastructure/http_client_factory.py — Shared, pooled HTTP client factory.

Architecture
------------
    ┌─────────────────────────────────┐
    │     HTTPClientFactory            │
    │                                 │
    │  _pool_config: HTTPPoolConfig   │
    │  _shared_transport: AsyncHTTP.. │
    │                                 │
    │  + create_client(provider_type) │
    │    → httpx.AsyncClient           │  (rest_api)
    │    → aioboto3.Session            │  (aws_sdk)
    └─────────────────────────────────┘

Design (per implementation_plan.md §8)
--------------------------------------
- One shared httpx.AsyncHTTPTransport for all REST providers → same TCP/TLS pool.
- Retries = 0 at the transport layer; retry logic lives in each provider via tenacity.
- Bedrock (aws_sdk) gets its own aioboto3.Session with a separate connection pool.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.core.settings.models.global_config import HTTPPoolConfig

logger = logging.getLogger(__name__)


class HTTPClientFactory:
    """Creates shared, pooled HTTP clients for LLM providers.

    REST API providers (OpenAI, Anthropic, vLLM, Azure OpenAI) share a single
    httpx transport for connection pooling. AWS SDK providers (Bedrock) get a
    dedicated aioboto3 session.

    Usage::

        factory = HTTPClientFactory(pool_config)
        openai_client = factory.create_client(ProviderType.REST_API)
        bedrock_session = factory.create_client(ProviderType.AWS_SDK)
    """

    def __init__(self, pool_config: HTTPPoolConfig) -> None:
        self._pool_config = pool_config
        self._shared_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=pool_config.max_connections,
                max_keepalive_connections=pool_config.max_keepalive_connections,
                keepalive_expiry=pool_config.keepalive_expiry_seconds,
            ),
            retries=0,  # Retry logic lives in provider layer via tenacity
        )
        logger.info(
            "HTTP transport pool created: max_connections=%d, keepalive=%d",
            pool_config.max_connections,
            pool_config.max_keepalive_connections,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_client(self, provider_type: str) -> httpx.AsyncClient | object:
        """Return a client appropriate for the provider type.

        Args:
            provider_type: One of ``"rest_api"``, ``"aws_sdk"``, or ``"grpc"``.

        Returns:
            - ``httpx.AsyncClient`` for ``rest_api``
            - ``aioboto3.Session`` for ``aws_sdk``
            - Raises ``ValueError`` for unknown types.

        Raises:
            ValueError: If *provider_type* is not supported.
        """
        if provider_type == "rest_api":
            return self._create_rest_client()
        if provider_type == "aws_sdk":
            return self._create_aws_session()
        if provider_type == "grpc":
            return self._create_grpc_stub()
        raise ValueError(
            f"Unsupported provider_type: {provider_type!r}. "
            f"Expected one of: rest_api, aws_sdk, grpc."
        )

    # ------------------------------------------------------------------
    # Internal: REST
    # ------------------------------------------------------------------

    def _create_rest_client(self) -> httpx.AsyncClient:
        """Return an httpx client sharing the pooled transport."""
        return httpx.AsyncClient(
            transport=self._shared_transport,
            timeout=httpx.Timeout(
                self._pool_config.read_timeout_seconds,
                connect=self._pool_config.connect_timeout_seconds,
                read=self._pool_config.read_timeout_seconds,
                write=self._pool_config.write_timeout_seconds,
                pool=5.0,
            ),
        )

    # ------------------------------------------------------------------
    # Internal: AWS SDK (aioboto3)
    # ------------------------------------------------------------------

    def _create_aws_session(self) -> object:
        """Create and return an aioboto3.Session.

        Falls back gracefully if aioboto3 is not installed — BedrockProvider
        will surface the error at call time.
        """
        try:
            import aioboto3  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "aioboto3 is not installed. Bedrock provider will fail at call time. "
                "Install with: pip install aioboto3"
            )
            return _MissingAioBoto3Session()

        logger.debug("aioboto3 session created for Bedrock provider.")
        return aioboto3.Session()

    # ------------------------------------------------------------------
    # Internal: gRPC (reserved)
    # ------------------------------------------------------------------

    @staticmethod
    def _create_grpc_stub() -> object:
        """Reserved for future gRPC-based providers."""
        raise NotImplementedError("gRPC provider transport is not yet implemented.")


# ---------------------------------------------------------------------------
# Sentinel for missing aioboto3
# ---------------------------------------------------------------------------


class _MissingAioBoto3Session:
    """Sentinel returned when aioboto3 is not installed.

    Raises a clear RuntimeError if any method is called, giving the operator
    an actionable error message instead of an opaque AttributeError.
    """

    def __getattr__(self, name: str) -> object:
        raise RuntimeError(
            "aioboto3 is not installed, but an AWS SDK provider (Bedrock) was "
            "requested. Install it with: pip install aioboto3"
        )
