-- Configuration audit log.
--
-- This table records who changed routing or security-sensitive configuration.
-- It is intentionally separate from token allocation state. Token allocation is
-- an operational lifecycle; this table is for configuration accountability.

CREATE TABLE IF NOT EXISTS configuration_audit_log (
    audit_log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tenant_id UUID REFERENCES tenants(tenant_id) ON DELETE SET NULL,
    actor_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    action TEXT NOT NULL
        CHECK (action IN ('create', 'update', 'delete', 'activate', 'deactivate', 'rotate_secret')),
    target_table TEXT NOT NULL CHECK (btrim(target_table) <> ''),
    target_record_id UUID,
    change_summary TEXT NOT NULL CHECK (btrim(change_summary) <> ''),
    before_values JSONB,
    after_values JSONB,
    request_id TEXT,
    trace_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_configuration_audit_log_tenant_time
    ON configuration_audit_log(tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_configuration_audit_log_actor_time
    ON configuration_audit_log(actor_user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_configuration_audit_log_target
    ON configuration_audit_log(target_table, target_record_id);

COMMENT ON TABLE configuration_audit_log IS
    'Audit history for tenant, deployment, entitlement, provider, model, and credential-reference changes.';
COMMENT ON COLUMN configuration_audit_log.change_summary IS
    'Plain-language summary of the configuration change.';
