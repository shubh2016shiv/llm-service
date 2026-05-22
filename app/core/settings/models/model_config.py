"""
LLM Model Specification Models — Per-model capability and pricing metadata.

Each LLMModelSpec is defined inside a provider's YAML and represents a single
model variant (e.g., gpt-4o, claude-3-5-sonnet). Specs are frozen and used
as lookup tables during request validation and cost estimation.

Architecture:
-------------
    config/providers/openai.yaml
          │
          ▼
    ProviderStaticConfig.models: Tuple[LLMModelSpec, ...]
          │
          ├──► Request Validator  (context window check)
          └──► Cost Estimator    (price_per_1k_tokens)

Step-by-step relation:
    1. Provider YAML declares model entries and capabilities.
    2. Loader validates each entry into ``LLMModelSpec``.
    3. Routing/validation checks capability and token limits.
    4. Cost estimation reads pricing fields when present.

Dependencies:
    - pydantic >= 2.0 — frozen BaseModel

Author: Shubham Singh
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ModelCapability(StrEnum):
    """Capabilities a model may support.

    Used to enforce that embed-only models are never sent chat requests,
    and that rerank capability is present before routing rerank calls.
    """

    CHAT = "chat"
    EMBED = "embed"
    RERANK = "rerank"
    IMAGE_GENERATION = "image_generation"
    IMAGE_ANALYSIS = "image_analysis"
    AUDIO_TRANSCRIPTION = "audio_transcription"
    CODE_GENERATION = "code_generation"


class LLMModelSpec(BaseModel):
    """Specification for a single LLM model variant.

    Embedded inside ProviderStaticConfig.models. Loaded from YAML at startup;
    never mutated at runtime. Used for validation, cost estimation, and routing.

    Example:
        >>> spec = LLMModelSpec(
        ...     name="gpt-4o",
        ...     max_output_tokens=4096,
        ...     context_window=128000,
        ...     capabilities=frozenset({ModelCapability.CHAT, ModelCapability.EMBED}),
        ...     price_per_1k_prompt_tokens=Decimal("0.005"),
        ...     price_per_1k_completion_tokens=Decimal("0.015"),
        ... )
        >>> spec.supports(ModelCapability.CHAT)
        True
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(
        description="Model identifier as used in API calls (e.g., 'gpt-4o').",
    )
    display_name: str | None = Field(
        default=None,
        description="Human-readable name for UI display.",
    )
    version: str | None = Field(
        default=None,
        description="Model version string if the provider distinguishes versions.",
    )

    # ── Context & Output Limits ───────────────────────────────────────────
    max_output_tokens: int = Field(
        description="Maximum tokens the model can generate in one response.",
        gt=0,
    )
    context_window: int = Field(
        description="Total token budget (prompt + completion) the model supports.",
        gt=0,
    )

    # ── Capabilities ──────────────────────────────────────────────────────
    capabilities: frozenset[ModelCapability] = Field(
        default=frozenset({ModelCapability.CHAT}),
        description="Set of operations this model supports.",
    )

    # ── Pricing (USD) ─────────────────────────────────────────────────────
    price_per_1k_prompt_tokens: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        description="Cost in USD per 1,000 prompt/input tokens.",
    )
    price_per_1k_completion_tokens: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        description="Cost in USD per 1,000 completion/output tokens.",
    )
    currency: str = Field(
        default="USD",
        description="ISO 4217 currency code for pricing fields.",
    )

    # ── Lifecycle ─────────────────────────────────────────────────────────
    is_active: bool = Field(
        default=True,
        description="Whether this model is available for new requests.",
    )
    is_deprecated: bool = Field(
        default=False,
        description="Deprecated models still work but warn on use.",
    )

    @model_validator(mode="after")
    def validate_context_window_exceeds_output(self) -> LLMModelSpec:
        """Ensure context_window >= max_output_tokens.

        A model cannot output more tokens than its total context allows.

        Raises:
            ValueError: If max_output_tokens exceeds context_window.
        """
        if self.max_output_tokens > self.context_window:
            raise ValueError(
                f"Model {self.name!r}: max_output_tokens ({self.max_output_tokens}) "
                f"cannot exceed context_window ({self.context_window})."
            )
        return self

    def supports(self, capability: ModelCapability) -> bool:
        """Check whether this model supports a given operation.

        Args:
            capability: The capability to check for.

        Returns:
            True if the model supports the given capability.

        Example:
            >>> spec.supports(ModelCapability.EMBED)
            False
        """
        return capability in self.capabilities

    def estimate_cost_usd(
        self, prompt_tokens: int, completion_tokens: int
    ) -> Decimal | None:
        """Estimate the USD cost for a given token usage.

        Returns None when pricing data is not available for this model.

        Args:
            prompt_tokens: Number of prompt/input tokens consumed.
            completion_tokens: Number of completion/output tokens generated.

        Returns:
            Estimated cost in USD, or None if pricing is unavailable.

        Example:
            >>> spec.estimate_cost_usd(prompt_tokens=1000, completion_tokens=500)
            Decimal('0.01250')
        """
        if (
            self.price_per_1k_prompt_tokens is None
            or self.price_per_1k_completion_tokens is None
        ):
            return None

        prompt_cost: Decimal = (
            Decimal(prompt_tokens) / Decimal("1000")
        ) * self.price_per_1k_prompt_tokens

        completion_cost: Decimal = (
            Decimal(completion_tokens) / Decimal("1000")
        ) * self.price_per_1k_completion_tokens

        return (prompt_cost + completion_cost).quantize(Decimal("0.000001"))
