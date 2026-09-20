"""Regression tests for explicit and secret-safe database projections.

Architecture:
    Persistence update -> explicit safe columns -> API/service result
"""

from types import ModuleType

import pytest

from app.database.queries import (
    model_catalog_queries,
    provider_catalog_queries,
    tenant_deployment_queries,
    tenant_membership_queries,
    tenant_queries,
    user_entitlement_queries,
    user_queries,
)
from app.database.queries.tenant_deployment_queries import DEPLOYMENT_SAFE_COLUMN_NAMES
from app.database.queries.user_entitlement_queries import ENTITLEMENT_SAFE_COLUMN_NAMES
from app.database.queries.user_queries import (
    USER_SAFE_COLUMN_NAMES,
    build_user_count_query,
    build_user_list_query,
)

QUERY_MODULES: tuple[ModuleType, ...] = (
    model_catalog_queries,
    provider_catalog_queries,
    tenant_deployment_queries,
    tenant_membership_queries,
    tenant_queries,
    user_entitlement_queries,
    user_queries,
)


def test_user_safe_projection_excludes_password_hash() -> None:
    """REQ: user records returned by persistence never expose password hashes."""
    assert "password_hash" not in USER_SAFE_COLUMN_NAMES


def test_deployment_safe_projection_excludes_secret_reference() -> None:
    """REQ: management deployment records never expose credential references."""
    assert "secret_reference" not in DEPLOYMENT_SAFE_COLUMN_NAMES


def test_entitlement_safe_projection_excludes_secret_reference() -> None:
    """REQ: management entitlement records never expose credential references."""
    assert "secret_reference" not in ENTITLEMENT_SAFE_COLUMN_NAMES


def test_user_list_and_count_queries_share_parameterized_filters() -> None:
    """REQ: list and count operations use the same safe filter predicates."""
    list_sql, list_parameters = build_user_list_query("admin", "active", 50, 10)
    count_sql, count_parameters = build_user_count_query("admin", "active")

    for expected_clause in ("platform_role = :platform_role", "status = :status"):
        assert expected_clause in list_sql
        assert expected_clause in count_sql
    assert list_parameters == {
        "platform_role": "admin",
        "status": "active",
        "limit": 50,
        "offset": 10,
    }
    assert count_parameters == {"platform_role": "admin", "status": "active"}
    assert "admin" not in list_sql
    assert "active" not in list_sql


@pytest.mark.parametrize("query_module", QUERY_MODULES)
def test_query_module_uses_explicit_row_projections(query_module: ModuleType) -> None:
    """REQ: schema additions cannot silently change repository return payloads."""
    sql_constants = [
        value
        for name, value in vars(query_module).items()
        if name.endswith("_SQL") and isinstance(value, str)
    ]

    assert all("SELECT *" not in sql.upper() for sql in sql_constants)
    assert all("RETURNING *" not in sql.upper() for sql in sql_constants)
