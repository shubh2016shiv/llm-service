"""PostgreSQL boundary for security-sensitive inference-routing reads.

Architecture:
    InferenceRouteResolver -> PostgresInferenceRoutingConfigReader -> PostgreSQL

Suggested reading order:
    1. ``contracts.py`` defines the adapter's raw-row persistence Protocols.
       These differ from ``app.inference_routing.contracts``, which contains
       domain-facing interfaces that exchange typed routing models.
    2. ``postgres_config_reader.py`` performs the two authoritative reads.
    3. ``routing_config_mappers.py`` validates untrusted rows into models.
"""

from app.adapters.inference_routing.postgres_config_reader import (
    PostgresInferenceRoutingConfigReader,
)

__all__ = ["PostgresInferenceRoutingConfigReader"]
