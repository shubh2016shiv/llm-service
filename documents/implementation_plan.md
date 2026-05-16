# Enterprise Multi-Tenant LLM Provider Service — HLD & Config Design

## 1. Goal

Design an enterprise-grade, multi-tenant LLM provider abstraction that:
- Supports multiple tenants, each with multiple providers and multiple LLMs
- Is fully flexible: add / remove / edit providers, models, and configurations at runtime
- Enforces singleton + connection-pooled providers per `(tenant, deployment)` key
- Separates **static config** (YAML + Pydantic) from **dynamic runtime state**
- Provides structured logging, thread-safe async operations, and a clean abstract interface

---

## 2. System Architecture (HLD)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                        Incoming LLM Request                                │
│     POST /v1/{operation}  (chat / embed / rerank)                          │
│     Headers: X-Tenant-ID, X-Deployment-Key, X-Trace-ID, Authorization     │
└──────────────────────────────────┬─────────────────────────────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Auth + Tenant Middleware   │
                    │ - Verify tenant API key      │
                    │ - Validate tenant status     │
                    │ - Extract trace_id/user_id   │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Deployment Resolver        │
                    │ Priority: user entitlement   │
                    │   > tenant deployment        │
                    │   > tenant default           │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   Quota + Rate Limit Check   │
                    │ user → tenant → model limits │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      ProviderRegistry        │  ← singleton cache
                    │  key: tenant:deployment_id   │    Dict[str, BaseProvider]
                    │  - reuse if exists           │    asyncio.Lock (double-check)
                    │  - build once if not         │
                    └──────────────┬──────────────┘
                                   │
          ┌──────────┬─────────────┼──────────────┬──────────────┐
          ▼          ▼             ▼               ▼              ▼
   OpenAIProvider  AnthropicProvider  BedrockProvider  AzureOpenAIProvider  VLLMProvider
   (immutable cfg) (immutable cfg)   (immutable cfg)  (immutable cfg)      (immutable cfg)
   (pooled client) (pooled client)   (pooled client)  (pooled client)      (pooled client)
          │          │             │               │              │
          └──────────┴─────────────┴───────────────┴──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  Shared Async HTTP Transport │
                    │  httpx.AsyncClient           │
                    │  - max_connections: 100      │
                    │  - max_keepalive: 20         │
                    │  - keepalive_expiry: 300s    │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │   External LLM APIs          │
                    │  OpenAI / Anthropic / AWS /  │
                    │  Azure / VLLM / etc.         │
                    └─────────────────────────────┘
```

---

## 3. Configuration Hierarchy & Levels

The config stack has **4 levels**, each narrowing scope. Lower levels override higher ones for the same key.

```
Level 1: GLOBAL           (system defaults, env vars, infrastructure)
Level 2: CLOUD PROVIDER   (AWS, Azure, GCP defaults — region, retry, transport settings)
Level 3: LLM PROVIDER     (OpenAI, Anthropic, Bedrock… — auth mode, base URL, capabilities)
Level 4: TENANT           (per-org overrides — rate limits, allowed providers, billing)
  └── Level 4a: DEPLOYMENT (per tenant-provider-model config — API key, model, timeouts)
  └── Level 4b: USER       (per-user entitlement — personal API key, quota overrides)
```

### Override Resolution Order (lowest wins)

```
Global defaults
  ↑ overridden by Cloud Provider config
      ↑ overridden by LLM Provider config
          ↑ overridden by Tenant config
              ↑ overridden by Deployment config
                  ↑ overridden by Per-request params (temperature, max_tokens, etc.)
```

---

## 4. Config Strategy: YAML + Pydantic

### 4.1 Why YAML + Pydantic?

| Concern | Tool | Why |
|---|---|---|
| Static defaults, templates | YAML | Human-readable, version-controlled, no code change needed to add a provider |
| Runtime validation, type safety | Pydantic `BaseSettings` | Catches bad config at startup, not at request time |
| Secrets | Environment variables | Never in YAML; loaded via `pydantic-settings` |
| Dynamic overrides (tenant/deployment) | PostgreSQL + Redis | Editable at runtime without restart |

### 4.2 YAML File Roles

```
config/
├── base.yaml                  ← global defaults (log level, pool sizes, retry policy)
├── providers/                 ← direct REST API providers
│   ├── openai.yaml            ← OpenAI defaults (endpoint, auth_mode, capabilities)
│   ├── anthropic.yaml
│   └── vllm.yaml
├── cloud_providers/           ← cloud platform + cloud provider configs
│   ├── aws.yaml               ← AWS platform defaults (region, IAM, SigV4)
│   ├── bedrock.yaml           ← Bedrock provider (models, capabilities)
│   ├── azure.yaml             ← Azure platform defaults (tenant, subscription)
│   └── azure_openai.yaml      ← Azure OpenAI provider (models, api-version)
└── environments/
    ├── development.yaml       ← dev overrides (lower pool sizes, verbose logging)
    ├── staging.yaml
    └── production.yaml        ← production pool sizes, timeouts
```

### 4.3 YAML Content Examples

**`config/base.yaml`** — Global defaults
```yaml
service:
  name: llm-provider-service
  version: "1.0.0"
  environment: development     # overridden by APP_ENVIRONMENT env var

logging:
  level: INFO
  format: json                 # json | text
  include_request_body: false  # never log raw prompts in prod

http_pool:
  max_connections: 100
  max_keepalive_connections: 20
  keepalive_expiry_seconds: 300
  connect_timeout_seconds: 10
  read_timeout_seconds: 60
  write_timeout_seconds: 10

retry:
  max_attempts: 3
  backoff_multiplier: 1.0
  backoff_max_seconds: 8
  retryable_status_codes: [429, 500, 502, 503, 504]
```

**`config/providers/openai.yaml`** — Provider-level static config
```yaml
provider_name: openai
provider_type: rest_api
implementation_class: app.providers.direct.openai_provider.OpenAIProvider

auth:
  mode: bearer_token           # bearer_token | api_key_header | aws_sigv4 | oauth
  header_name: Authorization
  header_prefix: "Bearer"

endpoints:
  base_url: https://api.openai.com/v1
  chat: /chat/completions
  embed: /embeddings

capabilities:
  - chat
  - embed

defaults:
  timeout_seconds: 60
  max_retries: 3
  temperature: 0.7
  top_p: 1.0

models:
  - name: gpt-4o
    max_tokens: 128000
    context_window: 128000
    price_per_1k_prompt_tokens: 0.005
    price_per_1k_completion_tokens: 0.015
  - name: gpt-4o-mini
    max_tokens: 16384
    context_window: 128000
    price_per_1k_prompt_tokens: 0.00015
    price_per_1k_completion_tokens: 0.0006
  - name: text-embedding-3-small
    max_tokens: 8192
    capabilities: [embed]
```

**`config/cloud_providers/bedrock.yaml`** — Bedrock on AWS
```yaml
provider_name: bedrock
provider_type: aws_sdk
implementation_class: app.providers.cloud.bedrock_provider.BedrockProvider

auth:
  mode: aws_sigv4
  service: bedrock-runtime

endpoints:
  base_url_template: "https://bedrock-runtime.{region}.amazonaws.com"

capabilities:
  - chat
  - embed

defaults:
  timeout_seconds: 90
  max_retries: 3

models:
  - name: anthropic.claude-3-5-sonnet-20241022-v2:0
    max_tokens: 200000
  - name: meta.llama3-70b-instruct-v1:0
    max_tokens: 128000
```

---

## 5. Pydantic Config Models

### 5.1 Config Layers as Frozen Pydantic Models

```
app/
└── core/
    └── config/
        ├── __init__.py
        ├── settings.py          ← ApplicationSettings (pydantic-settings, env vars)
        ├── models/
        │   ├── __init__.py
        │   ├── global_config.py     ← GlobalConfig, HTTPPoolConfig, RetryConfig
        │   ├── provider_config.py   ← ProviderStaticConfig, ProviderAuthConfig
        │   ├── cloud_config.py      ← AWSConfig, AzureConfig
        │   ├── tenant_config.py     ← TenantConfig, DeploymentConfig
        │   └── model_config.py      ← LLMModelSpec
        └── loader.py            ← ConfigLoader (YAML → Pydantic models)
```

### 5.2 Model Definitions (Pydantic v2, frozen=True)

**`global_config.py`**
```python
@dataclass(frozen=True)  # or model_config = ConfigDict(frozen=True)
class HTTPPoolConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_connections: int = 100
    max_keepalive_connections: int = 20
    keepalive_expiry_seconds: int = 300
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 10.0

class RetryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    max_attempts: int = 3
    backoff_multiplier: float = 1.0
    backoff_max_seconds: float = 8.0
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)

class GlobalConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    service_name: str
    environment: str
    http_pool: HTTPPoolConfig
    retry: RetryConfig
    log_level: str = "INFO"
```

**`provider_config.py`**
```python
class ProviderAuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    mode: Literal["bearer_token", "api_key_header", "aws_sigv4", "oauth"]
    header_name: str | None = None
    header_prefix: str | None = None

class ProviderStaticConfig(BaseModel):
    """Loaded from YAML. Immutable. Shared across all tenants using this provider."""
    model_config = ConfigDict(frozen=True)
    provider_name: str
    provider_type: Literal["rest_api", "aws_sdk", "grpc"]
    implementation_class: str
    auth: ProviderAuthConfig
    base_url: str
    capabilities: frozenset[str]
    default_timeout_seconds: float
    default_max_retries: int
    models: tuple[LLMModelSpec, ...]
```

**`tenant_config.py`**
```python
class DeploymentConfig(BaseModel):
    """Per (tenant, deployment) runtime settings. Loaded from DB, cached in Redis."""
    model_config = ConfigDict(frozen=True)
    deployment_id: UUID
    tenant_id: UUID
    deployment_key: str
    provider_name: str              # links to ProviderStaticConfig
    model_name: str
    api_endpoint_url: str
    # Secrets NEVER stored here — loaded from SecretStore at provider build time
    timeout_seconds: float | None = None   # overrides provider default if set
    max_retries: int | None = None
    default_temperature: float = 0.7
    default_max_tokens: int | None = None
    extra_headers: dict[str, str] = {}    # provider-specific headers
    extra_config: dict[str, object] = {}  # provider-specific options (JSONB from DB)

class TenantConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    tenant_id: UUID
    tenant_name: str
    status: Literal["active", "suspended", "trial", "deleted"]
    rate_limit_rpm: int = 1000
    rate_limit_tpm: int = 100_000
    rate_limit_concurrent_requests: int = 10
    allowed_provider_names: frozenset[str] | None = None  # None = all allowed
```

**`settings.py`** — Secrets + Infra from env vars
```python
class ApplicationSettings(BaseSettings):
    """All secrets and infra URLs from environment. Never from YAML."""
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    # Infrastructure
    database_url: SecretStr
    redis_url: str = "redis://localhost:6379"
    app_environment: str = "development"

    # Master key for decrypting tenant provider API keys
    encryption_master_key: SecretStr

    # Service identity
    service_name: str = "llm-provider-service"
    log_level: str = "INFO"
```

---

## 6. Provider Abstract Interface

```
app/
└── providers/
    ├── base_provider.py         ← BaseProvider (ABC)
    ├── registry.py              ← ProviderRegistry (singleton cache)
    ├── direct/
    │   ├── openai_provider.py
    │   ├── anthropic_provider.py
    │   └── vllm_provider.py
    └── cloud/
        ├── bedrock_provider.py
        └── azure_openai_provider.py
```

### 6.1 BaseProvider (Abstract)

```python
class BaseProvider(ABC):
    """Abstract contract for all LLM providers.

    Immutable after construction. All methods are pure functions over:
      request payload + frozen settings + shared HTTP client.
    Never store per-request or per-tenant state on the instance.
    """

    def __init__(
        self,
        static_config: ProviderStaticConfig,
        deployment_config: DeploymentConfig,
        http_client: httpx.AsyncClient,    # shared, pooled — injected
    ) -> None:
        self._static = static_config       # frozen: provider-level defaults
        self._deployment = deployment_config  # frozen: per-tenant deployment
        self._http_client = http_client    # shared across concurrent requests
        self._logger = logging.getLogger(self.__class__.__module__)

    # ── Abstract Operations ───────────────────────────────────────────────
    @abstractmethod
    async def generate(self, request: ChatRequest) -> ChatResponse: ...

    @abstractmethod
    async def embed(self, request: EmbedRequest) -> EmbedResponse: ...

    @abstractmethod
    async def rerank(self, request: RerankRequest) -> RerankResponse: ...

    @abstractmethod
    async def stream_generate(
        self, request: ChatRequest
    ) -> AsyncIterator[ChatStreamChunk]: ...

    @abstractmethod
    async def health_check(self) -> HealthStatus: ...

    # ── Concrete Helpers ──────────────────────────────────────────────────
    def _build_auth_headers(self, api_key: str) -> dict[str, str]: ...
    def _emit_structured_log(self, operation: str, latency_ms: int, ...) -> None: ...
    def _handle_provider_error(self, exc: Exception) -> ProviderError: ...
    def _effective_timeout(self) -> float:
        return self._deployment.timeout_seconds or self._static.default_timeout_seconds
```

### 6.2 Concrete Provider (OpenAI example)

```python
class OpenAIProvider(BaseProvider):
    """OpenAI REST API provider (chat + embed).

    Thread-safe: all state is immutable settings + shared async HTTP client.
    Per-request variables (headers, payloads) are local to each call frame.
    """

    async def generate(self, request: ChatRequest) -> ChatResponse:
        headers = self._build_auth_headers(request.resolved_api_key)
        payload = self._build_chat_payload(request)
        t0 = time.monotonic()
        try:
            response = await self._http_client.post(
                f"{self._deployment.api_endpoint_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._effective_timeout(),
            )
            response.raise_for_status()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._emit_structured_log("chat.generate", latency_ms, status_code=200, ...)
            return self._parse_chat_response(response.json())
        except httpx.HTTPStatusError as exc:
            raise self._handle_provider_error(exc) from exc
```

---

## 7. Provider Registry (Singleton Manager)

```python
class ProviderRegistry:
    """Thread-safe singleton cache of provider instances.

    One provider instance per (tenant_id, deployment_id).
    Uses double-checked locking via asyncio.Lock for safe creation.
    """

    def __init__(
        self,
        http_client_factory: HTTPClientFactory,
        config_loader: ConfigLoader,
        secret_store: SecretStore,
    ) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._lock = asyncio.Lock()
        self._http_client_factory = http_client_factory
        self._config_loader = config_loader
        self._secret_store = secret_store

    async def get_provider(
        self, tenant_id: UUID, deployment_id: UUID
    ) -> BaseProvider:
        cache_key = f"{tenant_id}:{deployment_id}"

        # Fast path — no lock needed for reads (dict reads are GIL-safe + async)
        if cache_key in self._providers:
            return self._providers[cache_key]

        # Slow path — acquire lock, double-check, build
        async with self._lock:
            if cache_key in self._providers:
                return self._providers[cache_key]
            provider = await self._build_provider(tenant_id, deployment_id)
            self._providers[cache_key] = provider
            return provider

    async def invalidate(self, tenant_id: UUID, deployment_id: UUID) -> None:
        """Called when settings changes via Redis pub/sub event."""
        cache_key = f"{tenant_id}:{deployment_id}"
        async with self._lock:
            self._providers.pop(cache_key, None)
```

---

## 8. HTTP Client Factory (Connection Pooling)

```python
class HTTPClientFactory:
    """Creates shared, pooled httpx.AsyncClient instances.

    One shared transport for all providers → same TCP/TLS connection pool.
    Settings come from GlobalConfig (YAML-loaded, frozen).
    """

    def __init__(self, pool_config: HTTPPoolConfig) -> None:
        self._pool_config = pool_config
        self._shared_transport = httpx.AsyncHTTPTransport(
            limits=httpx.Limits(
                max_connections=pool_config.max_connections,
                max_keepalive_connections=pool_config.max_keepalive_connections,
                keepalive_expiry=pool_config.keepalive_expiry_seconds,
            ),
            retries=0,  # Retry logic lives in provider layer via tenacity
        )

    def create_client(self) -> httpx.AsyncClient:
        """Return a client sharing the same underlying transport/pool."""
        return httpx.AsyncClient(
            transport=self._shared_transport,
            timeout=httpx.Timeout(
                connect=self._pool_config.connect_timeout_seconds,
                read=self._pool_config.read_timeout_seconds,
                write=self._pool_config.write_timeout_seconds,
            ),
        )
```

---

## 9. Structured Logging Design

Every provider operation emits a log record with this shape:

```json
{
  "timestamp": "2026-05-16T14:31:00.000Z",
  "level": "INFO",
  "service": "llm-provider-service",
  "environment": "production",

  "trace_id": "uuid",
  "request_id": "uuid",
  "tenant_id": "uuid",
  "user_id": "uuid",

  "operation": "chat.generate",
  "provider_name": "openai",
  "deployment_name": "gpt4-prod",
  "deployment_id": "uuid",
  "model_name": "gpt-4o",

  "latency_ms": 1150,
  "provider_latency_ms": 1100,
  "status_code": 200,
  "retry_count": 0,

  "usage": {
    "prompt_tokens": 200,
    "completion_tokens": 80,
    "total_tokens": 280
  },
  "estimated_cost_usd": 0.0042,
  "error_type": null,
  "message": "chat.generate succeeded"
}
```

Log fields are injected via `logging.getLogger().info(msg, extra={...})`.  
The `structlog` library (or a custom `JSONFormatter`) formats these as JSON in production.

---

## 10. Directory Layout

```
app/
├── core/
│   ├── config/
│   │   ├── __init__.py
│   │   ├── settings.py              ← ApplicationSettings (env vars + secrets)
│   │   ├── loader.py                ← ConfigLoader (YAML → Pydantic)
│   │   └── models/
│   │       ├── global_config.py     ← GlobalConfig, HTTPPoolConfig, RetryConfig
│   │       ├── provider_config.py   ← ProviderStaticConfig, ProviderAuthConfig
│   │       ├── cloud_config.py      ← AWSConfig, AzureConfig
│   │       ├── tenant_config.py     ← TenantConfig, DeploymentConfig
│   │       └── model_config.py      ← LLMModelSpec
│   ├── exceptions.py                ← ProviderError hierarchy
│   ├── logging.py                   ← StructuredLogger, JSONFormatter
│   └── secret_store.py              ← SecretStore (decrypt provider API keys)
│
├── providers/
│   ├── __init__.py                  ← re-exports: BaseProvider, ProviderRegistry
│   ├── base_provider.py             ← BaseProvider (ABC)
│   ├── registry.py                  ← ProviderRegistry (singleton cache)
│   ├── direct/                      ← REST API providers (API-key auth)
│   │   ├── __init__.py
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   └── vllm_provider.py
│   └── cloud/                       ← Cloud-platform providers (IAM/SDK auth)
│       ├── __init__.py
│       ├── bedrock_provider.py
│       └── azure_openai_provider.py
│
├── infrastructure/
│   ├── http_client_factory.py       ← HTTPClientFactory (pooled transport)
│   ├── config_loader.py             ← DB + Redis config fetch
│   └── cache.py                     ← Redis wrapper
│
├── schemas/
│   ├── __init__.py
│   ├── requests.py                  ← ChatRequest, EmbedRequest, RerankRequest
│   ├── responses.py                 ← ChatResponse, EmbedResponse, HealthStatus, ChatStreamChunk
│   └── enums.py                     ← ProviderType, OperationType, AuthMode
│
├── services/
│   ├── __init__.py
│   ├── deployment_resolver.py       ← Hybrid resolution (user > tenant > default)
│   ├── quota_enforcer.py            ← Rate limit + quota checks
│   └── request_dispatcher.py        ← Orchestrates: resolve → quota → get_provider → call
│
└── http_client_manager.py           ← (existing file — absorb into infrastructure/)

config/
├── base.yaml
├── providers/                 ← direct REST API providers
│   ├── openai.yaml
│   ├── anthropic.yaml
│   └── vllm.yaml
├── cloud_providers/           ← cloud platform + cloud provider configs
│   ├── aws.yaml
│   ├── bedrock.yaml
│   ├── azure.yaml
│   └── azure_openai.yaml
└── environments/
    ├── development.yaml
    ├── staging.yaml
    └── production.yaml
```

---

## 11. Config Extensibility: Adding a New Provider

To add a new provider (e.g., `Mistral`):

1. **Create YAML**: `config/providers/mistral.yaml` — define `base_url`, `auth.mode`, `capabilities`, `models`
2. **Create Class**: `app/providers/direct/mistral_provider.py` (or `app/providers/cloud/...` for cloud providers) — extend `BaseProvider`, implement abstract methods
3. **Register in DB**: `INSERT INTO providers (provider_name, implementation_class, ...)` — no code change required in registry
4. **Create Tenant Deployment**: Admin API or DB INSERT into `tenant_deployments` — no restart needed

To **remove** a provider:
- Set `is_active = false` in `providers` table → ProviderRegistry stops resolving new instances
- Call `registry.invalidate(tenant_id, deployment_id)` for active singletons

To **edit** config:
- Update `tenant_deployments` or `user_llm_entitlements` in DB
- Publish Redis `config:changes` event → registry auto-invalidates that singleton
- Next request builds a fresh provider with updated config

---

## 12. Thread-Safety Rules (Summary)

| Pattern | Safe? | Why |
|---|---|---|
| Immutable `ProviderStaticConfig` (`frozen=True`) | ✅ | Read-only, never mutated |
| Shared `httpx.AsyncClient` across requests | ✅ | Thread-safe by design |
| Request-local payload/headers built in method | ✅ | Stack-allocated, no sharing |
| Double-checked `asyncio.Lock` in `ProviderRegistry` | ✅ | Prevents duplicate singletons |
| `tenant_id` / `user_id` on provider instance | ❌ | Shared state → race condition |
| Mutating `timeout` on instance per request | ❌ | Corrupts concurrent requests |
| Storing `last_response` on provider | ❌ | Race condition |

---

## 13. Open Questions for Review

> [!IMPORTANT]
> **Q1: YAML model catalog vs DB-only?**
> Should `models` (GPT-4o, Claude, etc.) live in YAML _and_ be seeded to the DB `llm_models` table, or only in YAML? DB gives queryability + admin UI; YAML gives version control. Recommendation: YAML is the source of truth at startup, seeded to DB on boot, admin can override in DB.

> [!IMPORTANT]
> **Q2: One shared `httpx.AsyncClient` or one per provider type?**
> A single transport pool is most efficient. But Bedrock (AWS SDK) doesn't use httpx — it uses `boto3`/`aioboto3`. Recommendation: REST providers share one `httpx` transport; Bedrock gets its own `aioboto3` session with its own connection pool.

> [!IMPORTANT]
> **Q3: `user_llm_entitlements` scope?**
> Should a user be able to bring their own API key for any provider (including ones not in the tenant's allowed list)? Or should user entitlements be restricted to providers the tenant admin has whitelisted?

> [!WARNING]
> **Q4: Existing `http_client_manager.py`**
> This file is currently empty. Should it become the `HTTPClientFactory`, or should we move it to `app/infrastructure/http_client_factory.py` per the Agents.md layered architecture rules?

> [!NOTE]
> **Q5: Config change propagation**
> Should the Registry listen to Redis Pub/Sub for config invalidation events (real-time, event-driven), or poll periodically? Pub/Sub is recommended for low latency but adds operational complexity.
