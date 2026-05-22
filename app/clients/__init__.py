"""
External Client Adapters
========================

This package contains client wrappers for communicating with external
microservices outside the LLM services application itself.

Think of these as "remote service callers." Instead of every service in
the application knowing how to talk to an external system (what URL to
call, what protocol to use, how to handle retries), each external system
gets one dedicated client class in this package. The rest of the app just
calls methods on that client, keeping the networking details in one place.

Enterprise Pattern: Adapter Pattern
    Each client class in this package adapts an external service's API into
    a Python interface the application can use naturally. When the external
    service changes (new endpoint, different protocol, different
    authentication method), only the adapter needs to change — the rest of
    the application is unaffected.

Current adapters:
    - TokenManagerClient — talks to the token-allocation microservice to
      check and track usage quotas before allowing LLM requests.

Author: Shubham Singh
"""

from app.clients.token_manager_client import (
    QuotaExceededError,
    TokenManagerClient,
)

__all__ = ["QuotaExceededError", "TokenManagerClient"]
