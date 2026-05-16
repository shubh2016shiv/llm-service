"""
app.providers.cloud — Cloud-platform providers (IAM/SDK auth).

These providers use cloud-platform-native SDKs (boto3, Azure SDK) instead of
plain httpx. Authentication is handled by the platform's credential chain
(IAM roles, managed identities, environment variables) rather than API keys.
"""

from app.providers.cloud.azure_openai_provider import AzureOpenAIProvider
from app.providers.cloud.bedrock_provider import BedrockProvider

__all__ = [
    "AzureOpenAIProvider",
    "BedrockProvider",
]
