"""
Catalog services — the librarians for providers and models
============================================================

What this package is for
------------------------
"Catalog" here means the registry of WHAT the platform knows about AI
providers (OpenAI, Anthropic, Bedrock, ...) and their models. Two
services keep those registries, each owning one kind of record:

    1. ProviderCatalogService  (provider_catalog.py)
       The provider records: their name, auth mode, endpoint defaults,
       and active/inactive state.

    2. ModelCatalogService     (model_catalog.py)
       The model records that belong to a provider: which operations a
       model supports, its limits, and its availability status.

Providers and models are strongly related but have different ownership
and validation rules, so each gets its own service — grouped here under
one folder so they are easy to find together.

What a "catalog service" is, in one sentence
--------------------------------------------
A thin layer between the API route and the database. The route does HTTP;
the service does the business thinking: validate, call persistence,
translate errors into typed domain errors, and scrub secret fields before
anything leaves the service. The route never touches the database, and
the database never touches HTTP.

Suggested reading order (for learning this package from scratch)
----------------------------------------------------------------
    1. model_catalog.py      (about 5 minutes) — the full CRUD pattern,
                               explained line by line.
    2. provider_catalog.py   (about 3 minutes) — the same pattern for
                               providers, so it reads faster the second
                               time.

Before you start, two helpers these files rely on (see
app/services/management_helpers.py for the full definitions):

    clean_row / clean_rows        -> return a copy of a database row with
                                     secret-bearing fields (password,
                                     password_hash, secret_reference)
                                     REMOVED, so secrets never leave the
                                     service layer.

    raise_clean_validation_error  -> take a generic database ValueError
                                     and turn it into a typed domain error
                                     (not-found / conflict / validation),
                                     so the API can map it to a clean
                                     HTTP status without reading message
                                     text.

If any line in these files still reads like jargon, it is a bug in the
comments — not in you. Fix it right there.

Author: Shubham Singh
"""
