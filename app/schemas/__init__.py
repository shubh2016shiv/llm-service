"""Validated data contracts shared across application boundaries.

Import schemas from the module that owns them, for example::

    from app.schemas.requests_schema import ChatRequest
    from app.schemas.responses_schema import ChatResponse
    from app.schemas.management_schema import TenantCreateRequest
    from app.schemas.management_responses import TenantResponse

Why this package has no barrel re-exports
-----------------------------------------
Schema modules sit low in the dependency graph. Eagerly importing every model
here makes an innocent import such as ``app.schemas.model_constraints`` load
management, authentication, and provider configuration too. Besides slowing
startup, that creates circular imports between configuration and schemas.

Direct imports show ownership at the call site, keep startup deterministic,
and ensure editing one contract does not initialize unrelated subsystems.
"""
