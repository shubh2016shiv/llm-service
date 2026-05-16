-- ============================================================================
-- ROLLBACK 5 - remove active llm_models capacity invariant
-- ============================================================================

ALTER TABLE llm_models
DROP CONSTRAINT IF EXISTS llm_models_active_requires_max_tokens;

UPDATE llm_models AS lm
SET is_active_status = audit.previous_is_active_status
FROM llm_models_active_capacity_audit AS audit
WHERE lm.llm_provider = audit.llm_provider
  AND lm.llm_model_name = audit.llm_model_name;
