"""
Provider Transport Factory
==========================

Shared transport/client factory for provider integrations.

Why this module exists:
    If every provider creates its own HTTP transport, connection reuse suffers
    and socket usage grows quickly under load. This factory centralizes client
    construction so REST-based providers can share one pooled transport.

Step-by-step flow:
    1. Startup creates ``ProviderTransportFactory`` with ``HTTPPoolConfig``.
    2. Factory builds one shared ``httpx.AsyncClient`` and connection pool.
    3. Provider registry requests a client/session per provider type.
    4. Every REST provider receives that factory-owned shared client.
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
    from app.core.settings.models.provider_config import ProviderType

logger = logging.getLogger(__name__)


class ProviderTransportFactory:
    """Create transport clients with predictable performance and failure behavior.

    What a new developer should know:
        This class does not perform provider calls itself. It only constructs
        client objects configured for efficient reuse. Centralizing this avoids
        duplicated timeout/pool settings scattered across provider adapters.

    Ownership rule:
        REST providers borrow the returned client; they must never close it.
        The application closes this factory once during shutdown. Returning
        one client, rather than many clients around one low-level transport,
        makes that ownership rule enforceable: closing an ``AsyncClient`` also
        closes its transport and therefore its connection pool.

    Example:
        >>> factory = ProviderTransportFactory(pool_config)
        >>> rest_client = factory.create_transport(ProviderType.REST_API)
        >>> aws_session = factory.create_transport(ProviderType.AWS_SDK)
    """

    def __init__(self, pool_config: HTTPPoolConfig) -> None:
        """Initialize the shared REST transport from pool configuration.

        Args:
            pool_config: Global HTTP pooling and timeout defaults.

        Rationale:
            A shared transport gives all REST providers one connection pool.
            This reduces handshake overhead and improves throughput under load.
        """
        shared_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=pool_config.max_connections,
                max_keepalive_connections=pool_config.max_keepalive_connections,
                keepalive_expiry=pool_config.keepalive_expiry_seconds,
            ),
            # Retries are intentionally disabled here; provider adapters own
            # retry policy so behavior can differ by provider/error type.
            retries=0,
        )
        self._rest_client: httpx.AsyncClient | None = httpx.AsyncClient(
            transport=shared_transport,
            timeout=httpx.Timeout(
                pool_config.read_timeout_seconds,
                connect=pool_config.connect_timeout_seconds,
                read=pool_config.read_timeout_seconds,
                write=pool_config.write_timeout_seconds,
                pool=pool_config.pool_timeout_seconds,
            ),
        )
        logger.info(
            "HTTP transport pool created: max_connections=%d, keepalive=%d",
            pool_config.max_connections,
            pool_config.max_keepalive_connections,
        )

    def create_transport(self, provider_type: ProviderType) -> httpx.AsyncClient | object:
        """Return an appropriate client/session for a provider integration style.

        Args:
            provider_type: ``rest_api``, ``aws_sdk``, or ``grpc``.

        Returns:
            - ``httpx.AsyncClient`` for REST providers.
            - ``aioboto3.Session`` for AWS SDK providers.

        Raises:
            ValueError: If provider_type is unsupported.
            NotImplementedError: For ``grpc`` placeholder path.
            RuntimeError: If an optional SDK required by the provider is absent.

        Rationale:
            Keeping provider-type branching in one place makes it obvious which
            transport stack each integration uses and simplifies future updates.
        """
        from app.core.settings.models.provider_config import ProviderType

        if self._rest_client is None:
            raise RuntimeError("ProviderTransportFactory is closed and cannot create transports.")

        if provider_type is ProviderType.REST_API:
            return self._create_rest_client()
        if provider_type is ProviderType.AWS_SDK:
            return self._create_aws_session()
        if provider_type is ProviderType.GRPC:
            return self._create_grpc_stub()
        raise ValueError(
            f"Unsupported provider_type: {provider_type!r}. "
            "Expected one of: rest_api, aws_sdk, grpc."
        )

    async def aclose(self) -> None:
        """Close the shared REST client exactly once and reject later reuse.

        The reference is cleared *before* awaiting I/O. A request racing with
        shutdown therefore fails immediately instead of borrowing a client
        whose pool is halfway through closing.
        """
        rest_client = self._rest_client
        self._rest_client = None
        if rest_client is None:
            return
        await rest_client.aclose()
        logger.info("Provider HTTP client and connection pool closed")

    def _create_rest_client(self) -> httpx.AsyncClient:
        """Return the factory-owned REST client shared by all REST providers."""
        rest_client = self._rest_client
        if rest_client is None:
            # ``create_transport`` already guards this. Keeping the check here
            # makes this helper safe if a future method calls it directly.
            raise RuntimeError("ProviderTransportFactory is closed.")
        return rest_client

    def _create_aws_session(self) -> object:
        """Create an ``aioboto3.Session`` for AWS SDK-based providers.

        Raises an actionable error immediately when ``aioboto3`` is missing,
        preventing an invalid provider instance from entering the registry.
        """
        try:
            import aioboto3
        except ImportError:
            raise RuntimeError(
                "AWS SDK provider requested but aioboto3 is not installed. "
                "Install the AWS provider dependencies before enabling Bedrock."
            ) from None

        logger.debug("aioboto3 session created for Bedrock provider.")
        return aioboto3.Session()

    @staticmethod
    def _create_grpc_stub() -> object:
        """Placeholder for future gRPC provider transport support."""
        raise NotImplementedError("gRPC provider transport is not yet implemented.")
