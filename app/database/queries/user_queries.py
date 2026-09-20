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

``password_hash`` is accepted only as an INSERT parameter. No SELECT or
RETURNING projection exposes it.
"""

USER_SAFE_COLUMN_NAMES: tuple[str, ...] = (
    "user_id",
    "username",
    "email",
    "first_name",
    "last_name",
    "platform_role",
    "status",
    "created_at",
    "updated_at",
)
_USER_SAFE_COLUMNS = ",\n        ".join(USER_SAFE_COLUMN_NAMES)

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

COUNT_USERS_SQL = "SELECT COUNT(*) FROM users"


def build_user_list_query(
    platform_role_filter: str | None,
    status_filter: str | None,
    limit: int,
    offset: int,
) -> tuple[str, dict[str, object]]:
    """Build a parameterized user list query from optional filters."""
    where_clauses, parameters = build_user_filters(platform_role_filter, status_filter)
    parameters.update({"limit": limit, "offset": offset})
    sql = f"""
        SELECT
            {_USER_SAFE_COLUMNS}
        FROM users
        WHERE {" AND ".join(where_clauses)}
        ORDER BY created_at DESC
        LIMIT :limit OFFSET :offset
    """
    return sql, parameters


def build_user_count_query(
    platform_role_filter: str | None,
    status_filter: str | None,
) -> tuple[str, dict[str, object]]:
    """Build a count query using the same filters as the user list query."""
    where_clauses, parameters = build_user_filters(platform_role_filter, status_filter)
    sql = f"SELECT COUNT(*) FROM users WHERE {' AND '.join(where_clauses)}"
    return sql, parameters


def build_user_filters(
    platform_role_filter: str | None,
    status_filter: str | None,
) -> tuple[list[str], dict[str, object]]:
    """Return shared WHERE clauses and bind values for user list/count queries."""
    where_clauses = ["TRUE"]
    parameters: dict[str, object] = {}
    if platform_role_filter is not None:
        where_clauses.append("platform_role = :platform_role")
        parameters["platform_role"] = platform_role_filter
    if status_filter is not None:
        where_clauses.append("status = :status")
        parameters["status"] = status_filter
    return where_clauses, parameters


# ── Delete ────────────────────────────────────────────────────────────────────

DELETE_USER_BY_ID_SQL = """
    DELETE FROM users
    WHERE user_id = :user_id
"""

DELETE_USER_BY_EMAIL_SQL = """
    DELETE FROM users
    WHERE email = :email
    RETURNING user_id
"""
