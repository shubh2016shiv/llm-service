"""Token-manager HTTP client and reservation contract.

Architecture:
-------------
    InferenceService -> TokenManagerClient -> llm_token_manager HTTP API

The client reserves capacity for the route already selected by ``llm_services``.
It never accepts a different endpoint from the token manager: doing so would
account for one deployment while executing against another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

import httpx
from jose import jwt

from app.core.exceptions import (
    LLMServiceError,
    ProviderInternalError,
    ProviderUnavailableError,
    QuotaExceededError,
)
from app.schemas.requests_schema import ChatRequest, EmbedRequest, RerankRequest

if TYPE_CHECKING:
    from app.inference_routing.models import ResolvedRoute

logger = logging.getLogger(__name__)

type InferenceRequest = ChatRequest | EmbedRequest | RerankRequest
type FinalizationStatus = Literal["completed", "failed", "cancelled", "disconnected"]


class TokenReservationRejectedError(QuotaExceededError):
    """Token manager refused or queued a capacity reservation."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        LLMServiceError.__init__(self, message)
        self.retry_after_seconds = retry_after_seconds


class TokenManagerUnavailableError(ProviderUnavailableError):
    """Token manager could not be reached or returned a transient failure."""

    def __init__(self, message: str) -> None:
        super().__init__(message, provider_name="token_manager")


class TokenManagerProtocolError(ProviderInternalError):
    """Token manager returned a response that violates the integration contract."""

    def __init__(self, message: str) -> None:
        super().__init__(message, provider_name="token_manager")


@dataclass(frozen=True, slots=True)
class TokenReservation:
    """Capacity reservation returned by the token manager."""

    reservation_id: str
    request_id: str
    user_id: UUID
    tenant_id: UUID
    token_count: int
    api_endpoint_url: str
    expires_at: datetime | None


class TokenManagerClient:
    """Reserve and release LLM capacity through the token-manager service."""

    def __init__(
        self,
        *,
        base_url: str,
        service_id: str,
        jwt_secret_key: str,
        jwt_algorithm: str,
        timeout: httpx.Timeout,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize one process-scoped token-manager client."""
        self._service_id = service_id
        self._jwt_secret_key = jwt_secret_key
        self._jwt_algorithm = jwt_algorithm
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
        )

    async def acquire_reservation(
        self,
        *,
        user_id: UUID,
        context: ResolvedRoute,
        request: InferenceRequest,
        request_id: str | None = None,
    ) -> TokenReservation:
        """Reserve capacity for the exact route selected by ``llm_services``."""
        correlation_id = request_id or str(uuid4())
        response = await self._request(
            "POST",
            "/api/v1/tokens/acquire",
            user_id=user_id,
            tenant_id=context.tenant_id,
            request_id=correlation_id,
            json={
                "llm_provider": context.provider_name,
                "model_name": context.model_name,
                "input_data": _token_estimation_input(request),
                "requested_completion_tokens": (
                    context.effective_max_tokens
                    if isinstance(request, ChatRequest)
                    else 0
                ),
                "deployment_name": context.deployment_key,
                "request_context": {
                    "request_id": correlation_id,
                    "thread_id": (
                        str(request.thread_id) if isinstance(request, ChatRequest) else None
                    ),
                    "route_fingerprint": context.route_fingerprint,
                    "quota_key": context.quota_key,
                    "operation": _operation_name(request),
                },
            },
        )
        retry_after_seconds = _retry_after_seconds(response)
        if response.status_code in {400, 409, 429}:
            raise TokenReservationRejectedError(
                _safe_error_detail(response, "Token capacity reservation was rejected."),
                retry_after_seconds=retry_after_seconds,
            )
        _raise_for_service_failure(response)
        payload = _response_object(response)
        if str(payload.get("allocation_status", "")).upper() != "ACQUIRED":
            raise TokenReservationRejectedError(
                "Token capacity is currently unavailable; reservation remains waiting.",
                retry_after_seconds=retry_after_seconds,
            )
        endpoint = _required_string(payload, "api_endpoint_url")
        if endpoint.rstrip("/") != context.api_endpoint_url.rstrip("/"):
            raise TokenManagerProtocolError(
                "Token manager reserved a different deployment endpoint than the authorized route."
            )
        return TokenReservation(
            reservation_id=_required_string(payload, "token_request_id"),
            request_id=correlation_id,
            user_id=user_id,
            tenant_id=context.tenant_id,
            token_count=_required_integer(payload, "token_count"),
            api_endpoint_url=endpoint,
            expires_at=_optional_datetime(payload.get("expires_at")),
        )

    async def finalize_reservation(
        self,
        reservation: TokenReservation,
        *,
        status: FinalizationStatus,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> None:
        """Release reserved capacity and report the terminal request outcome."""
        response = await self._request(
            "PUT",
            "/api/v1/tokens/release",
            request_id=reservation.request_id,
            user_id=reservation.user_id,
            tenant_id=reservation.tenant_id,
            json={
                "token_request_id": reservation.reservation_id,
                "completion_status": status,
                "actual_prompt_tokens": prompt_tokens,
                "actual_completion_tokens": completion_tokens,
            },
        )
        if response.status_code == 404:
            logger.info(
                "Token reservation already finalized",
                extra={"reservation_id": reservation.reservation_id},
            )
            return
        _raise_for_service_failure(response)

    async def aclose(self) -> None:
        """Close the owned HTTP connection pool."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        request_id: str,
        json: dict[str, object],
        user_id: UUID | None = None,
        tenant_id: UUID | None = None,
    ) -> httpx.Response:
        """Send one authenticated request and normalize transport failures."""
        token = self._build_service_token(user_id=user_id, tenant_id=tenant_id)
        try:
            return await self._http_client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-ID": request_id,
                    "X-Service-ID": self._service_id,
                },
                json=json,
            )
        except httpx.TimeoutException as exc:
            raise TokenManagerUnavailableError("Token manager request timed out.") from exc
        except httpx.RequestError as exc:
            raise TokenManagerUnavailableError("Token manager is unreachable.") from exc

    def _build_service_token(
        self,
        *,
        user_id: UUID | None,
        tenant_id: UUID | None,
    ) -> str:
        """Mint a short-lived internal token accepted by the token manager."""
        now = datetime.now(UTC)
        claims: dict[str, object] = {
            "user_id": str(user_id or UUID(int=0)),
            "tenant_id": str(tenant_id or UUID(int=0)),
            "role": "developer",
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=2),
        }
        return jwt.encode(claims, self._jwt_secret_key, algorithm=self._jwt_algorithm)


def _token_estimation_input(request: InferenceRequest) -> str | list[dict[str, object]]:
    """Translate operation-specific input into the token-manager estimator contract."""
    if isinstance(request, ChatRequest):
        return [message.model_dump(exclude_none=True) for message in request.messages]
    if isinstance(request, EmbedRequest):
        return request.input if isinstance(request.input, str) else "\n".join(request.input)
    return "\n".join([request.query, *request.documents])


def _operation_name(request: InferenceRequest) -> str:
    """Return the stable operation name for reservation audit context."""
    if isinstance(request, ChatRequest):
        return "chat"
    if isinstance(request, EmbedRequest):
        return "embed"
    return "rerank"


def _raise_for_service_failure(response: httpx.Response) -> None:
    """Translate non-success token-manager responses to typed domain errors."""
    if response.status_code in {401, 403}:
        raise TokenManagerProtocolError("Token manager rejected service authentication.")
    if response.status_code >= 500:
        raise TokenManagerUnavailableError(
            _safe_error_detail(response, "Token manager is temporarily unavailable.")
        )
    if response.is_error:
        raise TokenManagerProtocolError(
            _safe_error_detail(response, "Token manager rejected the integration request.")
        )


def _response_object(response: httpx.Response) -> dict[str, object]:
    """Decode a JSON object response or raise a protocol error."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise TokenManagerProtocolError("Token manager returned invalid JSON.") from exc
    if not isinstance(payload, dict):
        raise TokenManagerProtocolError("Token manager response must be a JSON object.")
    return {str(key): value for key, value in payload.items()}


def _safe_error_detail(response: httpx.Response, fallback: str) -> str:
    """Read a public error detail without exposing response internals."""
    try:
        payload = response.json()
    except ValueError:
        return fallback
    if isinstance(payload, dict) and isinstance(payload.get("detail"), str):
        return payload["detail"]
    return fallback


def _required_string(payload: dict[str, object], field_name: str) -> str:
    """Read one required non-empty string from a response object."""
    value = payload.get(field_name)
    if not isinstance(value, str) or not value:
        raise TokenManagerProtocolError(f"Token manager response omitted {field_name!r}.")
    return value


def _required_integer(payload: dict[str, object], field_name: str) -> int:
    """Read one required integer from a response object."""
    value = payload.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TokenManagerProtocolError(f"Token manager response omitted {field_name!r}.")
    return value


def _optional_datetime(value: object) -> datetime | None:
    """Parse an optional ISO-8601 timestamp from a response."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TokenManagerProtocolError("Token manager returned an invalid 'expires_at'.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TokenManagerProtocolError("Token manager returned an invalid 'expires_at'.") from exc


def _retry_after_seconds(response: httpx.Response) -> int | None:
    """Parse a positive Retry-After delta when supplied by the token manager."""
    raw_value = response.headers.get("Retry-After")
    if raw_value is None:
        return None
    try:
        parsed = int(raw_value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
