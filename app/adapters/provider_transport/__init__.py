"""Public entry points for outbound provider networking and resilience.

Application composition code should import the two concrete adapters from this
package. The small dependency protocols remain in ``contracts`` because they
are implementation seams, not services an application needs to construct.
"""

from app.adapters.provider_transport.circuit_breaker_registry import (
    ProviderCircuitBreakerRegistry,
)
from app.adapters.provider_transport.transport_factory import ProviderTransportFactory

__all__ = ["ProviderCircuitBreakerRegistry", "ProviderTransportFactory"]
