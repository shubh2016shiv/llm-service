"""
Inference Service
=================

Pure executor for provider calls. This service receives a fully resolved
execution context (provider, model, credential reference, endpoint URL, and
final request parameters) and performs the actual inference call.

Design principle - what this service does NOT know:
    This service intentionally knows nothing about routing strategy, tenant
    lookup, credential resolution, or authorization decisions. That work is
    completed earlier by ``OrchestrationPipeline`` in ``app/inference_routing``.
    Keeping this boundary strict means routing can evolve independently while
    this service stays a stable execution component.

Execution flow for each request:
    1. Check quota with the Token Manager (is the tenant allowed to make
       this call right now?).
    2. Look up the provider from the ProviderRegistry using the resolved
       context.
    3. Call the provider's method (generate, embed, rerank, or
       stream_generate) with the original request.
    4. Report actual token usage back to the Token Manager (reconciliation).
    5. Return the typed response to the caller.

Enterprise Pattern: Pure Executor Pattern
    The service contains no business rules - it only orchestrates the
    provider call. Authorization, routing, and parameter resolution happened
    upstream. The service is the final "do it" step.

Author: Shubham Singh
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.clients.token_manager_client import TokenManagerClient
    from app.inference_routing.models import ResolvedExecutionContext
    from app.providers.registry import ProviderRegistry
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        RerankResponse,
    )

logger = logging.getLogger(__name__)


class InferenceService:
    """Execute an inference request against a provider using a pre-resolved context.

    This service is the final step in the request pipeline. It does not
    make routing decisions or authorization checks; it assumes the caller
    has already resolved a valid ``ResolvedExecutionContext`` and now
    needs the provider call to execute.

    Dependencies (both injected at construction and reused across requests):
        - ``TokenManagerClient`` - checks and reports quota usage.
        - ``ProviderRegistry`` - locates the provider adapter for the
          resolved context (OpenAI, Anthropic, Bedrock, and others).
    """

    def __init__(
        self,
        token_manager_client: TokenManagerClient,
        provider_registry: ProviderRegistry,
    ) -> None:
        self._token_manager = token_manager_client
        self._registry = provider_registry

    async def execute_chat(
        self,
        context: ResolvedExecutionContext,
        request: ChatRequest,
    ) -> ChatResponse:
        """Run a non-streaming chat completion against the resolved provider.

        Steps:
            1. Check quota - asks the Token Manager whether this tenant has
               enough remaining allowance for this request.
            2. Resolve provider adapter - uses the pre-resolved context to
               select the concrete provider implementation.
            3. Execute generation - calls ``provider.generate(request)``.
            4. Reconcile usage - reports provider-returned token usage so
               quota accounting reflects actual consumption.
            5. Return the typed ``ChatResponse``.

        "Usage reconciliation" means updating quota with real token counts
        rather than only pre-call estimates.
        """
        await self._token_manager.check_quota(
            context.tenant_config.tenant_id, context.quota_key, request
        )
        provider = await self._registry.get_provider(context)
        response = await provider.generate(request)
        if response.usage:
            await self._token_manager.report_usage(
                context.tenant_config.tenant_id,
                context.quota_key,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )
        return response

    async def execute_stream_chat(
        self,
        context: ResolvedExecutionContext,
        request: ChatRequest,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Run a streaming chat completion and yield chunks as they arrive.

        Quota is checked once before streaming starts. Unlike the
        non-streaming path, this method does not perform post-stream usage
        reporting. The caller is responsible for aggregating usage metadata
        from streamed chunks (if available) and reporting it separately.

        This keeps stream delivery low-latency and avoids delaying chunk
        forwarding for bookkeeping.
        """
        await self._token_manager.check_quota(
            context.tenant_config.tenant_id, context.quota_key, request
        )
        provider = await self._registry.get_provider(context)
        async for chunk in provider.stream_generate(request):
            yield chunk

    async def execute_embed(
        self,
        context: ResolvedExecutionContext,
        request: EmbedRequest,
    ) -> EmbedResponse:
        """Run an embedding request against the resolved provider.

        Uses the same flow as ``execute_chat``: quota check, provider lookup,
        execute, then usage reporting. Embedding operations consume input
        tokens but do not generate completion text, so reported completion
        token count is always zero.
        """
        await self._token_manager.check_quota(
            context.tenant_config.tenant_id, context.quota_key, request
        )
        provider = await self._registry.get_provider(context)
        response = await provider.embed(request)
        if response.usage:
            await self._token_manager.report_usage(
                context.tenant_config.tenant_id,
                context.quota_key,
                response.usage.prompt_tokens,
                0,
            )
        return response

    async def execute_rerank(
        self,
        context: ResolvedExecutionContext,
        request: RerankRequest,
    ) -> RerankResponse:
        """Run a re-ranking request against the resolved provider.

        Quota is validated before the call. Usage is not reported afterward
        because many re-ranking providers do not return reliable token usage
        metadata, and the operation is relevance scoring rather than text
        generation.
        """
        await self._token_manager.check_quota(
            context.tenant_config.tenant_id, context.quota_key, request
        )
        provider = await self._registry.get_provider(context)
        return await provider.rerank(request)
