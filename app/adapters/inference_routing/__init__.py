"""PostgreSQL boundary for security-sensitive inference-routing reads.

Architecture:
    InferenceRouteResolver -> PostgresInferenceRoutingConfigReader -> PostgreSQL

Start with ``postgres_config_reader.py`` to follow the two reads, then open
``routing_config_mappers.py`` to see how untrusted SQL rows are validated.
"""

from app.adapters.inference_routing.postgres_config_reader import (
    PostgresInferenceRoutingConfigReader,
)

__all__ = ["PostgresInferenceRoutingConfigReader"]
