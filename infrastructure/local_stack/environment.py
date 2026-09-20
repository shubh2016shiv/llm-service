"""Read, validate, and initialize local infrastructure environment values."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import UUID, uuid4

from infrastructure.local_stack.constants import (
    DASHBOARD_JWT_ROLES,
    NON_SECRET_DEFAULTS,
    REQUIRED_PRIMARY_KEYS,
)

# An unquoted value ends at the first `#` preceded by whitespace. Requiring the
# whitespace keeps values that legitimately contain `#` (a password, a URL
# fragment) intact.
COMMENT_SUFFIX_PATTERN = re.compile(r"\s+#")

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


def parse_env_value(raw_value: str) -> str:
    """Return the effective value of one `KEY=value` right-hand side.

    Docker Compose and this tooling must agree on what a line means, so the two
    conventions Compose applies are reproduced here:

    * A quoted value keeps everything inside the quotes, including any ``#``.
    * An unquoted value ends at the first ``#`` that follows whitespace, which
      is what makes ``APP_ENVIRONMENT=development   # dev | prod`` mean
      ``development`` rather than the whole annotated string.

    Without this, a documented `.env.example` copied verbatim produces values
    with the explanatory comment glued onto them, and the failure surfaces far
    away — as a rejected enum, or a password that is "wrong" for no visible
    reason.
    """
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return COMMENT_SUFFIX_PATTERN.split(value, maxsplit=1)[0].strip()


@dataclass(frozen=True)
class EnvironmentInspection:
    """Read-only description of an environment file's correctness."""

    values: dict[str, str]
    missing_keys: tuple[str, ...]
    duplicate_keys: tuple[str, ...]
    malformed_lines: tuple[int, ...]

    @property
    def is_valid(self) -> bool:
        """Return whether existing content is unambiguous and well formed."""
        return not self.duplicate_keys and not self.malformed_lines


@dataclass(frozen=True)
class LocalInfrastructureEnvironment:
    """Resolved credentials and host-side connection URLs."""

    postgres_user: str
    postgres_password: str
    postgres_database: str
    redis_password: str
    vault_root_token: str
    vault_service_username: str
    vault_service_password: str
    vault_kv_prefix: str
    jwt_secret_key: str
    jwt_algorithm: str
    jwt_issuer: str
    jwt_audience: str
    jwt_access_token_expire_hours: int
    dashboard_jwt_user_id: UUID
    dashboard_jwt_role: str

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> LocalInfrastructureEnvironment:
        """Build a validated environment from parsed primary values."""
        missing = [key for key in REQUIRED_PRIMARY_KEYS if not values.get(key)]
        if missing:
            raise ValueError(f"Missing required .env values: {', '.join(missing)}")
        # Validated here rather than at the point of use because this value is
        # interpolated into the dashboard-identity seed statement and signed
        # into the dashboard JWT. Rejecting it at the boundary keeps both of
        # those callers free of untrusted input.
        role = values["LOCAL_DASHBOARD_JWT_ROLE"]
        if role not in DASHBOARD_JWT_ROLES:
            raise ValueError(
                f"LOCAL_DASHBOARD_JWT_ROLE must be one of: {', '.join(sorted(DASHBOARD_JWT_ROLES))}"
            )
        return cls(
            postgres_user=values["POSTGRES_USER"],
            postgres_password=values["POSTGRES_PASSWORD"],
            postgres_database=values["POSTGRES_DB"],
            redis_password=values["REDIS_PASSWORD"],
            vault_root_token=values["VAULT_ROOT_TOKEN"],
            vault_service_username=values["VAULT_SERVICE_USERNAME"],
            vault_service_password=values["VAULT_SERVICE_PASSWORD"],
            vault_kv_prefix=values.get("VAULT_KV_PREFIX", "llm-provider-service"),
            jwt_secret_key=values["JWT_SECRET_KEY"],
            jwt_algorithm=values["JWT_ALGORITHM"],
            jwt_issuer=values["JWT_ISSUER"],
            jwt_audience=values["JWT_AUDIENCE"],
            jwt_access_token_expire_hours=int(values["JWT_ACCESS_TOKEN_EXPIRE_HOURS"]),
            dashboard_jwt_user_id=UUID(values["LOCAL_DASHBOARD_JWT_USER_ID"]),
            dashboard_jwt_role=values["LOCAL_DASHBOARD_JWT_ROLE"],
        )

    @property
    def postgres_url(self) -> str:
        """Return a URL-safe host-side async PostgreSQL connection string."""
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        database = quote(self.postgres_database, safe="")
        return f"postgresql+asyncpg://{user}:{password}@localhost:5432/{database}"

    @property
    def redis_url(self) -> str:
        """Return a URL-safe host-side Redis connection string."""
        return f"redis://:{quote(self.redis_password, safe='')}@localhost:6379/0"


class LocalEnvironmentFile:
    """Inspect and initialize `.env` without overwriting developer values."""

    def __init__(self, path: Path) -> None:
        """Bind the manager to one project-local environment file."""
        self.path = path

    def inspect(self) -> EnvironmentInspection:
        """Parse `.env` without writing and report ambiguity or missing values."""
        if not self.path.exists():
            return EnvironmentInspection({}, REQUIRED_PRIMARY_KEYS, (), ())

        values: dict[str, str] = {}
        duplicates: set[str] = set()
        malformed_lines: list[int] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                malformed_lines.append(line_number)
                continue
            key, raw_value = stripped.split("=", 1)
            key = key.strip()
            if not key:
                malformed_lines.append(line_number)
                continue
            if key in values:
                duplicates.add(key)
            values[key] = parse_env_value(raw_value)

        missing = tuple(key for key in REQUIRED_PRIMARY_KEYS if not values.get(key))
        return EnvironmentInspection(
            values=values,
            missing_keys=missing,
            duplicate_keys=tuple(sorted(duplicates)),
            malformed_lines=tuple(malformed_lines),
        )

    def initialize(self) -> LocalInfrastructureEnvironment:
        """Append missing generated/default values and return resolved configuration.

        Algorithm:
            1. Reject ambiguous existing files rather than guessing.
            2. Generate only missing local secrets.
            3. Append primary values, then derive application URLs and aliases.
            4. Re-read and validate the completed file.
        """
        inspection = self.inspect()
        if not inspection.is_valid:
            raise ValueError(self._format_inspection_error(inspection))

        additions = self._build_missing_primary_values(inspection.values)
        complete_values = {**inspection.values, **additions}
        additions.update(self._missing_derived_values(complete_values))
        if additions:
            self._append_values(additions)

        final_inspection = self.inspect()
        if not final_inspection.is_valid or final_inspection.missing_keys:
            raise ValueError(self._format_inspection_error(final_inspection))
        return LocalInfrastructureEnvironment.from_values(final_inspection.values)

    def require_initialized(self) -> LocalInfrastructureEnvironment:
        """Return existing values without mutating `.env`."""
        inspection = self.inspect()
        if not inspection.is_valid or inspection.missing_keys:
            raise ValueError(
                f"{self._format_inspection_error(inspection)} Run `python -m infrastructure init`."
            )
        return LocalInfrastructureEnvironment.from_values(inspection.values)

    def _build_missing_primary_values(self, current: Mapping[str, str]) -> dict[str, str]:
        """Create defaults and unique local secrets only for absent keys."""
        generated_secrets = {
            "POSTGRES_PASSWORD": f"local-postgres-{secrets.token_urlsafe(18)}",
            "REDIS_PASSWORD": f"local-redis-{secrets.token_urlsafe(18)}",
            "VAULT_ROOT_TOKEN": f"local-vault-root-{secrets.token_urlsafe(18)}",
            "VAULT_SERVICE_PASSWORD": f"local-vault-service-{secrets.token_urlsafe(18)}",
            "VAULT_ADMIN_PASSWORD": f"local-vault-admin-{secrets.token_urlsafe(18)}",
            "JWT_SECRET_KEY": secrets.token_hex(32),
            "LOCAL_DASHBOARD_JWT_USER_ID": str(uuid4()),
        }
        candidates = {**NON_SECRET_DEFAULTS, **generated_secrets}
        return {key: value for key, value in candidates.items() if not current.get(key)}

    def _missing_derived_values(self, values: Mapping[str, str]) -> dict[str, str]:
        """Build application-facing URLs and Vault credential aliases."""
        environment = LocalInfrastructureEnvironment.from_values(values)
        derived = {
            "DATABASE_URL": environment.postgres_url,
            "REDIS_URL": environment.redis_url,
            "VAULT_USERNAME": environment.vault_service_username,
            "VAULT_PASSWORD": environment.vault_service_password,
        }
        return {key: value for key, value in derived.items() if not values.get(key)}

    def _append_values(self, values: Mapping[str, str]) -> None:
        """Append a clearly marked managed section without replacing existing data."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if self.path.exists() and self.path.stat().st_size else ""
        with self.path.open("a", encoding="utf-8", newline="\n") as env_file:
            env_file.write(f"{prefix}# Local values managed by `python -m infrastructure`.\n")
            for key, value in values.items():
                env_file.write(f"{key}={value}\n")

    @staticmethod
    def _format_inspection_error(inspection: EnvironmentInspection) -> str:
        """Create one actionable validation message from all environment issues."""
        issues: list[str] = []
        if inspection.missing_keys:
            issues.append(f"missing keys: {', '.join(inspection.missing_keys)}")
        if inspection.duplicate_keys:
            issues.append(f"duplicate keys: {', '.join(inspection.duplicate_keys)}")
        if inspection.malformed_lines:
            lines = ", ".join(str(line) for line in inspection.malformed_lines)
            issues.append(f"malformed lines: {lines}")
        return ".env validation failed: " + "; ".join(issues or ["unknown error"])
