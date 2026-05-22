"""
HTTP Client Factory
===================

Shared transport/client factory for provider integrations.

Why this module exists:
    If every provider creates its own HTTP transport, connection reuse suffers
    and socket usage grows quickly under load. This factory centralizes client
    construction so REST-based providers can share one pooled transport.

Step-by-step flow:
    1. Startup creates ``HTTPClientFactory`` with ``HTTPPoolConfig``.
    2. Factory builds one shared ``httpx.AsyncHTTPTransport``.
    3. Provider registry requests a client per provider type.
    4. REST providers receive ``httpx.AsyncClient`` bound to shared transport.
    5. AWS SDK providers receive ``aioboto3.Session``.

Jargon explained:
    - Transport: low-level HTTP engine that owns connection pooling.
    - Keep-alive pool: reusable TCP connections kept open for future requests.
    - Provider type: integration style (REST API vs AWS SDK).

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.core.settings.models.global_config import HTTPPoolConfig

logger = logging.getLogger(__name__)


class HTTPClientFactory:
    """Create transport clients with predictable performance and failure behavior.

    What a new developer should know:
        This class does not perform provider calls itself. It only constructs
        client objects configured for efficient reuse. Centralizing this avoids
        duplicated timeout/pool settings scattered across provider adapters.

    Example:
        >>> factory = HTTPClientFactory(pool_config)
        >>> rest_client = factory.create_client("rest_api")
        >>> aws_session = factory.create_client("aws_sdk")
    """

    def __init__(self, pool_config: HTTPPoolConfig) -> None:
        """Initialize the shared REST transport from pool configuration.

        Args:
            pool_config: Global HTTP pooling and timeout defaults.

        Rationale:
            A shared transport gives all REST providers one connection pool.
            This reduces handshake overhead and improves throughput under load.
        """
        self._pool_config = pool_config
        self._shared_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=pool_config.max_connections,
                max_keepalive_connections=pool_config.max_keepalive_connections,
                keepalive_expiry=pool_config.keepalive_expiry_seconds,
            ),
            # Retries are intentionally disabled here; provider adapters own
            # retry policy so behavior can differ by provider/error type.
            retries=0,
        )
        logger.info(
            "HTTP transport pool created: max_connections=%d, keepalive=%d",
            pool_config.max_connections,
            pool_config.max_keepalive_connections,
        )

    def create_client(self, provider_type: str) -> httpx.AsyncClient | object:
        """Return an appropriate client/session for a provider integration style.

        Args:
            provider_type: ``rest_api``, ``aws_sdk``, or ``grpc``.

        Returns:
            - ``httpx.AsyncClient`` for REST providers.
            - ``aioboto3.Session`` for AWS SDK providers.

        Raises:
            ValueError: If provider_type is unsupported.
            NotImplementedError: For ``grpc`` placeholder path.

        Rationale:
            Keeping provider-type branching in one place makes it obvious which
            transport stack each integration uses and simplifies future updates.
        """
        if provider_type == "rest_api":
            return self._create_rest_client()
        if provider_type == "aws_sdk":
            return self._create_aws_session()
        if provider_type == "grpc":
            return self._create_grpc_stub()
        raise ValueError(
            f"Unsupported provider_type: {provider_type!r}. "
            "Expected one of: rest_api, aws_sdk, grpc."
        )

    def _create_rest_client(self) -> httpx.AsyncClient:
        """Create a REST client bound to the shared transport pool.

        A new ``AsyncClient`` instance is returned, but transport and sockets
        are shared, which is the key performance optimization.

        Why not a singleton AsyncClient:
            Returning a client per provider keeps adapter composition simple,
            while shared transport still preserves pool reuse.
        """
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

    def _create_aws_session(self) -> object:
        """Create an ``aioboto3.Session`` for AWS SDK-based providers.

        Returns a sentinel object when ``aioboto3`` is unavailable so runtime
        errors are explicit and actionable.

        Rationale:
            This fails loudly with guidance instead of silently returning an
            unusable object that would break deeper in call paths.
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

    @staticmethod
    def _create_grpc_stub() -> object:
        """Placeholder for future gRPC provider transport support."""
        raise NotImplementedError("gRPC provider transport is not yet implemented.")


class _MissingAioBoto3Session:
    """Sentinel returned when ``aioboto3`` is not installed.

    Any attribute access raises a clear runtime error so operators get a direct
    remediation path instead of an opaque attribute failure.
    """

    def __getattr__(self, name: str) -> object:
        raise RuntimeError(
            "aioboto3 is not installed, but an AWS SDK provider (Bedrock) was "
            "requested. Install it with: pip install aioboto3"
        )
