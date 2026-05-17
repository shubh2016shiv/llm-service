-- Model catalog contains global model definitions.
--
-- This table answers "what models exist for a provider?" It does not describe
-- a tenant endpoint, credential, deployment region, or routing preference.
-- Those are tenant deployment concerns.

CREATE TABLE IF NOT EXISTS model_catalog (
    model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_id UUID NOT NULL REFERENCES provider_catalog(provider_id) ON DELETE RESTRICT,
    model_name TEXT NOT NULL CHECK (btrim(model_name) <> ''),
    model_version TEXT,
    display_name TEXT,
    supported_operations TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    context_window_tokens INTEGER CHECK (context_window_tokens IS NULL OR context_window_tokens > 0),
    max_output_tokens INTEGER CHECK (max_output_tokens IS NULL OR max_output_tokens > 0),
    default_temperature NUMERIC(3, 2) NOT NULL DEFAULT 0.70
        CHECK (default_temperature >= 0.00 AND default_temperature <= 2.00),
    default_top_p NUMERIC(4, 3) NOT NULL DEFAULT 1.000
        CHECK (default_top_p >= 0.000 AND default_top_p <= 1.000),
    pricing_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    model_metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'deprecated', 'retired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_model_catalog_provider_model_id UNIQUE (provider_id, model_id),
    CONSTRAINT chk_model_catalog_supported_operations_not_empty
        CHECK (cardinality(supported_operations) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_model_catalog_provider_model_version
    ON model_catalog(provider_id, model_name, COALESCE(model_version, ''));
CREATE INDEX IF NOT EXISTS idx_model_catalog_provider_status
    ON model_catalog(provider_id, status);
CREATE INDEX IF NOT EXISTS idx_model_catalog_model_name
    ON model_catalog(model_name);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'model_catalog_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER model_catalog_set_updated_at
            BEFORE UPDATE ON model_catalog
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

COMMENT ON TABLE model_catalog IS
    'Global model catalog. Tenant endpoint and credential details live in tenant_deployments.';
COMMENT ON COLUMN model_catalog.context_window_tokens IS
    'Maximum context window advertised by the model, not tenant deployment capacity.';
COMMENT ON COLUMN model_catalog.pricing_metadata IS
    'Structured pricing facts such as prompt and completion token rates.';
