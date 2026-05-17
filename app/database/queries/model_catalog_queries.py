"""
Model catalog SQL query constants.

Table: model_catalog
  model_id                UUID PRIMARY KEY
  provider_id             UUID NOT NULL → provider_catalog.provider_id
  model_name              TEXT NOT NULL
  model_version           TEXT
  display_name            TEXT
  supported_operations    TEXT[] NOT NULL DEFAULT ARRAY[]
  context_window_tokens   INTEGER
  max_output_tokens       INTEGER
  default_temperature     NUMERIC(3,2)  DEFAULT 0.70
  default_top_p           NUMERIC(4,3)  DEFAULT 1.000
  pricing_metadata        JSONB NOT NULL DEFAULT '{}'
  model_metadata          JSONB NOT NULL DEFAULT '{}'
  status                  TEXT NOT NULL  -- 'active' | 'deprecated' | 'retired'
  created_at              TIMESTAMPTZ
  updated_at              TIMESTAMPTZ

Unique index: (provider_id, model_name, COALESCE(model_version, ''))
"""

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_MODEL_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_id = :model_id
    LIMIT 1
"""

CHECK_MODEL_EXISTS_BY_NAME_SQL = """
    SELECT 1 FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_name = :model_name
      AND model_version IS NOT DISTINCT FROM :model_version
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_MODEL_SQL = """
    INSERT INTO model_catalog (
        provider_id,
        model_name,
        model_version,
        display_name,
        supported_operations,
        context_window_tokens,
        max_output_tokens,
        default_temperature,
        default_top_p,
        pricing_metadata,
        model_metadata,
        status
    )
    VALUES (
        :provider_id,
        :model_name,
        :model_version,
        :display_name,
        :supported_operations,
        :context_window_tokens,
        :max_output_tokens,
        :default_temperature,
        :default_top_p,
        :pricing_metadata,
        :model_metadata,
        :status
    )
    RETURNING *
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_MODEL_BY_ID_SQL = """
    SELECT *
    FROM model_catalog
    WHERE model_id = :model_id
"""

GET_MODEL_BY_PROVIDER_AND_ID_SQL = """
    SELECT *
    FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_id = :model_id
"""

GET_MODEL_BY_NAME_SQL = """
    SELECT *
    FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_name = :model_name
      AND model_version IS NOT DISTINCT FROM :model_version
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_MODELS_BY_PROVIDER_SQL = """
    SELECT *
    FROM model_catalog
    WHERE provider_id = :provider_id
    ORDER BY model_name, model_version
    LIMIT :limit OFFSET :offset
"""

LIST_ACTIVE_MODELS_BY_PROVIDER_SQL = """
    SELECT *
    FROM model_catalog
    WHERE provider_id = :provider_id
      AND status = 'active'
    ORDER BY model_name, model_version
    LIMIT :limit OFFSET :offset
"""

LIST_MODELS_BY_OPERATION_SQL = """
    SELECT *
    FROM model_catalog
    WHERE :operation = ANY(supported_operations)
      AND status = 'active'
    ORDER BY provider_id, model_name
    LIMIT :limit OFFSET :offset
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_MODELS_BY_PROVIDER_SQL = """
    SELECT COUNT(*) FROM model_catalog
    WHERE provider_id = :provider_id
"""

COUNT_ACTIVE_MODELS_BY_PROVIDER_SQL = """
    SELECT COUNT(*) FROM model_catalog
    WHERE provider_id = :provider_id AND status = 'active'
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_MODEL_BY_ID_SQL = """
    DELETE FROM model_catalog
    WHERE provider_id = :provider_id
      AND model_id = :model_id
"""
