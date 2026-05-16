"""
app.providers.direct — REST API providers (API-key auth).

These providers communicate with LLM services over standard HTTPS REST APIs
using httpx.AsyncClient. Authentication is typically Bearer token or custom
header-based.
"""

from app.providers.direct.anthropic_provider import AnthropicProvider
from app.providers.direct.openai_provider import OpenAIProvider
from app.providers.direct.vllm_provider import VLLMProvider

__all__ = [
    "AnthropicProvider",
    "OpenAIProvider",
    "VLLMProvider",
]
