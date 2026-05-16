-- ============================================================================
-- MIGRATION 5 - enforce capacity on active llm_models rows
-- ============================================================================
-- Purpose:
-- 1. Audit currently invalid active rows with NULL max_tokens.
-- 2. Deactivate those rows so the system no longer treats them as routable.
-- 3. Enforce the invariant that active deployments must always declare max_tokens.

CREATE TABLE IF NOT EXISTS llm_models_active_capacity_audit (
    llm_provider TEXT NOT NULL,
    llm_model_name TEXT NOT NULL,
    api_endpoint_url TEXT NOT NULL,
    deployment_name TEXT,
    deployment_region TEXT,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    previous_is_active_status BOOLEAN NOT NULL,
    PRIMARY KEY (llm_provider, llm_model_name)
);

INSERT INTO llm_models_active_capacity_audit (
    llm_provider,
    llm_model_name,
    api_endpoint_url,
    deployment_name,
    deployment_region,
    previous_is_active_status
)
SELECT
    llm_provider,
    llm_model_name,
    api_endpoint_url,
    deployment_name,
    deployment_region,
    is_active_status
FROM llm_models
WHERE is_active_status = TRUE
  AND max_tokens IS NULL
ON CONFLICT (llm_provider, llm_model_name) DO UPDATE
SET
    api_endpoint_url = EXCLUDED.api_endpoint_url,
    deployment_name = EXCLUDED.deployment_name,
    deployment_region = EXCLUDED.deployment_region,
    previous_is_active_status = EXCLUDED.previous_is_active_status,
    captured_at = CURRENT_TIMESTAMP;

UPDATE llm_models
SET is_active_status = FALSE
WHERE is_active_status = TRUE
  AND max_tokens IS NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'llm_models_active_requires_max_tokens'
    ) THEN
        ALTER TABLE llm_models
        ADD CONSTRAINT llm_models_active_requires_max_tokens
        CHECK (NOT is_active_status OR max_tokens IS NOT NULL);
    END IF;
END
$$;
