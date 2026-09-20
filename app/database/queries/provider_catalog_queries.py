"""Provider catalog SQL query constants.

Table: provider_catalog
  provider_id               UUID PRIMARY KEY
  provider_name             TEXT NOT NULL UNIQUE  -- lowercase slug, e.g. 'openai'
  display_name              TEXT NOT NULL
  provider_type             TEXT NOT NULL  -- 'direct_api' | 'cloud_api' | 'self_hosted' | 'gateway'
  auth_mode                 TEXT NOT NULL  -- 'bearer_token' | 'api_key_header' | 'aws_sigv4' | 'oauth' | 'custom'
  default_api_endpoint_url  TEXT
  supported_operations      TEXT[] NOT NULL DEFAULT ARRAY[]
  is_active                 BOOLEAN NOT NULL DEFAULT TRUE
  provider_metadata         JSONB NOT NULL DEFAULT '{}'
  created_at                TIMESTAMPTZ
  updated_at                TIMESTAMPTZ
"""

PROVIDER_CATALOG_COLUMN_NAMES: tuple[str, ...] = (
    "provider_id",
    "provider_name",
    "display_name",
    "provider_type",
    "auth_mode",
    "default_api_endpoint_url",
    "supported_operations",
    "is_active",
    "provider_metadata",
    "created_at",
    "updated_at",
)
_PROVIDER_CATALOG_COLUMNS = ",\n        ".join(PROVIDER_CATALOG_COLUMN_NAMES)

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_PROVIDER_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM provider_catalog
    WHERE provider_id = :provider_id
    LIMIT 1
"""

CHECK_PROVIDER_EXISTS_BY_NAME_SQL = """
    SELECT 1 FROM provider_catalog
    WHERE provider_name = :provider_name
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_PROVIDER_SQL = f"""
    INSERT INTO provider_catalog (
        provider_name,
        display_name,
        provider_type,
        auth_mode,
        default_api_endpoint_url,
        supported_operations,
        is_active,
        provider_metadata
    )
    VALUES (
        :provider_name,
        :display_name,
        :provider_type,
        :auth_mode,
        :default_api_endpoint_url,
        :supported_operations,
        :is_active,
        :provider_metadata
    )
    RETURNING
        {_PROVIDER_CATALOG_COLUMNS}
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_PROVIDER_BY_ID_SQL = f"""
    SELECT
        {_PROVIDER_CATALOG_COLUMNS}
    FROM provider_catalog
    WHERE provider_id = :provider_id
"""

GET_PROVIDER_BY_NAME_SQL = f"""
    SELECT
        {_PROVIDER_CATALOG_COLUMNS}
    FROM provider_catalog
    WHERE provider_name = :provider_name
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_ACTIVE_PROVIDERS_SQL = f"""
    SELECT
        {_PROVIDER_CATALOG_COLUMNS}
    FROM provider_catalog
    WHERE is_active = TRUE
    ORDER BY provider_name
    LIMIT :limit OFFSET :offset
"""

LIST_ALL_PROVIDERS_SQL = f"""
    SELECT
        {_PROVIDER_CATALOG_COLUMNS}
    FROM provider_catalog
    ORDER BY provider_name
    LIMIT :limit OFFSET :offset
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_ACTIVE_PROVIDERS_SQL = """
    SELECT COUNT(*) FROM provider_catalog WHERE is_active = TRUE
"""

COUNT_ALL_PROVIDERS_SQL = """
    SELECT COUNT(*) FROM provider_catalog
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_PROVIDER_BY_ID_SQL = """
    DELETE FROM provider_catalog
    WHERE provider_id = :provider_id
"""
