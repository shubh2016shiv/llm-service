"""
Providers Package
=================

This package is the execution boundary between our internal inference workflow
and external LLM systems.

Why this layer exists:
    - External providers fail in different ways (timeouts, throttling, schema drift).
    - We need one consistent internal interface regardless of provider.
    - We must centralize resilience controls (timeouts, circuit breaker behavior,
      normalized errors, structured telemetry) in one place.

Enterprise Pattern: Adapter + Registry + Resilience Boundary
    - Adapter: each provider class translates our contracts to provider-specific API calls.
    - Registry: provider instances are cached and reused by resolved route fingerprint.
    - Resilience boundary: circuit breaker and error mapping isolate upstream instability.

Author: Shubham Singh
"""

from app.providers.base_provider import BaseProvider
from app.providers.registry import ProviderRegistry

__all__ = [
    "BaseProvider",
    "ProviderRegistry",
]

