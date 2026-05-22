"""
Cloud Providers Package
=======================

Provider adapters that rely on cloud-platform-native auth and/or SDK patterns.

Why this package exists:
    - Cloud integrations often use identity chains (IAM/managed identity) rather
      than static API keys.
    - SDK and endpoint semantics differ from direct REST providers.

Architecture note:
    Cloud adapters intentionally isolate platform-specific auth and endpoint
    semantics (Azure deployment paths, AWS SDK invocation patterns) so the rest
    of the inference stack stays provider-agnostic.

Step-by-step relation:
    1. Registry selects cloud adapter class from static provider config.
    2. Adapter converts normalized request into cloud-native payload/headers.
    3. Adapter translates cloud response back into normalized response schema.

Author: Shubham Singh
"""

from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.cloud.bedrock_provider import BedrockProvider

__all__ = [
    "AzureOpenAIProvider",
    "BedrockProvider",
]

