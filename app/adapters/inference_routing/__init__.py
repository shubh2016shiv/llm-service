"""Production adapters that supply configuration to route resolution.

Reading order for a new developer:
    1. ``contracts.py`` — the small capabilities required from PostgreSQL/Redis.
    2. ``routing_config_mappers.py`` — raw SQL rows become validated models.
    3. ``deployment_cache.py`` — versioned Redis payloads and self-healing.
    4. ``cached_config_reader.py`` — orchestration and shared database reads.

Architecture:
    InferenceRouteResolver -> CachedInferenceRoutingConfigReader
                              |-> PostgreSQL persistence protocols
                              '-> DeploymentConfigCache -> RedisCache
"""

from app.adapters.inference_routing.cached_config_reader import (
    CachedInferenceRoutingConfigReader,
)

__all__ = ["CachedInferenceRoutingConfigReader"]
