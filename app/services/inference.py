"""
Inference Service
=================

Pure executor for provider calls. This service receives a fully resolved
execution context (provider, model, credential reference, endpoint URL, and
final request parameters) and performs the actual inference call.

Design principle - what this service does NOT know:
    This service intentionally knows nothing about routing strategy, tenant
    lookup, credential resolution, or authorization decisions. That work is
    completed earlier by ``InferenceRouteResolver`` in ``app/inference_routing``.
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

import asyncio
import logging
from typing import TYPE_CHECKING

from app.services.stream_session import StreamingInferenceSession

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from app.clients.token_manager_client import (
        FinalizationStatus,
        TokenManagerClient,
        TokenReservation,
    )
    from app.inference_routing.models import ResolvedRoute
    from app.providers.registry import ProviderRegistry
    from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest
    from app.schemas.responses_schema import (
        ChatResponse,
        ChatStreamChunk,
        EmbedResponse,
        RerankResponse,
    )
    from app.streaming.admission import StreamAdmissionController

logger = logging.getLogger(__name__)


class InferenceService:
    """Execute an inference request against a provider using a pre-resolved context.

    This service is the final step in the request pipeline. It does not
    make routing decisions or authorization checks; it assumes the caller
    has already resolved a valid ``ResolvedRoute`` and now
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
        stream_admission: StreamAdmissionController,
        stream_cleanup_timeout_seconds: float = 5.0,
    ) -> None:
        if stream_cleanup_timeout_seconds <= 0:
            raise ValueError("stream_cleanup_timeout_seconds must be positive")
        self._token_manager = token_manager_client
        self._registry = provider_registry
        self._stream_admission = stream_admission
        self._stream_cleanup_timeout_seconds = stream_cleanup_timeout_seconds

    async def execute_chat(
        self,
        context: ResolvedRoute,
        request: ChatRequest,
        *,
        user_id: UUID,
        request_id: str | None = None,
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
        reservation = await self._token_manager.acquire_reservation(
            user_id=user_id,
            context=context,
            request=request,
            request_id=request_id,
        )
        try:
            provider = await self._registry.get_provider(context)
            response = await provider.generate(request)
        except asyncio.CancelledError:
            await self._finalize_preserving_original(reservation, status="cancelled")
            raise
        except Exception:
            await self._finalize_preserving_original(reservation, status="failed")
            raise
        usage = response.usage
        await self._finalize(
            reservation,
            status="completed",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )
        return response

    async def prepare_stream_chat(
        self,
        context: ResolvedRoute,
        request: ChatRequest,
        *,
        user_id: UUID,
        request_id: str | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        """Acquire capacity and return an exact-once managed provider stream.

        Preparation is eager so acquisition and provider-construction failures
        are translated to an HTTP error before SSE response headers are sent.
        """
        lease = await self._stream_admission.acquire()
        try:
            reservation = await self._token_manager.acquire_reservation(
                user_id=user_id,
                context=context,
                request=request,
                request_id=request_id,
            )
        except BaseException:
            await lease.release()
            raise
        try:
            provider = await self._registry.get_provider(context)
            provider_chunks = provider.stream_generate(request)
        except asyncio.CancelledError:
            await self._finalize_preserving_original(reservation, status="cancelled")
            await lease.release()
            raise
        except Exception:
            await self._finalize_preserving_original(reservation, status="failed")
            await lease.release()
            raise
        return StreamingInferenceSession(
            provider_chunks=provider_chunks,
            lease=lease,
            cleanup_timeout_seconds=self._stream_cleanup_timeout_seconds,
            finalize=lambda status, prompt_tokens, completion_tokens: (
                self._finalize_preserving_original(
                    reservation,
                    status=status,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            ),
        )

    async def execute_embed(
        self,
        context: ResolvedRoute,
        request: EmbedRequest,
        *,
        user_id: UUID,
    ) -> EmbedResponse:
        """Run an embedding request against the resolved provider.

        Uses the same flow as ``execute_chat``: quota check, provider lookup,
        execute, then usage reporting. Embedding operations consume input
        tokens but do not generate completion text, so reported completion
        token count is always zero.
        """
        reservation = await self._token_manager.acquire_reservation(
            user_id=user_id,
            context=context,
            request=request,
        )
        try:
            provider = await self._registry.get_provider(context)
            response = await provider.embed(request)
        except asyncio.CancelledError:
            await self._finalize_preserving_original(reservation, status="cancelled")
            raise
        except Exception:
            await self._finalize_preserving_original(reservation, status="failed")
            raise
        usage = response.usage
        await self._finalize(
            reservation,
            status="completed",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=0 if usage else None,
        )
        return response

    async def execute_rerank(
        self,
        context: ResolvedRoute,
        request: RerankRequest,
        *,
        user_id: UUID,
    ) -> RerankResponse:
        """Run a re-ranking request against the resolved provider.

        Quota is validated before the call. Usage is not reported afterward
        because many re-ranking providers do not return reliable token usage
        metadata, and the operation is relevance scoring rather than text
        generation.
        """
        reservation = await self._token_manager.acquire_reservation(
            user_id=user_id,
            context=context,
            request=request,
        )
        try:
            provider = await self._registry.get_provider(context)
            response = await provider.rerank(request)
        except asyncio.CancelledError:
            await self._finalize_preserving_original(reservation, status="cancelled")
            raise
        except Exception:
            await self._finalize_preserving_original(reservation, status="failed")
            raise
        usage = response.usage
        await self._finalize(
            reservation,
            status="completed",
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
        )
        return response

    async def _finalize(
        self,
        reservation: TokenReservation,
        *,
        status: FinalizationStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Commit the terminal accounting record.

        On a successful non-streaming call this is part of the operation, not
        best-effort cleanup. Propagating an accounting failure prevents the
        API from claiming success while quota state remains uncommitted.
        """
        await self._token_manager.finalize_reservation(
            reservation,
            status=status,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    async def _finalize_preserving_original(
        self,
        reservation: TokenReservation,
        *,
        status: FinalizationStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Attempt cleanup without hiding the provider or cancellation error.

        Streaming also uses this path because headers may already be sent when
        terminal accounting happens; the failure is therefore observable in
        logs but cannot safely become a second HTTP response.
        """
        try:
            await self._finalize(
                reservation,
                status=status,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        except Exception:
            logger.error(
                "Token reservation finalization failed",
                extra={
                    "reservation_id": reservation.reservation_id,
                    "status": status,
                },
                exc_info=True,
            )
