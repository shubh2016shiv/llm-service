"""
Direct Providers Package
========================

Provider adapters that call external LLM REST APIs directly via httpx.

Why this package exists:
    - Some providers are reached over standard HTTPS with API-key style auth.
    - These adapters share common HTTP patterns but still require provider-specific
      payload and response translation.

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

