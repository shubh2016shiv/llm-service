"""Technology adapters used at application boundaries.

Subpackages are grouped by the application capability they adapt:

- ``cache``: Redis caching and pub/sub.
- ``inference_routing``: persistence-backed routing configuration reads.
- ``postgresql``: PostgreSQL engine, connection-pool, and session lifecycle.
- ``secret_management``: secret retrieval and encryption.
- ``provider_transport``: outbound transports and provider circuit breakers.

Import concrete adapters from their owning subpackage. This root package does
not re-export them because explicit imports keep dependency ownership visible.
"""
