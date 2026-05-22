"""
Catalog Services
================

Business services for managing provider metadata and model metadata.

This sub-package contains:
    - ``ProviderCatalogService``: lifecycle management for provider records
      (registration details, endpoint defaults, and active/inactive state).
    - ``ModelCatalogService``: lifecycle management for model records that
      belong to a provider (capabilities, limits, and availability status).

Rationale:
    Providers and models are strongly related but represent different
    ownership scopes and validation rules. Grouping them in ``catalog/``
    keeps discovery easy while preserving separate service boundaries.

Enterprise Pattern: Domain Service Sub-Package
    Related service classes are co-located by business domain so the
    top-level ``app/services`` package remains navigable.

Author: Shubham Singh
"""
