"""Repository paths, service coordinates, and `.env` policy for local tooling.

Everything here is a fact about *this repository's layout* or about the
contract between the tooling and `docker-compose.yml`. Nothing here is tunable
at runtime: a value that an operator should be able to change belongs in
`.env`, and a value the application reads belongs in `app/core/settings`.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
SCHEMA_DIRECTORY = PROJECT_ROOT / "postgres_schema"
SCHEMA_MANIFEST = SCHEMA_DIRECTORY / "schema_creation_order.md"

# The sibling repository that serves quota reservations. Inference fails
# without it, so `start` brings it up too; see token_manager.py.
TOKEN_MANAGER_ROOT = PROJECT_ROOT.parent / "llm_token_manager"
TOKEN_MANAGER_HEALTH_URL = "http://127.0.0.1:8001/api/v1/health/"
# Keys whose value must be byte-identical in both repositories' `.env` files.
# The database trio because the token manager reads llm_services' database
# directly; the JWT secret because llm_services signs the service token that
# the token manager verifies.
TOKEN_MANAGER_SHARED_KEYS = {
    "POSTGRES_USER": "DATABASE_USER",
    "POSTGRES_PASSWORD": "DATABASE_PASSWORD",
    "POSTGRES_DB": "DATABASE_NAME",
    "JWT_SECRET_KEY": "JWT_SECRET_KEY",
}

MANAGED_SERVICES = ("vault", "postgres", "redis")
APPLICATION_SERVICE = "app"
ALL_COMPOSE_SERVICES = (*MANAGED_SERVICES, "vault-init", APPLICATION_SERVICE)
# Must stay in step with the `ports:` entries in docker-compose.yml. The
# tooling reclaims exactly these host ports before starting, so a port added
# to Compose but missing here silently fails to start on a busy machine.
SERVICE_PORTS = {
    "postgres": 5432,
    "redis": 6379,
    "vault": 8200,
    APPLICATION_SERVICE: 8000,
}
MINIMUM_PYTHON_VERSION = (3, 12)
# Mirrors app/schemas/role_hierarchy.py. Duplicated deliberately: this package
# must run before the application's dependencies are installed, so it cannot
# import from `app`.
DASHBOARD_JWT_ROLES = frozenset({"developer", "operator", "admin", "owner"})

# Non-secret values `init` writes when absent. They are safe to commit, safe to
# share, and identical on every machine — which is exactly what distinguishes
# them from the generated secrets in LocalEnvironmentFile.
NON_SECRET_DEFAULTS = {
    "APP_ENVIRONMENT": "development",
    "POSTGRES_USER": "llm_user",
    "POSTGRES_DB": "llm_services",
    "SECRET_BACKEND": "vault",
    "VAULT_ADDR": "http://localhost:8200",
    "VAULT_SERVICE_USERNAME": "llm-service",
    "VAULT_ADMIN_USERNAME": "llm-service-admin",
    "VAULT_MOUNT_PATH": "secret",
    "VAULT_KV_PREFIX": "llm-provider-service",
    "VAULT_TOKEN_REFRESH_AFTER_LEASE_FRACTION": "0.9",
    "JWT_ALGORITHM": "HS256",
    "JWT_ISSUER": "llm-local-infrastructure",
    "JWT_AUDIENCE": "llm-provider-service",
    "JWT_ACCESS_TOKEN_EXPIRE_HOURS": "1",
    "JWT_MAX_TOKEN_AGE_SECONDS": "3600",
    "LOCAL_DASHBOARD_JWT_ROLE": "owner",
}

# Keys that must hold a value before any command can run. A key here is either
# a NON_SECRET_DEFAULTS entry or one that `init` generates; anything else would
# make `init` unable to satisfy its own requirement. Derived values
# (DATABASE_URL, REDIS_URL, VAULT_USERNAME/PASSWORD) are absent on purpose —
# they are composed from these primaries rather than supplied independently.
REQUIRED_PRIMARY_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "REDIS_PASSWORD",
    "VAULT_ROOT_TOKEN",
    "VAULT_SERVICE_USERNAME",
    "VAULT_SERVICE_PASSWORD",
    "VAULT_ADMIN_USERNAME",
    "VAULT_ADMIN_PASSWORD",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "JWT_ACCESS_TOKEN_EXPIRE_HOURS",
    "LOCAL_DASHBOARD_JWT_USER_ID",
    "LOCAL_DASHBOARD_JWT_ROLE",
)
