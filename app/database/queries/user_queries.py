"""
User SQL query constants.

Table: users
  user_id         UUID PRIMARY KEY
  username        TEXT NOT NULL UNIQUE
  email           TEXT NOT NULL UNIQUE
  first_name      TEXT NOT NULL
  last_name       TEXT NOT NULL
  password_hash   TEXT NOT NULL
  platform_role   TEXT NOT NULL  -- 'owner' | 'admin' | 'operator' | 'developer'
  status          TEXT NOT NULL  -- 'active' | 'suspended' | 'inactive' | 'deleted'
  created_at      TIMESTAMPTZ
  updated_at      TIMESTAMPTZ

Note: password_hash is never returned in SELECT queries. The RETURNING clause
on INSERT includes it because the caller already holds the hash and needs the
full row for the response; reads never expose it.
"""

# ── Existence checks ──────────────────────────────────────────────────────────

CHECK_USER_EMAIL_EXISTS_SQL = """
    SELECT 1 FROM users
    WHERE email = :email
    LIMIT 1
"""

CHECK_USERNAME_EXISTS_SQL = """
    SELECT 1 FROM users
    WHERE username = :username
    LIMIT 1
"""

CHECK_USER_EXISTS_BY_ID_SQL = """
    SELECT 1 FROM users
    WHERE user_id = :user_id
    LIMIT 1
"""

# ── Create ────────────────────────────────────────────────────────────────────

CREATE_USER_SQL = """
    INSERT INTO users (
        user_id,
        username,
        email,
        first_name,
        last_name,
        password_hash,
        platform_role,
        status,
        created_at,
        updated_at
    )
    VALUES (
        :user_id,
        :username,
        :email,
        :first_name,
        :last_name,
        :password_hash,
        :platform_role,
        :status,
        :created_at,
        :updated_at
    )
    RETURNING
        user_id,
        username,
        email,
        first_name,
        last_name,
        platform_role,
        status,
        created_at,
        updated_at
"""

# ── Point reads ───────────────────────────────────────────────────────────────

GET_USER_BY_ID_SQL = """
    SELECT
        user_id,
        username,
        email,
        first_name,
        last_name,
        platform_role,
        status,
        created_at,
        updated_at
    FROM users
    WHERE user_id = :user_id
"""

GET_USER_BY_EMAIL_SQL = """
    SELECT
        user_id,
        username,
        email,
        first_name,
        last_name,
        platform_role,
        status,
        created_at,
        updated_at
    FROM users
    WHERE email = :email
"""

GET_USER_BY_USERNAME_SQL = """
    SELECT
        user_id,
        username,
        email,
        first_name,
        last_name,
        platform_role,
        status,
        created_at,
        updated_at
    FROM users
    WHERE username = :username
"""

# ── Aggregate ─────────────────────────────────────────────────────────────────

COUNT_USERS_BY_STATUS_SQL = """
    SELECT COUNT(*)
    FROM users
    WHERE status = :status
"""

COUNT_USERS_BY_ROLE_SQL = """
    SELECT COUNT(*)
    FROM users
    WHERE platform_role = :platform_role
"""

# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_USER_BY_ID_SQL = """
    DELETE FROM users
    WHERE user_id = :user_id
"""

DELETE_USER_BY_EMAIL_SQL = """
    DELETE FROM users
    WHERE email = :email
"""
