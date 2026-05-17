"""
Tenant membership SQL query constants.

Table: tenant_memberships
  membership_id       UUID PRIMARY KEY
  tenant_id           UUID NOT NULL → tenants.tenant_id
  user_id             UUID NOT NULL → users.user_id
  tenant_role         TEXT NOT NULL  -- 'owner' | 'admin' | 'developer' | 'viewer' | 'operator'
  status              TEXT NOT NULL  -- 'active' | 'suspended' | 'inactive'
  created_by_user_id  UUID → users.user_id
  created_at          TIMESTAMPTZ
  updated_at          TIMESTAMPTZ

Unique constraint: (tenant_id, user_id)
"""

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_MEMBERSHIP_EXISTS_SQL = """
    SELECT 1 FROM tenant_memberships
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
    LIMIT 1
"""

CHECK_MEMBERSHIP_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM tenant_memberships
    WHERE membership_id = :membership_id
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_MEMBERSHIP_SQL = """
    INSERT INTO tenant_memberships (
        tenant_id,
        user_id,
        tenant_role,
        status,
        created_by_user_id
    )
    VALUES (
        :tenant_id,
        :user_id,
        :tenant_role,
        :status,
        :created_by_user_id
    )
    RETURNING *
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_MEMBERSHIP_BY_ID_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE membership_id = :membership_id
"""

GET_MEMBERSHIP_BY_TENANT_AND_USER_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
"""

# ── List reads ────────────────────────────────────────────────────────────────

LIST_MEMBERSHIPS_BY_TENANT_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE tenant_id = :tenant_id
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

LIST_MEMBERSHIPS_BY_USER_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE user_id = :user_id
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

LIST_ACTIVE_MEMBERSHIPS_BY_TENANT_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE tenant_id = :tenant_id
      AND status = 'active'
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

LIST_MEMBERSHIPS_BY_TENANT_AND_ROLE_SQL = """
    SELECT *
    FROM tenant_memberships
    WHERE tenant_id = :tenant_id
      AND tenant_role = :tenant_role
    ORDER BY created_at DESC
    LIMIT :limit OFFSET :offset
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_MEMBERSHIPS_BY_TENANT_SQL = """
    SELECT COUNT(*) FROM tenant_memberships WHERE tenant_id = :tenant_id
"""

COUNT_ACTIVE_MEMBERSHIPS_BY_TENANT_SQL = """
    SELECT COUNT(*) FROM tenant_memberships
    WHERE tenant_id = :tenant_id AND status = 'active'
"""

COUNT_TENANTS_FOR_USER_SQL = """
    SELECT COUNT(*) FROM tenant_memberships WHERE user_id = :user_id
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_MEMBERSHIP_BY_ID_SQL = """
    DELETE FROM tenant_memberships
    WHERE membership_id = :membership_id
"""

DELETE_MEMBERSHIP_BY_TENANT_AND_USER_SQL = """
    DELETE FROM tenant_memberships
    WHERE tenant_id = :tenant_id
      AND user_id = :user_id
"""
