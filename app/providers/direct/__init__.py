"""
Direct Providers Package
========================

Provider adapters that call external LLM REST APIs directly via httpx.

Why this package exists:
    - Some providers are reached over standard HTTPS with API-key style auth.
    - These adapters share common HTTP patterns but still require provider-specific
      payload and response translation.

Architecture note:
    Direct providers share the same transport style (HTTP REST), but each still
    has unique request schemas, auth headers, and stream event formats. Keeping
    these differences in adapter-local modules avoids branching upstream.

Step-by-step relation:
    1. Registry instantiates direct provider adapter with shared httpx client.
    2. Adapter maps domain request to provider payload.
    3. Adapter maps provider response back to domain response model.

Author: Shubham Singh
"""

from app.providers.direct.anthropic_provider import AnthropicProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider

__all__ = [
    "AnthropicProvider",
    "OpenAIProvider",
    "VLLMProvider",
]

