"""
LLM Inference Endpoints — submit a prompt, get a job ID, poll for the result.

How this works (for any new developer):
----------------------------------------
1. Caller sends POST /api/v1/llm/jobs  with the prompt + provider + model.
2. This endpoint puts the job onto a background worker queue and immediately
   returns a job_id.  The caller does NOT wait for the LLM to respond here.
3. Caller polls GET /api/v1/llm/jobs/{job_id} (handled by result_store/router.py)
   until status is SUCCESS or FAILURE.

Why background workers instead of answering inline?
   LLM calls take 1–30 seconds.  Blocking an HTTP connection for that long
   kills throughput under any real load.  Workers run in parallel; the HTTP
   layer stays fast.

Priority queue:
   Standard requests go to a pool of 7 workers.
   Priority requests (header X-Priority: true) go to a separate pool of 3
   dedicated workers that are never shared with standard traffic.

Architecture:
-------------
    Caller
      │  POST /api/v1/llm/jobs
      ▼
    llm_inference_endpoints.py   ← you are here
      │  submit_llm_job() / submit_priority_llm_job()
      │  puts job on worker queue → returns job_id immediately (HTTP 202)
      ▼
    Celery worker (background)
      │  standard_task.process_llm_request
      │  priority_task.process_priority_llm_request
      ▼
    LLM provider (OpenAI / Anthropic / etc.)
      │  result stored in Redis under job_id
      ▼
    Caller polls GET /api/v1/llm/jobs/{job_id}

Dependencies:
    - app/llm_gateway/worker_queue.py                        — celery_app, queue name constants
    - app/llm_gateway/gateway_tasks/standard_task.py         — process_llm_request task
    - app/llm_gateway/gateway_tasks/priority_task.py         — process_priority_llm_request task

Author: LLM Gateway Team
Last Updated: 2026-05-11
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from loguru import logger
from pydantic import BaseModel, Field

from app.llm_gateway.worker_queue import (
    LLM_REQUESTS_QUEUE,
    PRIORITY_REQUESTS_QUEUE,
    celery_app,
)

router = APIRouter(prefix="/api/v1/llm", tags=["LLM Inference"])

# ---------------------------------------------------------------------------
# Task names — must match the @celery_app.task(name=...) in each task file.
# Celery routes by name; if these drift the job silently goes to the wrong
# worker (or nowhere).
# ---------------------------------------------------------------------------
_STANDARD_TASK = "app.llm_gateway.gateway_tasks.standard_task.process_llm_request"
_PRIORITY_TASK = "app.llm_gateway.gateway_tasks.priority_task.process_priority_llm_request"


# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class LLMJobRequest(BaseModel):
    """What the caller sends when submitting an LLM job.

    The caller MUST first call POST /api/v1/tokens/acquire and receive a
    token_request_id in ACQUIRED state. That ID is required here. The worker
    holds the tokens locked until the LLM call completes, then releases them
    automatically. The caller never calls /tokens/release manually.

    Fields:
        token_request_id: The allocation ID from POST /api/v1/tokens/acquire.
                          Must be in ACQUIRED state. This is the guardrail —
                          no allocation, no LLM call.
        user_id:          The requesting user's UUID (used for credential lookup).
        llm_provider:     Which provider to call: "openai", "anthropic",
                          "azure_openai", "aws_bedrock", or "gcp_vertex".
        llm_model_name:   The model identifier, e.g. "gpt-4o".
        user_prompt:      The prompt text to send to the model.
        system_message:   Optional system-level instruction.
        max_tokens:       Optional cap on completion length.
        temperature:      Optional sampling temperature (0.0 – 2.0).
    """

    token_request_id: str = Field(
        ...,
        description=(
            "Allocation ID from POST /api/v1/tokens/acquire. "
            "Tokens stay locked until the LLM call finishes. "
            "The worker releases them — do not call /tokens/release manually."
        ),
    )
    user_id: UUID = Field(..., description="Requesting user's UUID")
    llm_provider: str = Field(
        ...,
        description="Provider name: openai | anthropic | azure_openai | aws_bedrock | gcp_vertex",
    )
    llm_model_name: str = Field(
        ..., description="Model identifier (e.g. 'gpt-4o', 'claude-3-5-sonnet-20241022')"
    )
    user_prompt: str = Field(..., min_length=1, description="Prompt text to send to the model")
    system_message: str | None = Field(None, description="Optional system instruction")
    max_tokens: int | None = Field(None, gt=0, description="Optional max completion tokens")
    temperature: float | None = Field(None, ge=0.0, le=2.0, description="Optional temperature")


class LLMJobAccepted(BaseModel):
    """Returned immediately when a job is queued successfully (HTTP 202).

    The caller uses job_id to poll GET /api/v1/llm/jobs/{job_id}.

    Fields:
        job_id:     Unique identifier for this job — use it to poll for the result.
        poll_url:   Ready-to-use URL for polling.
        submitted_at: UTC timestamp of when the job was queued.
        queue:      Which worker queue received the job (informational).
    """

    job_id: str = Field(..., description="Unique job identifier — poll with this")
    poll_url: str = Field(..., description="URL to poll for the result")
    submitted_at: str = Field(..., description="UTC ISO-8601 submission time")
    queue: str = Field(..., description="Worker queue the job was sent to")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/jobs",
    response_model=LLMJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an LLM job",
    description=(
        "Queues a prompt for background LLM execution and returns a job_id immediately. "
        "The LLM call happens in a background worker — this endpoint does not wait for it. "
        "Poll GET /api/v1/llm/jobs/{job_id} for the result."
    ),
)
async def submit_llm_job(request: LLMJobRequest) -> LLMJobAccepted:
    """Put an LLM inference job onto the standard worker queue.

    Returns HTTP 202 immediately with a job_id.  Does not wait for the LLM.

    Args:
        request: Validated job request body.

    Returns:
        LLMJobAccepted with job_id, poll_url, and submission timestamp.

    Raises:
        HTTP 503: If the worker queue is unreachable.
    """
    return _enqueue_job(request, queue=LLM_REQUESTS_QUEUE, task_name=_STANDARD_TASK)


@router.post(
    "/jobs/priority",
    response_model=LLMJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a priority LLM job",
    description=(
        "Same as POST /api/v1/llm/jobs but routes to the priority worker pool. "
        "Priority workers are never shared with standard traffic, so this queue "
        "stays fast even when the standard queue is saturated. "
        "Use sparingly — there are only 3 priority workers."
    ),
)
async def submit_priority_llm_job(request: LLMJobRequest) -> LLMJobAccepted:
    """Put an LLM inference job onto the priority worker queue.

    Priority workers (3 dedicated) never pick up standard jobs.
    The caller gets the same HTTP 202 + job_id contract as the standard endpoint.

    Args:
        request: Validated job request body.

    Returns:
        LLMJobAccepted with job_id, poll_url, and submission timestamp.

    Raises:
        HTTP 503: If the worker queue is unreachable.
    """
    return _enqueue_job(request, queue=PRIORITY_REQUESTS_QUEUE, task_name=_PRIORITY_TASK)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _enqueue_job(
    request: LLMJobRequest,
    queue: str,
    task_name: str,
) -> LLMJobAccepted:
    """Build the job payload and send it to the Celery worker queue.

    The job_id is a fresh UUID generated here.  It is passed into the worker
    payload so the worker can write Redis metadata under that same key —
    keeping job_id stable and predictable from the moment of submission.

    Args:
        request:   Validated request body.
        queue:     Target Celery queue name.
        task_name: Fully-qualified Celery task name to invoke.

    Returns:
        LLMJobAccepted ready to be returned as the HTTP 202 body.

    Raises:
        HTTP 503: On any queue connectivity failure.
    """
    job_id = str(uuid4())
    submitted_at = datetime.now(timezone.utc).isoformat()

    payload = {
        "job_id": job_id,
        "token_request_id": request.token_request_id,
        "user_id": str(request.user_id),
        "llm_provider": request.llm_provider,
        "llm_model_name": request.llm_model_name,
        "user_prompt": request.user_prompt,
        "submitted_at": submitted_at,
        "system_message": request.system_message,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
    }

    try:
        celery_app.send_task(
            task_name,
            kwargs=payload,
            queue=queue,
            task_id=job_id,
        )
    except Exception as exc:
        logger.error(
            "[LLMInference] Failed to enqueue job | provider={provider} model={model} error={err}",
            provider=request.llm_provider,
            model=request.llm_model_name,
            err=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Worker queue is unreachable. Please retry in a few seconds.",
        ) from exc

    logger.info(
        "[LLMInference] Job queued | job_id={job_id} provider={provider} "
        "model={model} queue={queue}",
        job_id=job_id,
        provider=request.llm_provider,
        model=request.llm_model_name,
        queue=queue,
    )

    return LLMJobAccepted(
        job_id=job_id,
        poll_url=f"/api/v1/llm/jobs/{job_id}",
        submitted_at=submitted_at,
        queue=queue,
    )
