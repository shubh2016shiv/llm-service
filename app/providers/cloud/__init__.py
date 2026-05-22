"""
Cloud Providers Package
=======================

Provider adapters that rely on cloud-platform-native auth and/or SDK patterns.

Why this package exists:
    - Cloud integrations often use identity chains (IAM/managed identity) rather
      than static API keys.
    - SDK and endpoint semantics differ from direct REST providers.

Author: Shubham Singh
"""

from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.cloud.bedrock_provider import BedrockProvider

__all__ = [
    "AzureOpenAIProvider",
    "BedrockProvider",
]

