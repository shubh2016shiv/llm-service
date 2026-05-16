"""
app/providers — LLM provider implementations.

Package Structure
-----------------
    providers/
    ├── base_provider.py       ← BaseProvider (ABC)
    ├── registry.py            ← ProviderRegistry (singleton cache)
    ├── direct/                ← REST API providers (API-key auth)
    │   ├── openai_provider.py
    │   ├── anthropic_provider.py
    │   └── vllm_provider.py
    └── cloud/                 ← Cloud-platform providers (IAM/SDK auth)
        ├── bedrock_provider.py
        └── azure_openai_provider.py

Usage
-----
    from app.providers import BaseProvider, ProviderRegistry
    from app.providers.direct import OpenAIProvider
    from app.providers.cloud import BedrockProvider
"""

from app.providers.base_provider import BaseProvider
from app.providers.registry import ProviderRegistry

__all__ = [
    "BaseProvider",
    "ProviderRegistry",
]
