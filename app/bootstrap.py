"""Build process-owned adapters and register their shutdown before using them.

Architecture:
    main.lifespan -> configure_runtime -> adapters -> AsyncExitStack

The exit stack is deliberately passed through the builders: as soon as a pool
exists, its closer is registered. If the *next* dependency fails, the stack
still closes every pool already created, in reverse creation order.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from app.adapters.cache import RedisCache, RedisConnectionManager
from app.adapters.inference_routing import PostgresInferenceRoutingConfigReader
from app.adapters.postgresql import PostgresSessionProvider
from app.adapters.provider_transport import ProviderCircuitBreakerRegistry, ProviderTransportFactory
from app.adapters.secret_management import (
    AesGcmSecretStore,
    EnvironmentSecretStore,
    SecretStore,
    VaultClientOptions,
    VaultSecretStore,
    VaultSecretWriter,
)
from app.clients.token_manager_client import TokenManagerClient
from app.core.settings.loader import ConfigLoader
from app.database import TenantPersistence, UserEntitlementPersistence
from app.inference_routing import InferenceRouteResolver
from app.providers.registry import ProviderRegistry
from app.services import InferenceService
from app.streaming.stream_capacity import WorkerStreamCapacityLimiter

if TYPE_CHECKING:
    from contextlib import AsyncExitStack

    from fastapi import FastAPI

    from app.core.settings.models import GlobalConfig
    from app.core.settings.settings import ApplicationSettings

logger = logging.getLogger(__name__)


async def configure_runtime(
    app: FastAPI, settings: ApplicationSettings, stack: AsyncExitStack
) -> None:
    """Create the runtime graph, registering every owned resource immediately."""
    config_loader, global_config = _load_provider_config(settings)
    app.state.config_loader = config_loader
    postgres = PostgresSessionProvider(settings)
    stack.push_async_callback(postgres.close)
    app.state.postgres_session_provider = postgres
    redis = await _build_redis(settings, stack)
    app.state.redis_connection = redis
    app.state.redis_cache = RedisCache(redis)
    transport = ProviderTransportFactory(pool_config=global_config.http_pool)
    stack.push_async_callback(transport.aclose)
    secret_store, credential_writer = _build_secret_backend(settings, stack)
    app.state.credential_writer = credential_writer
    token_manager = _build_token_manager(settings, stack)
    _wire_inference(
        app,
        settings,
        config_loader,
        global_config,
        postgres,
        transport,
        secret_store,
        token_manager,
        stack,
    )
    app.state.stream_heartbeat_interval_seconds = settings.stream_heartbeat_interval_seconds


def _load_provider_config(settings: ApplicationSettings) -> tuple[ConfigLoader, GlobalConfig]:
    """Validate all provider YAML before opening any connection pool."""
    loader = ConfigLoader(config_dir=settings.config_dir, environment=settings.app_environment)
    global_config = loader.load_global_config()
    loader.load_all_provider_configs()
    loader.load_all_cloud_configs()
    if settings.stream_max_concurrent_per_worker > global_config.http_pool.max_connections:
        raise RuntimeError("STREAM_MAX_CONCURRENT_PER_WORKER exceeds provider HTTP pool capacity")
    return loader, global_config


async def _build_redis(
    settings: ApplicationSettings, stack: AsyncExitStack
) -> RedisConnectionManager:
    """Register Redis cleanup before the first connection attempt."""
    redis = RedisConnectionManager(
        settings.redis_url,
        initial_retry_delay_seconds=settings.redis_initial_retry_delay_seconds,
        max_retry_delay_seconds=settings.redis_max_retry_delay_seconds,
        socket_connect_timeout_seconds=settings.redis_socket_connect_timeout_seconds,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        health_check_interval_seconds=settings.redis_health_check_interval_seconds,
    )
    stack.push_async_callback(redis.close)
    await redis.connect()
    return redis


def _vault_options(settings: ApplicationSettings) -> VaultClientOptions:
    """Apply the same bounded retry and timeout policy to both Vault identities."""
    return VaultClientOptions(
        connect_timeout_seconds=settings.vault_connect_timeout_seconds,
        read_timeout_seconds=settings.vault_read_timeout_seconds,
        write_timeout_seconds=settings.vault_write_timeout_seconds,
        pool_timeout_seconds=settings.vault_pool_timeout_seconds,
        request_max_attempts=settings.vault_request_max_attempts,
        retry_base_delay_seconds=settings.vault_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.vault_retry_max_delay_seconds,
    )


def _build_secret_backend(
    settings: ApplicationSettings, stack: AsyncExitStack
) -> tuple[SecretStore, VaultSecretWriter | None]:
    """Keep Vault read/write identities separate and register each owned client."""
    if settings.secret_backend == "vault":
        return _build_vault_backend(settings, stack)
    if settings.secret_backend == "aes_gcm":
        store = AesGcmSecretStore(master_key_b64=settings.encryption_master_key.get_secret_value())
        logger.info("Secret backend configured", extra={"backend": "aes_gcm"})
        return store, None
    logger.info("Secret backend configured", extra={"backend": "environment"})
    return EnvironmentSecretStore(), None


def _build_vault_backend(
    settings: ApplicationSettings, stack: AsyncExitStack
) -> tuple[VaultSecretStore, VaultSecretWriter | None]:
    """Fail early for a missing read identity, and own both client lifetimes."""
    if settings.vault_username is None or settings.vault_password is None:
        raise RuntimeError("secret_backend=vault requires VAULT_USERNAME and VAULT_PASSWORD")
    options = _vault_options(settings)
    store = VaultSecretStore(
        vault_addr=settings.vault_addr,
        username=settings.vault_username,
        password=settings.vault_password.get_secret_value(),
        mount_path=settings.vault_mount_path,
        kv_prefix=settings.vault_kv_prefix,
        token_refresh_after_lease_fraction=settings.vault_token_refresh_after_lease_fraction,
        client_options=options,
    )
    stack.push_async_callback(store.aclose)
    writer = None
    if settings.vault_admin_username is not None and settings.vault_admin_password is not None:
        writer = VaultSecretWriter(
            vault_addr=settings.vault_addr,
            username=settings.vault_admin_username,
            password=settings.vault_admin_password.get_secret_value(),
            mount_path=settings.vault_mount_path,
            kv_prefix=settings.vault_kv_prefix,
            token_refresh_after_lease_fraction=settings.vault_token_refresh_after_lease_fraction,
            client_options=options,
        )
        stack.push_async_callback(writer.aclose)
    logger.info("Secret backend configured", extra={"backend": "vault"})
    return store, writer


def _build_token_manager(
    settings: ApplicationSettings, stack: AsyncExitStack
) -> TokenManagerClient:
    """Own one shared outbound HTTP client for reservations and usage reports."""
    client = TokenManagerClient(
        base_url=settings.token_manager_base_url,
        service_id=settings.token_manager_service_id,
        jwt_secret_key=settings.jwt_secret_key.get_secret_value(),
        jwt_algorithm=settings.jwt_algorithm,
        timeout=httpx.Timeout(
            connect=settings.token_manager_connect_timeout_seconds,
            read=settings.token_manager_read_timeout_seconds,
            write=settings.token_manager_write_timeout_seconds,
            pool=settings.token_manager_pool_timeout_seconds,
        ),
    )
    stack.push_async_callback(client.aclose)
    return client


def _wire_inference(
    app: FastAPI,
    settings: ApplicationSettings,
    loader: ConfigLoader,
    global_config: GlobalConfig,
    postgres: PostgresSessionProvider,
    transport: ProviderTransportFactory,
    store: SecretStore,
    token_manager: TokenManagerClient,
    stack: AsyncExitStack,
) -> None:
    """Compose request services from process-owned adapters without new pools."""
    reader = PostgresInferenceRoutingConfigReader(
        tenant_persistence=TenantPersistence(postgres),
        entitlement_persistence=UserEntitlementPersistence(postgres),
    )
    app.state.routing_config_reader = reader
    app.state.inference_route_resolver = InferenceRouteResolver(
        routing_config_reader=reader,
        config_loader=loader,
    )
    registry = ProviderRegistry(
        transport_factory=transport,
        circuit_breaker_registry=ProviderCircuitBreakerRegistry(
            global_config.provider_circuit_breakers
        ),
        secret_store=store,
        cache_ttl_seconds=settings.provider_cache_ttl_seconds,
        max_cached_providers=settings.provider_cache_max_entries,
    )
    # Cached providers retain plaintext credentials; clear them before closing
    # the transports and secret store registered earlier on the exit stack.
    stack.push_async_callback(registry.clear)
    app.state.inference_service = InferenceService(
        token_manager_client=token_manager,
        provider_registry=registry,
        stream_admission=WorkerStreamCapacityLimiter(
            max_concurrent=settings.stream_max_concurrent_per_worker,
            retry_after_seconds=settings.stream_capacity_retry_after_seconds,
        ),
        stream_cleanup_timeout_seconds=settings.stream_cleanup_timeout_seconds,
    )
