"""
Infrastructure Tooling - local control plane for developer dependencies.

Architecture:
-------------
    Developer Shell
          |
          v
    manage_local_infrastructure.py
          |
          v
    Docker Compose services: postgres, redis, vault, vault-init
          |
          v
    postgres_schema/schema_creation_order.md

Dependencies:
    - docker-compose.yml - defines local infrastructure containers.
    - postgres_schema/schema_creation_order.md - defines approved DDL order.

Author: Engineering Team
Last Updated: 2026-05-17
"""

