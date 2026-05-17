-- Users are platform identities.
--
-- Tenant-specific permissions live in tenant_memberships. This table keeps
-- login identity, profile data, global account state, and optional platform
-- administration rights in one place.

CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE CHECK (btrim(username) <> ''),
    email TEXT NOT NULL UNIQUE CHECK (email ~* '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    first_name TEXT NOT NULL CHECK (btrim(first_name) <> ''),
    last_name TEXT NOT NULL CHECK (btrim(last_name) <> ''),
    password_hash TEXT NOT NULL CHECK (btrim(password_hash) <> ''),

    -- This is platform-wide access, not tenant membership. Tenant roles are
    -- stored separately so one user can have different responsibilities in
    -- different tenants.
    platform_role TEXT NOT NULL DEFAULT 'user'
        CHECK (platform_role IN ('owner', 'admin', 'operator', 'user')),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'suspended', 'inactive', 'deleted')),

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'users_set_updated_at'
    ) THEN
        EXECUTE '
            CREATE TRIGGER users_set_updated_at
            BEFORE UPDATE ON users
            FOR EACH ROW
            EXECUTE FUNCTION set_updated_at_timestamp()
        ';
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);

COMMENT ON TABLE users IS
    'People or service identities that can authenticate with the platform.';
COMMENT ON COLUMN users.platform_role IS
    'Platform-wide role. Tenant-specific roles belong in tenant_memberships.';
