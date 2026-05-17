"""
Application entry point.

Startup sequence (lifespan handler):
    1. Load ApplicationSettings from environment / .env
    2. Load GlobalConfig from YAML (config_dir)
    3. Connect RedisCache
    4. Build HTTPClientFactory  (shared TCP/TLS connection pool)
    5. Build ConfigLoader       (YAML-backed provider/model metadata)
    6. Build ProviderRegistry   (singleton cache of provider instances)
    7. Build DeploymentResolver (reads tenant deployment config from Redis)
    8. Build TokenManagerClient (quota check / usage reporting)
    9. Build InferenceService   (orchestrates steps 7-8 per request)
   10. Store InferenceService on app.state (survives across requests)

Shutdown sequence (lifespan handler, reversed):
    - Close RedisCache connection pool
    - (HTTPClientFactory transport is GC'd; httpx handles cleanup)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from fastapi import FastAPI

from app.adapters.clients.token_manager_client import TokenManagerClient
from app.api.llm_inference_endpoints import router as llm_inference_router
from app.core.secret_store import EnvironmentSecretStore, SecretStore, VaultSecretStore
from app.core.settings.loader import ConfigLoader
from app.core.settings.settings import get_application_settings
from app.execution.inference_service import InferenceService
from app.infrastructure.cache import RedisCache
from app.infrastructure.http_client_factory import HTTPClientFactory
from app.providers.registry import ProviderRegistry
from app.routing.deployment_resolver import DeploymentResolver

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Wire all application-scoped dependencies and store them on app.state."""
    settings = get_application_settings()

    config_loader = ConfigLoader(
        config_dir=Path(settings.config_dir),
        environment=settings.app_environment,
    )
    global_config = config_loader.load_global_config()

    redis_cache = RedisCache(redis_url=settings.redis_url)
    await redis_cache.connect()

    http_client_factory = HTTPClientFactory(pool_config=global_config.http_pool)

    secret_store: SecretStore
    if settings.secret_backend == "vault":
        if settings.vault_username is None or settings.vault_password is None:
            raise RuntimeError(
                "secret_backend=vault requires VAULT_USERNAME and VAULT_PASSWORD to be set."
            )
        secret_store = VaultSecretStore(
            vault_addr=settings.vault_addr,
            username=settings.vault_username,
            password=settings.vault_password.get_secret_value(),
            mount_path=settings.vault_mount_path,
            kv_prefix=settings.vault_kv_prefix,
        )
        logger.info("Secret backend: Vault at %s", settings.vault_addr)
    else:
        secret_store = EnvironmentSecretStore()
        logger.info("Secret backend: environment variables")

    provider_registry = ProviderRegistry(
        http_client_factory=http_client_factory,
        config_loader=config_loader,
        cache=redis_cache,
        secret_store=secret_store,
    )

    deployment_resolver = DeploymentResolver(cache=redis_cache)
    token_manager_client = TokenManagerClient()

    app.state.inference_service = InferenceService(
        deployment_resolver=deployment_resolver,
        token_manager_client=token_manager_client,
        provider_registry=provider_registry,
    )

    logger.info("Application startup complete | environment=%s", settings.app_environment)

    yield

    await secret_store.aclose()
    await redis_cache.disconnect()
    logger.info("Application shutdown complete")


app = FastAPI(
    title="LLM Provider Service",
    description=(
        "Multi-tenant LLM provider abstraction layer. "
        "Routes inference requests to the correct provider based on tenant deployment config."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(llm_inference_router)

# NOTE: the routers below depend on app/auth.py which is not yet implemented.
# Uncomment each line once auth is in place.
# from app.api.user_endpoints import router as user_router
# from app.api.user_entitlement_endpoints import router as entitlement_router
# from app.api.llm_configuration_endpoints import router as llm_config_router
# app.include_router(user_router)
# app.include_router(entitlement_router)
# app.include_router(llm_config_router)


@app.get("/health", tags=["Health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
