"""
Cloud Provider Configuration Models — Region, transport, and SDK defaults.

Architecture:
-------------
    config/cloud_providers/aws.yaml    ──►  AWSCloudConfig
    config/cloud_providers/azure.yaml  ──►  AzureCloudConfig
                                               │
                                               ▼
                                    BedrockProvider / AzureOpenAIProvider

Step-by-step relationship:
    1. ``ConfigLoader`` reads cloud vendor YAML.
    2. YAML is validated into vendor-specific frozen model instances.
    3. Provider adapters read timeout/region defaults from these models.
    4. Deployment-level overrides, when present, take precedence at runtime.

Dependencies:
    - pydantic >= 2.0

Author: Shubham Singh
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CloudVendor(StrEnum):
    """Supported cloud infrastructure vendors."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    ON_PREMISE = "on_premise"


class AWSCloudConfig(BaseModel):
    """AWS-specific infrastructure defaults for Bedrock and other AWS-hosted LLMs.

    Region resolution order:
        1. DeploymentConfig-level override
        2. This settings's default_region
        3. AWS SDK environment / instance metadata

    Example:
        >>> aws = AWSCloudConfig(default_region="us-east-1")
        >>> aws.default_region
        'us-east-1'
    """

    model_config = ConfigDict(frozen=True)

    vendor: CloudVendor = Field(default=CloudVendor.AWS)

    default_region: str = Field(
        default="us-east-1",
        description="Default AWS region for Bedrock and other AWS-hosted services.",
    )
    supported_regions: list[str] = Field(
        default_factory=list,
        description="Regions where Bedrock is available; used for routing validation.",
    )
    # WHY: Per-deployment role assumption limits blast radius of a compromised key.
    default_role_arn: str | None = Field(
        default=None,
        description=(
            "IAM role ARN to assume. If None, uses ambient service credentials."
        ),
    )
    session_duration_seconds: int = Field(
        default=3600,
        ge=900,
        le=43200,
        description="Duration of STS assumed-role sessions in seconds.",
    )
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    read_timeout_seconds: float = Field(
        default=90.0,
        gt=0,
        description="Bedrock can be slow on large models; keep this generous.",
    )
    max_pool_connections: int = Field(
        default=50,
        ge=1,
        description="Maximum connections in the aioboto3 connection pool.",
    )
    extra_config: dict[str, object] = Field(
        default_factory=dict,
        description="Pass-through settings for advanced boto3 Config() parameters.",
    )


class AzureCloudConfig(BaseModel):
    """Azure-specific infrastructure defaults for Azure OpenAI deployments.

    Example:
        >>> az = AzureCloudConfig(default_api_version="2024-02-01")
        >>> az.default_api_version
        '2024-02-01'
    """

    model_config = ConfigDict(frozen=True)

    vendor: CloudVendor = Field(default=CloudVendor.AZURE)

    default_api_version: str = Field(
        default="2024-02-01",
        description="Azure OpenAI API version query parameter.",
    )
    endpoint_template: str = Field(
        default="https://{resource_name}.openai.azure.com",
        description="URL template; {resource_name} is filled from DeploymentConfig.",
    )
    default_subscription_id: str | None = Field(default=None)
    default_resource_group: str | None = Field(default=None)
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    read_timeout_seconds: float = Field(default=60.0, gt=0)


class GCPCloudConfig(BaseModel):
    """GCP-specific defaults for Vertex AI / Gemini (future integration).

    Example:
        >>> gcp = GCPCloudConfig(default_project_id="my-project")
    """

    model_config = ConfigDict(frozen=True)

    vendor: CloudVendor = Field(default=CloudVendor.GCP)

    default_project_id: str | None = Field(default=None)
    default_region: str = Field(default="us-central1")
    connect_timeout_seconds: float = Field(default=10.0, gt=0)
    read_timeout_seconds: float = Field(default=60.0, gt=0)


# Type alias — store any cloud settings behind a single annotation.
AnyCloudConfig = AWSCloudConfig | AzureCloudConfig | GCPCloudConfig
