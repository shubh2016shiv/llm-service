"""
Inference Service
=================

Application service that executes resolved LLM requests against a provider.

Architecture:
-------------
    FastAPI Router / Worker
        │
        ▼
    InferenceService.execute_chat(...)
        │
        ├── 1. DeploymentResolver.resolve(tenant_id, deployment_key)
        ├── 2. TokenManagerClient.check_quota(...)
        ├── 3. ProviderRegistry.get_provider(tenant_id, deployment_id)
        ├── 4. provider.generate(request)
        └── 5. TokenManagerClient.report_usage(...)
        │
        └── returns ChatResponse
Dependencies:
    - app.routing.deployment_resolver — deployment lookup
    - app.adapters.clients.token_manager_client — token allocation client
    - app.providers.registry — provider instance cache

Author: Engineering Team
Last Updated: 2026-05-16
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from app.adapters.clients.token_manager_client import TokenManagerClient
    from app.providers.registry import ProviderRegistry
    from app.routing.deployment_resolver import DeploymentResolver
    from app.schemas.requests import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses import ChatResponse, ChatStreamChunk, EmbedResponse, RerankResponse

logger = logging.getLogger(__name__)


class InferenceService:
    """Orchestrates the resolution, quota check, and provider execution."""

    def __init__(
        self,
        deployment_resolver: DeploymentResolver,
        token_manager_client: TokenManagerClient,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._resolver = deployment_resolver
        self._token_manager = token_manager_client
        self._registry = provider_registry

    async def execute_chat(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        request: ChatRequest,
    ) -> ChatResponse:
        """Execute a non-streaming chat completion request.
        
        Args:
            tenant_id: The UUID of the requesting tenant.
            deployment_key: The ID of the deployment to route to.
            request: The chat completion request payload.
            
        Returns:
            A normalised ChatResponse.
        """
        # 1. Resolve Configuration
        config = await self._resolver.resolve(tenant_id, deployment_key)
        
        # 2. Check Quotas
        await self._token_manager.check_quota(tenant_id, deployment_key, request)
        
        # 3. Get Provider
        provider = await self._registry.get_provider(config)
        
        # 4. Execute Inference
        response = await provider.generate(request)
        
        # 5. Report Usage
        if response.usage:
            await self._token_manager.report_usage(
                tenant_id=tenant_id,
                deployment_key=deployment_key,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
            
        return response

    async def execute_stream_chat(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Execute a streaming chat completion request.
        
        Usage reporting happens after the stream closes, which typically requires
        the API route to track the yielded tokens and report them, or the orchestrator
        to wrap the generator and accumulate token counts.
        """
        config = await self._resolver.resolve(tenant_id, deployment_key)
        await self._token_manager.check_quota(tenant_id, deployment_key, request)
        
        provider = await self._registry.get_provider(config)
        
        # We yield chunks as they come in. For precise usage reporting in streaming,
        # we'd accumulate the chunks. For now, we omit usage reporting in stream
        # since it's highly dependent on the provider's token counting method.
        async for chunk in provider.stream_generate(request):
            yield chunk

    async def execute_embed(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        request: EmbedRequest,
    ) -> EmbedResponse:
        """Execute an embedding request."""
        config = await self._resolver.resolve(tenant_id, deployment_key)
        await self._token_manager.check_quota(tenant_id, deployment_key, request)
        
        provider = await self._registry.get_provider(config)
        response = await provider.embed(request)
        
        if response.usage:
            await self._token_manager.report_usage(
                tenant_id=tenant_id,
                deployment_key=deployment_key,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=0,
            )
            
        return response

    async def execute_rerank(
        self,
        tenant_id: UUID | str,
        deployment_key: str,
        request: RerankRequest,
    ) -> RerankResponse:
        """Execute a rerank request."""
        config = await self._resolver.resolve(tenant_id, deployment_key)
        await self._token_manager.check_quota(tenant_id, deployment_key, request)
        
        provider = await self._registry.get_provider(config)
        return await provider.rerank(request)
