# PostgreSQL Schema Creation Order

Run the DDL files in this order.

1. `create_required_postgres_capabilities.sql`
2. `create_updated_at_timestamp_trigger.sql`
3. `create_tenants.sql`
4. `create_users.sql`
5. `create_tenant_memberships.sql`
6. `create_provider_catalog.sql`
7. `create_model_catalog.sql`
8. `create_tenant_deployments.sql`
9. `create_user_entitlements.sql`
10. `create_token_manager_deployment_capacity_view.sql`
11. `create_llm_token_allocations.sql`
12. `create_configuration_audit_log.sql`

## Why This Order

PostgreSQL capabilities and the shared timestamp trigger come first because later
tables depend on them.

Tenants and users come before memberships because memberships join those two
roots.

Providers and models come before tenant deployments because deployments choose
from the global catalog.

Tenant deployments come before user entitlements because entitlements override a
tenant route, not a random provider/model pair.

The token manager capacity view comes after deployments because it exposes active
deployment capacity to `llm_token_manager`.

Token allocations come after deployments and users because every allocation must
belong to a real user and a real deployment.

Configuration audit comes last because it references the configuration objects
created earlier.
