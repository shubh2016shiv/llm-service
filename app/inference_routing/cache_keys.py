"""The shared Redis label for a deployment's cached configuration.

One tiny helper, one important idea: the label is built HERE and used by
BOTH sides of the caching story —
    - routing reads it (via CachedInferenceRoutingConfigReader) to fetch
      the cached deployment config,
    - management deletes it when a deployment changes, so the next read
      fetches fresh data.
Writing the format once means the two sides can never drift apart.
"""

# This line makes every type hint below a lazy string. (Boilerplate.)
from __future__ import annotations

# TYPE_CHECKING is only True while a type checker reads the file, never at
# runtime — imports under it exist purely for type hints.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID


def build_deployment_config_cache_key(tenant_id: UUID, deployment_key: str) -> str:
    """Return the one shared Redis label for this deployment's config.

    Example: "tenant:<id>:deployments:<key>".
    """
    return f"tenant:{tenant_id}:deployments:{deployment_key}"
