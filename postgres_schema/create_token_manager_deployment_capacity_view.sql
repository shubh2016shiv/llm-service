-- Read contract for the token manager microservice.
--
-- The token manager should not need to understand how tenants, providers, and
-- model catalog rows are joined. It needs one clear source of active deployment
-- capacity. This view provides that stable read shape.

CREATE OR REPLACE VIEW token_manager_deployment_capacity AS
SELECT
    td.tenant_id,
    td.deployment_id,
    td.deployment_key,
    td.deployment_name,
    td.provider_deployment_name,
    pc.provider_name,
    mc.model_name,
    td.api_endpoint_url,
    td.cloud_provider,
    td.cloud_region,
    td.token_capacity_limit,
    td.token_lock_duration_seconds,
    td.default_temperature,
    td.default_top_p,
    td.default_max_output_tokens,
    td.routing_priority
FROM tenant_deployments AS td
JOIN provider_catalog AS pc ON pc.provider_id = td.provider_id
JOIN model_catalog AS mc ON mc.model_id = td.model_id
WHERE td.status = 'active'
  AND pc.is_active = TRUE
  AND mc.status = 'active';

COMMENT ON VIEW token_manager_deployment_capacity IS
    'Active deployment capacity read model for the llm_token_manager microservice.';
