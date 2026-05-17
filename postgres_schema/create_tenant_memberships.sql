-- Tenant memberships connect users to tenants.
--
-- This is where tenant-level authorization belongs. Keeping roles here avoids
-- the common mistake of assuming a user has the same role in every tenant.

CREATE TABLE IF NOT EXISTS tenant_memberships (
    membership_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    tenant_role TEXT NOT NULL DEFAULT 'developer'
        CHECK (tenant_role IN ('owner', 'admin', 'developer', 'viewer', 'operator')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'inactive')),
    created_by_user_id UUID REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_tenant_memberships_tenant_user UNIQUE (tenant_id, user_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'tenant_memberships_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER tenant_memberships_set_updated_at
            BEFORE UPDATE ON tenant_memberships
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_tenant_memberships_user_tenant
    ON tenant_memberships(user_id, tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant_role
    ON tenant_memberships(tenant_id, tenant_role);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_status
    ON tenant_memberships(status);

COMMENT ON TABLE tenant_memberships IS
    'Tenant-scoped user roles and membership status.';
