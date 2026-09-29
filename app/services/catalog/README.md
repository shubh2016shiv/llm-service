# Catalog services

## The short version

The catalog describes which AI providers and models the platform knows about.

Examples include an OpenAI provider record and a `gpt-4o` model record beneath
it. Administrators manage these database records through this package.

The catalog has two sources that must agree:

1. **Database catalog** — records administrators can list and manage.
2. **Runtime YAML catalog** — provider adapters and models this application
   build actually knows how to execute.

A database record alone is not enough. Creating a provider or model that has no
matching runtime configuration would postpone the failure until a real
inference request. These services reject that drift during management writes.

## Vocabulary

| Term | Plain-language meaning |
| --- | --- |
| Provider | A system that serves AI models, such as OpenAI, Anthropic, Bedrock, or a self-hosted server. |
| Model | A named AI model owned by one provider. |
| Catalog record | The database representation administrators manage. |
| Runtime configuration | Validated YAML describing adapters, endpoints, authentication, models, and capabilities. |
| Capability/operation | Work a provider or model supports, such as chat, embedding, or reranking. |
| Template | A client-safe suggestion built from runtime YAML for management forms. |
| CRUD | Create, read, update, and delete operations. |

## How provider creation works

```text
Validated ProviderCreateRequest
    |
    v
Load provider with the same name from runtime YAML
    |
    +-- missing -> reject management request
    |
    v
Confirm authentication mode and operations agree with YAML
    |
    v
Persist provider row -> remove secret-bearing fields -> return safe row
```

This prevents an administrator from registering a provider the running service
cannot construct.

## How model creation works

A model belongs to exactly one provider. Before writing it, the service:

1. confirms the database provider exists;
2. loads that provider's runtime YAML;
3. confirms the model appears in the YAML;
4. confirms requested operations are supported there;
5. writes and cleans the database row.

Model lookups use both `provider_id` and `model_id`. That ownership check stops
a valid model ID from one provider being read through another provider's URL.

## Runtime templates

`build_provider_templates()` turns YAML configuration into client-safe values
for “Add Provider” and “Add Model” forms. It does not write to the database.

The suggested provider type is only a UI hint. Runtime YAML describes the
transport but does not always know whether an endpoint is a managed API or a
self-hosted service, so the form keeps that suggestion editable.

## Updates, lifecycle, and deletion

- Updates use `exclude_unset=True`; omitted fields remain unchanged.
- Activating a model marks it available again.
- Deactivating currently moves a model to `deprecated`, preserving history.
- Provider deletion does not silently cascade. Database foreign-key rules
  reject deletion while related records still depend on the provider.
- List and count operations use matching filters so pagination totals describe
  the same set of records.

## Errors and safe output

These services raise typed domain errors rather than HTTP responses:

- missing records become `ResourceNotFoundError`;
- duplicates become `ResourceConflictError` through the shared helper;
- invalid or runtime-incompatible configuration becomes
  `ManagementValidationError`.

Every returned database row passes through `clean_row()` or `clean_rows()`.
Secret-named fields are removed before data reaches routers, responses, or
logs.

## File map

1. [`provider_catalog.py`](provider_catalog.py) — provider lifecycle and
   runtime-policy checks.
2. [`model_catalog.py`](model_catalog.py) — provider-scoped model lifecycle and
   runtime-policy checks.
3. [`provider_templates.py`](provider_templates.py) — read-only YAML-to-form
   suggestions.
4. [`__init__.py`](__init__.py) — package overview and public imports.
5. [`../management_helpers.py`](../management_helpers.py) — row cleaning and
   persistence-error translation shared by management services.
6. [`../../core/settings/loader.py`](../../core/settings/loader.py) — loads and
   validates runtime YAML.

HTTP endpoints live in
[`../../api/management_routers/provider_catalog_router.py`](../../api/management_routers/provider_catalog_router.py)
and
[`../../api/management_routers/model_catalog_router.py`](../../api/management_routers/model_catalog_router.py).
Focused runtime-policy tests live in
[`../../../tests/unit/services/test_catalog_runtime_policy.py`](../../../tests/unit/services/test_catalog_runtime_policy.py).

## Rules to preserve

1. Never create database catalog entries the runtime YAML cannot execute.
2. Keep models scoped to their owning provider.
3. Keep list and count filters identical.
4. Use partial-update semantics; do not reset omitted fields.
5. Clean every returned row, including every row in lists.
6. Return typed domain errors and let the API layer choose HTTP statuses.

