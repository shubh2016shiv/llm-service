# Enterprise LLM Provider Microservice - High-Level Design

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-05-16 | Principal Architect | Initial HLD |

---

## 1. Executive Summary

### 1.1 Purpose
Design an enterprise-grade, multi-tenant LLM provider abstraction microservice that enables organizations to:
- Manage multiple LLM providers (OpenAI, Anthropic, AWS Bedrock, Azure OpenAI, VLLM, etc.)
- Support multiple tenants with isolated configurations
- Dynamically configure, add, remove, and update providers without service restart
- Ensure high performance through connection pooling and singleton patterns
- Maintain thread safety and async operations
- Provide comprehensive observability

### 1.2 Key Design Principles
1. **Singleton Pattern**: One provider instance per (tenant, deployment) combination
2. **Immutable Configuration**: Thread-safe, read-only provider configs
3. **Connection Pooling**: Shared HTTP clients with persistent connections
4. **Multi-Tenancy**: Complete tenant isolation with shared infrastructure
5. **Dynamic Configuration**: Runtime provider management via PostgreSQL + Redis
6. **Observability**: Structured logging, metrics, and distributed tracing
7. **Extensibility**: Plugin architecture for new providers

---

## 2. System Architecture Overview

### 2.1 Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          API Gateway / Load Balancer                     │
│                     (Rate Limiting, Auth, Routing)                       │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                    ┌────────────┴────────────┐
                    │                         │
         ┌──────────▼──────────┐   ┌─────────▼──────────┐
         │  LLM Provider API   │   │  Admin API         │
         │  (FastAPI/Flask)    │   │  (Configuration)   │
         └──────────┬──────────┘   └─────────┬──────────┘
                    │                        │
         ┌──────────▼────────────────────────▼──────────┐
         │         Application Layer                     │
         │  ┌─────────────────────────────────────┐     │
         │  │    Request Router & Validator       │     │
         │  └──────────────┬──────────────────────┘     │
         │                 │                             │
         │  ┌──────────────▼──────────────────────┐     │
         │  │      Provider Registry Manager      │     │
         │  │   (Singleton Cache + Lifecycle)     │     │
         │  └──────────────┬──────────────────────┘     │
         │                 │                             │
         │     ┌───────────┼───────────┐                │
         │     │           │           │                │
         │  ┌──▼────┐  ┌──▼────┐  ┌──▼────┐            │
         │  │OpenAI │  │Bedrock│  │ VLLM  │  ...       │
         │  │Provider│ │Provider│ │Provider│            │
         │  └───┬───┘  └───┬───┘  └───┬───┘            │
         │      └──────────┼──────────┘                 │
         └─────────────────┼────────────────────────────┘
                           │
         ┌─────────────────▼────────────────────┐
         │   Shared Async HTTP Transport        │
         │   (httpx.AsyncClient with pooling)   │
         └──────────────────────────────────────┘
                           │
         ┌─────────────────┼────────────────────┐
         │                 │                     │
    ┌────▼─────┐    ┌─────▼──────┐    ┌────────▼───────┐
    │PostgreSQL│    │   Redis    │    │ External LLMs  │
    │(Config & │    │ (Cache &   │    │ (OpenAI, AWS,  │
    │ Metadata)│    │  Registry) │    │  Anthropic)    │
    └──────────┘    └────────────┘    └────────────────┘
```

### 2.2 Request Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Incoming Request                                              │
│    POST /v1/chat/completions                                    │
│    Headers: X-Tenant-ID, X-Deployment-Key, X-Trace-ID          │
│    Body: {messages, model, temperature, ...}                    │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 2. Authentication & Tenant Identification                        │
│    - Validate API key                                           │
│    - Extract tenant_id from JWT/header                          │
│    - Validate tenant is active                                  │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 3. Deployment Resolution                                         │
│    - Lookup deployment_key for tenant                           │
│    - Validate deployment exists and is enabled                  │
│    - Load deployment configuration (model, provider)            │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 4. Provider Registry Lookup                                      │
│    cache_key = f"{tenant_id}:{deployment_id}"                  │
│                                                                  │
│    if cache_key in registry:                                    │
│        provider = registry.get(cache_key)  # Reuse singleton   │
│    else:                                                        │
│        provider = build_provider(deployment_config)            │
│        registry.set(cache_key, provider)   # Cache singleton   │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 5. Provider Execution                                            │
│    response = await provider.generate(                          │
│        messages=request.messages,                               │
│        model=request.model,                                     │
│        temperature=request.temperature,                         │
│        trace_id=trace_id,                                       │
│        tenant_id=tenant_id                                      │
│    )                                                            │
│                                                                  │
│    - Uses shared HTTP client (connection pool)                 │
│    - Emits structured logs                                     │
│    - Tracks metrics (latency, tokens, cost)                    │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 6. Response Normalization                                        │
│    - Convert provider-specific response to standard format     │
│    - Calculate usage metrics                                    │
│    - Estimate costs                                             │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│ 7. Response Return                                               │
│    {                                                            │
│      "id": "chatcmpl-...",                                      │
│      "object": "chat.completion",                               │
│      "model": "gpt-4",                                          │
│      "choices": [...],                                          │
│      "usage": {...}                                             │
│    }                                                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Multi-Tenancy Design

### 3.1 Tenant Isolation Model

```
Tenant A                  Tenant B                  Tenant C
   │                         │                         │
   ├─ Deployment 1          ├─ Deployment 1          ├─ Deployment 1
   │  (OpenAI GPT-4)        │  (Bedrock Claude)      │  (Azure OpenAI)
   │                         │                         │
   ├─ Deployment 2          ├─ Deployment 2          └─ Deployment 2
   │  (Anthropic Claude)    │  (VLLM Llama)             (OpenAI GPT-3.5)
   │                         │
   └─ Deployment 3          └─ Deployment 3
      (VLLM Custom)            (OpenAI GPT-4)


Registry Cache Structure:
{
  "tenant_a:deployment_1": OpenAIProvider(config_1),
  "tenant_a:deployment_2": AnthropicProvider(config_2),
  "tenant_a:deployment_3": VLLMProvider(config_3),
  "tenant_b:deployment_1": BedrockProvider(config_4),
  "tenant_b:deployment_2": VLLMProvider(config_5),
  "tenant_b:deployment_3": OpenAIProvider(config_6),
  "tenant_c:deployment_1": AzureOpenAIProvider(config_7),
  "tenant_c:deployment_2": OpenAIProvider(config_8)
}
```

### 3.2 Tenant Configuration Hierarchy

```
┌──────────────────────────────────────────────────────────────┐
│                      Tenant                                   │
│  - tenant_id: UUID                                           │
│  - name: "Acme Corp"                                         │
│  - api_key: "sk-tenant-..."                                  │
│  - rate_limits: {rpm: 1000, tpm: 100000}                    │
│  - status: "active"                                          │
└────────────────────┬─────────────────────────────────────────┘
                     │
         ┌───────────┼───────────┐
         │           │           │
    ┌────▼────┐ ┌───▼─────┐ ┌──▼──────┐
    │Deploy 1 │ │Deploy 2 │ │Deploy 3 │
    │         │ │         │ │         │
    │provider:│ │provider:│ │provider:│
    │ openai  │ │bedrock  │ │  vllm   │
    │         │ │         │ │         │
    │model:   │ │model:   │ │model:   │
    │ gpt-4   │ │ claude  │ │ llama2  │
    │         │ │         │ │         │
    │config:  │ │config:  │ │config:  │
    │{api_key,│ │{region, │ │{url,    │
    │ timeout}│ │ keys}   │ │ api_key}│
    └─────────┘ └─────────┘ └─────────┘
```

---

## 4. Component Design

### 4.1 Core Components

#### 4.1.1 ProviderRegistry (Singleton Manager)

```python
┌─────────────────────────────────────────────────────────────┐
│                    ProviderRegistry                          │
├─────────────────────────────────────────────────────────────┤
│ Responsibilities:                                            │
│ - Maintain singleton cache of provider instances            │
│ - Thread-safe provider creation and retrieval               │
│ - Handle provider lifecycle (init, refresh, destroy)        │
│ - Watch for configuration changes                           │
├─────────────────────────────────────────────────────────────┤
│ Attributes:                                                  │
│ - _providers: Dict[str, BaseProvider]                       │
│ - _lock: asyncio.Lock                                       │
│ - _http_client_factory: HTTPClientFactory                   │
│ - _config_loader: ConfigurationLoader                       │
├─────────────────────────────────────────────────────────────┤
│ Methods:                                                     │
│ + async get_provider(tenant_id, deployment_id) -> Provider  │
│ + async refresh_provider(tenant_id, deployment_id)          │
│ + async remove_provider(tenant_id, deployment_id)           │
│ + async health_check_all() -> Dict[str, HealthStatus]       │
│ + async get_metrics() -> RegistryMetrics                    │
└─────────────────────────────────────────────────────────────┘
```

#### 4.1.2 BaseProvider (Abstract Interface)

```python
┌─────────────────────────────────────────────────────────────┐
│                    BaseProvider (ABC)                        │
├─────────────────────────────────────────────────────────────┤
│ Immutable Attributes:                                        │
│ - config: ProviderConfig (frozen dataclass)                 │
│ - http_client: httpx.AsyncClient (shared, pooled)           │
│ - logger: StructuredLogger                                  │
│ - serializer: RequestSerializer                             │
│ - deserializer: ResponseDeserializer                        │
├─────────────────────────────────────────────────────────────┤
│ Abstract Methods:                                            │
│ + async generate(request: ChatRequest) -> ChatResponse      │
│ + async embed(request: EmbedRequest) -> EmbedResponse       │
│ + async stream_generate(request) -> AsyncIterator           │
│ + async health_check() -> HealthStatus                      │
├─────────────────────────────────────────────────────────────┤
│ Concrete Methods:                                            │
│ + _build_headers(request: Request) -> Dict                  │
│ + _handle_error(error: Exception) -> ProviderError          │
│ + _emit_log(operation, latency, status, **kwargs)           │
│ + _track_metrics(operation, latency, tokens, cost)          │
└─────────────────────────────────────────────────────────────┘
```

#### 4.1.3 Concrete Provider Implementations

```
BaseProvider
     │
     ├─── OpenAIProvider
     │    ├─ Endpoint: https://api.openai.com/v1
     │    ├─ Auth: Bearer token
     │    └─ Models: gpt-4, gpt-3.5-turbo, etc.
     │
     ├─── AnthropicProvider
     │    ├─ Endpoint: https://api.anthropic.com/v1
     │    ├─ Auth: x-api-key header
     │    └─ Models: claude-3-opus, claude-3-sonnet
     │
     ├─── BedrockProvider
     │    ├─ Endpoint: AWS Bedrock Runtime
     │    ├─ Auth: AWS SigV4
     │    └─ Models: anthropic.claude-*, meta.llama2-*
     │
     ├─── AzureOpenAIProvider
     │    ├─ Endpoint: https://{resource}.openai.azure.com
     │    ├─ Auth: api-key header
     │    └─ Models: Custom deployments
     │
     └─── VLLMProvider
          ├─ Endpoint: http://vllm-server:8000/v1
          ├─ Auth: Optional API key
          └─ Models: Custom models
```

#### 4.1.4 HTTP Client Factory

```python
┌─────────────────────────────────────────────────────────────┐
│                  HTTPClientFactory                           │
├─────────────────────────────────────────────────────────────┤
│ Purpose: Create shared, pooled HTTP clients                  │
├─────────────────────────────────────────────────────────────┤
│ Configuration:                                               │
│ - max_connections: 100                                       │
│ - max_keepalive_connections: 20                             │
│ - keepalive_expiry: 300 seconds                             │
│ - timeout: httpx.Timeout(connect=10, read=60, write=10)     │
│ - limits: httpx.Limits(...)                                 │
│ - retries: 3 (with exponential backoff)                     │
├─────────────────────────────────────────────────────────────┤
│ Methods:                                                     │
│ + create_client(provider_type: str) -> httpx.AsyncClient    │
│ + get_shared_transport() -> httpx.AsyncHTTPTransport        │
└─────────────────────────────────────────────────────────────┘

Connection Pool Behavior:
┌──────────────────────────────────────────────────┐
│  Request 1 ─┐                                    │
│  Request 2 ─┼─→ Shared httpx.AsyncClient        │
│  Request 3 ─┤       │                            │
│  Request N ─┘       │                            │
│                     ▼                            │
│           ┌─────────────────────┐                │
│           │  Connection Pool    │                │
│           │  ┌────┐  ┌────┐    │                │
│           │  │Conn│  │Conn│    │                │
│           │  │ 1  │  │ 2  │ ...│                │
│           │  └────┘  └────┘    │                │
│           └─────────────────────┘                │
│                     │                            │
│                     ▼                            │
│           Multiple External LLMs                 │
└──────────────────────────────────────────────────┘
```

---

## 5. Data Model Design

### 5.1 PostgreSQL Schema

```sql
-- ============================================================
-- TENANTS TABLE
-- ============================================================
CREATE TABLE tenants (
    tenant_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    api_key VARCHAR(512) NOT NULL UNIQUE,
    api_key_hash VARCHAR(128) NOT NULL,
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    rate_limit_rpm INTEGER DEFAULT 1000,
    rate_limit_tpm INTEGER DEFAULT 100000,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    
    CONSTRAINT chk_status CHECK (status IN ('active', 'suspended', 'deleted'))
);

CREATE INDEX idx_tenants_api_key_hash ON tenants(api_key_hash);
CREATE INDEX idx_tenants_status ON tenants(status);

-- ============================================================
-- PROVIDERS TABLE (Provider Type Definitions)
-- ============================================================
CREATE TABLE providers (
    provider_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_name VARCHAR(100) NOT NULL UNIQUE, -- 'openai', 'anthropic', 'bedrock'
    provider_type VARCHAR(50) NOT NULL,         -- 'rest_api', 'aws_sdk', 'grpc'
    implementation_class VARCHAR(255) NOT NULL,  -- 'providers.openai.OpenAIProvider'
    default_config JSONB DEFAULT '{}',
    supported_capabilities JSONB DEFAULT '[]',   -- ['chat', 'embed', 'rerank']
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed data
INSERT INTO providers (provider_name, provider_type, implementation_class, supported_capabilities) VALUES
('openai', 'rest_api', 'providers.openai.OpenAIProvider', '["chat", "embed"]'),
('anthropic', 'rest_api', 'providers.anthropic.AnthropicProvider', '["chat"]'),
('bedrock', 'aws_sdk', 'providers.bedrock.BedrockProvider', '["chat", "embed"]'),
('azure_openai', 'rest_api', 'providers.azure.AzureOpenAIProvider', '["chat", "embed"]'),
('vllm', 'rest_api', 'providers.vllm.VLLMProvider', '["chat"]');

-- ============================================================
-- DEPLOYMENTS TABLE (Tenant-Provider Configurations)
-- ============================================================
CREATE TABLE deployments (
    deployment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    provider_id UUID NOT NULL REFERENCES providers(provider_id),
    
    deployment_name VARCHAR(255) NOT NULL,
    deployment_key VARCHAR(255) NOT NULL,  -- User-friendly identifier
    
    -- Provider-specific configuration
    model_name VARCHAR(255) NOT NULL,
    endpoint_url VARCHAR(512),
    api_key_encrypted TEXT,              -- Encrypted with tenant-specific key
    region VARCHAR(50),
    
    -- Request configuration
    config JSONB NOT NULL DEFAULT '{}',  -- timeout, max_retries, temperature_default, etc.
    
    -- Metadata
    status VARCHAR(50) NOT NULL DEFAULT 'active',
    priority INTEGER DEFAULT 0,          -- For fallback routing
    is_default BOOLEAN DEFAULT false,
    tags JSONB DEFAULT '[]',
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(255),
    
    CONSTRAINT uq_tenant_deployment_key UNIQUE (tenant_id, deployment_key),
    CONSTRAINT chk_deployment_status CHECK (status IN ('active', 'inactive', 'maintenance'))
);

CREATE INDEX idx_deployments_tenant_id ON deployments(tenant_id);
CREATE INDEX idx_deployments_status ON deployments(status);
CREATE INDEX idx_deployments_tenant_key ON deployments(tenant_id, deployment_key);

-- ============================================================
-- PROVIDER_CREDENTIALS TABLE (Sensitive data isolation)
-- ============================================================
CREATE TABLE provider_credentials (
    credential_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_id UUID NOT NULL REFERENCES deployments(deployment_id) ON DELETE CASCADE,
    
    credential_type VARCHAR(50) NOT NULL,  -- 'api_key', 'aws_access_key', 'oauth_token'
    credential_value_encrypted TEXT NOT NULL,
    
    metadata JSONB DEFAULT '{}',
    expires_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT uq_deployment_credential_type UNIQUE (deployment_id, credential_type)
);

-- ============================================================
-- REQUEST_LOGS TABLE (Audit and analytics)
-- ============================================================
CREATE TABLE request_logs (
    log_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
    deployment_id UUID NOT NULL REFERENCES deployments(deployment_id),
    
    request_id VARCHAR(255) NOT NULL UNIQUE,
    trace_id VARCHAR(255),
    
    operation VARCHAR(50) NOT NULL,      -- 'chat', 'embed', 'rerank'
    model_name VARCHAR(255),
    
    -- Timing
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    latency_ms INTEGER,
    
    -- Usage
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    estimated_cost DECIMAL(10, 6),
    
    -- Status
    status_code INTEGER,
    error_type VARCHAR(100),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    
    -- Request metadata
    metadata JSONB DEFAULT '{}',
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Partitioning by month for performance
CREATE INDEX idx_request_logs_tenant_timestamp ON request_logs(tenant_id, timestamp DESC);
CREATE INDEX idx_request_logs_deployment_timestamp ON request_logs(deployment_id, timestamp DESC);
CREATE INDEX idx_request_logs_trace_id ON request_logs(trace_id);

-- ============================================================
-- CONFIGURATION_HISTORY TABLE (Audit trail)
-- ============================================================
CREATE TABLE configuration_history (
    history_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type VARCHAR(50) NOT NULL,    -- 'deployment', 'tenant', 'provider'
    entity_id UUID NOT NULL,
    
    change_type VARCHAR(50) NOT NULL,    -- 'created', 'updated', 'deleted'
    old_value JSONB,
    new_value JSONB,
    changed_fields JSONB,
    
    changed_by VARCHAR(255),
    change_reason TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_config_history_entity ON configuration_history(entity_type, entity_id, created_at DESC);
```

### 5.2 Redis Data Structures

```
# ============================================================
# PROVIDER REGISTRY CACHE
# ============================================================
# Key: provider:registry:{tenant_id}:{deployment_id}
# Type: Hash
# TTL: 3600 seconds (1 hour)
# Purpose: Cache provider configuration to avoid DB hits

HSET provider:registry:{tenant_id}:{deployment_id}
  provider_name "openai"
  model_name "gpt-4"
  endpoint_url "https://api.openai.com/v1"
  config_json "{\"timeout\": 60, \"max_retries\": 3}"
  version "v1.2.3"
  cached_at "2026-05-16T10:00:00Z"

# ============================================================
# DEPLOYMENT CONFIGURATION CACHE
# ============================================================
# Key: deployment:config:{deployment_id}
# Type: String (JSON)
# TTL: 1800 seconds (30 minutes)

SET deployment:config:{deployment_id}
'{
  "deployment_id": "uuid",
  "tenant_id": "uuid",
  "provider_name": "openai",
  "model_name": "gpt-4",
  "config": {...},
  "status": "active"
}'

# ============================================================
# TENANT METADATA CACHE
# ============================================================
# Key: tenant:metadata:{tenant_id}
# Type: Hash
# TTL: 7200 seconds (2 hours)

HSET tenant:metadata:{tenant_id}
  name "Acme Corp"
  status "active"
  rate_limit_rpm "1000"
  rate_limit_tpm "100000"

# ============================================================
# RATE LIMITING
# ============================================================
# Key: ratelimit:{tenant_id}:rpm:{minute_bucket}
# Type: String (counter)
# TTL: 60 seconds

INCR ratelimit:{tenant_id}:rpm:2026051610  # Returns current count
EXPIRE ratelimit:{tenant_id}:rpm:2026051610 60

# Key: ratelimit:{tenant_id}:tpm:{minute_bucket}
# Type: String (counter)
# TTL: 60 seconds

INCRBY ratelimit:{tenant_id}:tpm:2026051610 150  # Add token count
EXPIRE ratelimit:{tenant_id}:tpm:2026051610 60

# ============================================================
# CONFIGURATION CHANGE NOTIFICATIONS
# ============================================================
# Pub/Sub channel for configuration updates
# Channel: config:changes

PUBLISH config:changes
'{
  "event_type": "deployment_updated",
  "tenant_id": "uuid",
  "deployment_id": "uuid",
  "timestamp": "2026-05-16T10:00:00Z"
}'

# ============================================================
# HEALTH CHECK STATUS
# ============================================================
# Key: health:{provider_name}:{deployment_id}
# Type: Hash
# TTL: 300 seconds (5 minutes)

HSET health:openai:{deployment_id}
  status "healthy"
  last_check "2026-05-16T10:00:00Z"
  latency_ms "150"
  error_count "0"
```

---

## 6. Configuration Management System

### 6.1 Configuration Loading Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                  Configuration Sources                       │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │ PostgreSQL   │   │    Redis     │   │ Environment  │   │
│  │  (Source of  │   │   (Cache)    │   │  Variables   │   │
│  │   Truth)     │   │              │   │  (Secrets)   │   │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘   │
│         │                  │                  │            │
└─────────┼──────────────────┼──────────────────┼────────────┘
          │                  │                  │
          └──────────┬───────┴──────────┬───────┘
                     │                  │
          ┌──────────▼──────────────────▼──────────┐
          │     Configuration Loader                │
          │  - Load from cache (Redis) first       │
          │  - Fallback to DB (PostgreSQL)         │
          │  - Merge with environment secrets      │
          │  - Validate configuration schema       │
          │  - Cache result in Redis               │
          └──────────┬─────────────────────────────┘
                     │
          ┌──────────▼─────────────────────────────┐
          │     Provider Registry                  │
          │  Creates/updates provider instances    │
          └────────────────────────────────────────┘
```

### 6.2 Dynamic Configuration Updates

```
┌─────────────────────────────────────────────────────────────┐
│ Admin Update Flow                                            │
└─────────────────────────────────────────────────────────────┘

1. Admin API Call
   POST /admin/v1/tenants/{tenant_id}/deployments/{deployment_id}
   {
     "model_name": "gpt-4-turbo",
     "config": {"timeout": 90}
   }
   │
   ▼
2. Update Database (PostgreSQL)
   UPDATE deployments SET model_name = ..., config = ...
   INSERT INTO configuration_history ...
   │
   ▼
3. Invalidate Cache (Redis)
   DEL deployment:config:{deployment_id}
   DEL provider:registry:{tenant_id}:{deployment_id}
   │
   ▼
4. Publish Change Notification (Redis Pub/Sub)
   PUBLISH config:changes
   {
     "event_type": "deployment_updated",
     "tenant_id": "...",
     "deployment_id": "..."
   }
   │
   ▼
5. Provider Registry Listener
   - Receives notification
   - Removes provider from in-memory cache
   - Next request will rebuild with new config
   │
   ▼
6. Next Request
   - Cache miss in ProviderRegistry
   - Loads new config from DB
   - Creates new provider instance with updated config
   - Caches provider instance
```

---

## 7. Structured Logging Design

### 7.1 Log Schema

```json
{
  "timestamp": "2026-05-16T10:30:45.123Z",
  "level": "INFO",
  "service": "llm-provider-service",
  "version": "1.2.3",
  "environment": "production",
  
  "trace_id": "7f8d4e2a-3b1c-4d5e-9f6a-8c7b5e4d3a2f",
  "request_id": "req-abc123",
  "tenant_id": "tenant-uuid",
  
  "operation": "chat.generate",
  "provider_name": "openai",
  "deployment_name": "gpt4-production",
  "deployment_id": "deployment-uuid",
  "model_name": "gpt-4",
  
  "latency_ms": 1250,
  "status_code": 200,
  "retry_count": 0,
  
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 75,
    "total_tokens": 225
  },
  
  "cost": {
    "estimated_usd": 0.0135
  },
  
  "error": null,
  
  "metadata": {
    "temperature": 0.7,
    "max_tokens": 500,
    "stream": false
  },
  
  "message": "Chat completion successful"
}
```

### 7.2 Logging Levels and Use Cases

```
TRACE   → Detailed execution flow (disabled in production)
DEBUG   → Provider internals, HTTP requests/responses
INFO    → Successful operations, configuration loads
WARNING → Rate limit approaching, retry attempts
ERROR   → Provider errors, failed requests
CRITICAL→ Service degradation, circuit breaker trips
```

---

## 8. Thread Safety & Concurrency Design

### 8.1 Safe vs Unsafe Patterns

```python
# ✅ SAFE: Immutable configuration
@dataclass(frozen=True)
class ProviderConfig:
    endpoint_url: str
    api_key: str
    timeout: int
    model_name: str

# ✅ SAFE: Shared async client (thread-safe)
class OpenAIProvider(BaseProvider):
    def __init__(self, config: ProviderConfig, http_client: httpx.AsyncClient):
        self.config = config  # Immutable
        self.http_client = http_client  # Shared, thread-safe
    
    async def generate(self, request: ChatRequest) -> ChatResponse:
        # Request-local variables (not stored on instance)
        headers = self._build_headers(request)
        payload = self._build_payload(request)
        
        response = await self.http_client.post(
            self.config.endpoint_url,
            headers=headers,
            json=payload
        )
        return self._parse_response(response)

# ❌ UNSAFE: Mutating instance state per request
class BadProvider(BaseProvider):
    def __init__(self):
        self.current_tenant_id = None  # ❌ Shared mutable state
        self.last_request = None       # ❌ Race condition
    
    async def generate(self, request: ChatRequest) -> ChatResponse:
        self.current_tenant_id = request.tenant_id  # ❌ Thread unsafe
        # If two concurrent requests arrive, tenant_id will be wrong
```

### 8.2 Singleton Creation Safety

```python
class ProviderRegistry:
    def __init__(self):
        self._providers: Dict[str, BaseProvider] = {}
        self._lock = asyncio.Lock()  # Async lock for singleton creation
    
    async def get_provider(
        self, 
        tenant_id: str, 
        deployment_id: str
    ) -> BaseProvider:
        cache_key = f"{tenant_id}:{deployment_id}"
        
        # Fast path: check without lock
        if cache_key in self._providers:
            return self._providers[cache_key]
        
        # Slow path: acquire lock for creation
        async with self._lock:
            # Double-check pattern
            if cache_key in self._providers:
                return self._providers[cache_key]
            
            # Create new provider instance
            config = await self._load_config(tenant_id, deployment_id)
            provider = self._build_provider(config)
            
            # Cache singleton
            self._providers[cache_key] = provider
            return provider
```

---

## 9. Error Handling & Resilience

### 9.1 Error Hierarchy

```
ProviderError (Base)
│
├─── AuthenticationError
│    ├─ InvalidAPIKeyError
│    └─ ExpiredTokenError
│
├─── RateLimitError
│    ├─ RequestsPerMinuteExceededError
│    └─ TokensPerMinuteExceededError
│
├─── ValidationError
│    ├─ InvalidRequestError
│    └─ ModelNotSupportedError
│
├─── ProviderUnavailableError
│    ├─ ServiceDownError
│    └─ TimeoutError
│
└─── InternalError
     ├─ ConfigurationError
     └─ SerializationError
```

### 9.2 Retry Strategy

```python
# Retry only on safe operations (idempotent GET-like calls)
RETRY_ON_STATUS_CODES = [429, 500, 502, 503, 504]
MAX_RETRIES = 3
BACKOFF_STRATEGY = "exponential"  # 1s, 2s, 4s

# Example retry configuration
retry_strategy = httpx.Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["POST"],  # LLM calls are typically POST but idempotent
)
```

### 9.3 Circuit Breaker Pattern

```
┌─────────────────────────────────────────────┐
│         Circuit Breaker States              │
├─────────────────────────────────────────────┤
│                                             │
│  ┌─────────┐  failure rate > threshold     │
│  │ CLOSED  │───────────────────────┐       │
│  └────┬────┘                       │       │
│       │                             ▼       │
│       │ success                ┌─────────┐ │
│       │                        │  OPEN   │ │
│       │                        └────┬────┘ │
│       │                             │       │
│       │                             │       │
│       │ after timeout               │       │
│       │                             ▼       │
│       │                     ┌──────────────┐│
│       └─────────────────────│ HALF-OPEN    ││
│         success             └──────────────┘│
│                                             │
└─────────────────────────────────────────────┘

Configuration:
- failure_threshold: 50% (trip after 50% failures)
- timeout: 60 seconds (try again after 60s)
- expected_exception: ProviderUnavailableError
```

---

## 10. Monitoring & Observability

### 10.1 Metrics to Track

```
# Request Metrics
llm_requests_total{tenant_id, provider, model, operation, status}
llm_request_duration_seconds{tenant_id, provider, model, operation}
llm_tokens_total{tenant_id, provider, model, type="prompt|completion"}
llm_cost_usd_total{tenant_id, provider, model}

# Provider Health
llm_provider_health{provider, deployment_id, status="healthy|degraded|down"}
llm_provider_errors_total{provider, error_type}
llm_provider_retry_count{provider}

# Connection Pool
http_connection_pool_size{provider}
http_connection_pool_idle_connections{provider}
http_connection_pool_active_connections{provider}

# Rate Limiting
llm_rate_limit_hits{tenant_id, limit_type="rpm|tpm"}
llm_rate_limit_remaining{tenant_id, limit_type="rpm|tpm"}

# Cache Performance
redis_cache_hits_total{cache_type="provider|deployment|tenant"}
redis_cache_misses_total{cache_type="provider|deployment|tenant"}
```

### 10.2 Distributed Tracing

```
Trace Example:
┌────────────────────────────────────────────────────────────┐
│ Trace ID: 7f8d4e2a-3b1c-4d5e-9f6a-8c7b5e4d3a2f            │
├────────────────────────────────────────────────────────────┤
│                                                            │
│ Span 1: POST /v1/chat/completions [200ms]                 │
│   ├─ Span 2: authenticate_request [5ms]                   │
│   ├─ Span 3: resolve_deployment [10ms]                    │
│   │    └─ Span 4: redis_get deployment:config [2ms]       │
│   ├─ Span 5: get_provider [15ms]                          │
│   │    ├─ Span 6: load_config_from_db [10ms]              │
│   │    └─ Span 7: build_provider_instance [3ms]           │
│   └─ Span 8: provider.generate [150ms]                    │
│        ├─ Span 9: build_request_payload [2ms]             │
│        ├─ Span 10: http_post to OpenAI [140ms]            │
│        └─ Span 11: parse_response [5ms]                   │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## 11. API Design

### 11.1 Client API (LLM Requests)

```
POST /v1/chat/completions
Headers:
  X-Tenant-ID: {tenant_id}
  X-Deployment-Key: {deployment_key}  # e.g., "gpt4-production"
  X-Trace-ID: {trace_id}
  Authorization: Bearer {api_key}

Body:
{
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "temperature": 0.7,
  "max_tokens": 500,
  "stream": false
}

Response:
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1684234567,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello! How can I help you today?"
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 20,
    "completion_tokens": 10,
    "total_tokens": 30
  }
}
```

### 11.2 Admin API (Configuration Management)

```
# Create Deployment
POST /admin/v1/tenants/{tenant_id}/deployments
{
  "deployment_name": "GPT-4 Production",
  "deployment_key": "gpt4-prod",
  "provider_name": "openai",
  "model_name": "gpt-4",
  "config": {
    "timeout": 60,
    "max_retries": 3,
    "temperature_default": 0.7
  },
  "credentials": {
    "api_key": "sk-..."
  }
}

# Update Deployment
PATCH /admin/v1/tenants/{tenant_id}/deployments/{deployment_id}
{
  "model_name": "gpt-4-turbo",
  "config": {
    "timeout": 90
  }
}

# Delete Deployment
DELETE /admin/v1/tenants/{tenant_id}/deployments/{deployment_id}

# List Deployments
GET /admin/v1/tenants/{tenant_id}/deployments
Response:
{
  "deployments": [
    {
      "deployment_id": "uuid",
      "deployment_name": "GPT-4 Production",
      "deployment_key": "gpt4-prod",
      "provider_name": "openai",
      "model_name": "gpt-4",
      "status": "active"
    }
  ]
}

# Health Check
GET /admin/v1/health/deployments/{deployment_id}
Response:
{
  "deployment_id": "uuid",
  "status": "healthy",
  "last_check": "2026-05-16T10:00:00Z",
  "latency_ms": 150,
  "error_count_last_hour": 0
}
```

---

## 12. Security Considerations

### 12.1 Credential Management

```
┌─────────────────────────────────────────────────────────────┐
│            Encryption at Rest (PostgreSQL)                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Tenant API Keys                                         │
│     - Hash with bcrypt (for authentication)                 │
│     - Store original encrypted with AES-256                 │
│                                                              │
│  2. Provider API Keys                                       │
│     - Encrypt with tenant-specific key                      │
│     - Tenant key derived from master key + tenant_id        │
│     - Master key stored in AWS Secrets Manager / Vault      │
│                                                              │
│  3. AWS Credentials (for Bedrock)                           │
│     - Store in separate credentials table                   │
│     - Encrypt with same tenant-specific key                 │
│     - Never log or expose in API responses                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘

Decryption Flow:
┌────────────────────────────────────────────────────────────┐
│ 1. Load master key from Secrets Manager (cached)           │
│ 2. Derive tenant key: HKDF(master_key, tenant_id)         │
│ 3. Decrypt provider credentials                            │
│ 4. Use in-memory only (never persist decrypted)            │
│ 5. Rotate keys periodically (90 days)                      │
└────────────────────────────────────────────────────────────┘
```

### 12.2 API Authentication

```
┌─────────────────────────────────────────────────────────────┐
│              Authentication Flow                             │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Client sends request with Bearer token                  │
│     Authorization: Bearer sk-tenant-abc123...               │
│                                                              │
│  2. Extract and hash API key                                │
│     api_key_hash = bcrypt.hash(api_key)                     │
│                                                              │
│  3. Lookup in Redis cache                                   │
│     GET tenant:auth:{api_key_hash}                          │
│     → Cache hit: return tenant_id                           │
│     → Cache miss: query PostgreSQL                          │
│                                                              │
│  4. Validate tenant status                                  │
│     - Must be "active"                                      │
│     - Check rate limits                                     │
│                                                              │
│  5. Cache result in Redis (1 hour TTL)                      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 13. Deployment Architecture

### 13.1 Infrastructure Components

```
┌────────────────────────────────────────────────────────────────┐
│                     Production Environment                      │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────┐    │
│  │              Load Balancer (AWS ALB / NGINX)          │    │
│  │  - SSL Termination                                    │    │
│  │  - Health Checks                                      │    │
│  │  - Request Routing                                    │    │
│  └──────────────────────┬────────────────────────────────┘    │
│                         │                                      │
│          ┌──────────────┼──────────────┐                      │
│          │              │              │                      │
│  ┌───────▼──────┐ ┌────▼──────┐ ┌────▼──────┐               │
│  │ API Service  │ │ API Service│ │API Service│               │
│  │  Instance 1  │ │ Instance 2 │ │Instance 3 │               │
│  │ (Container)  │ │(Container) │ │(Container)│               │
│  └───────┬──────┘ └────┬───────┘ └────┬──────┘               │
│          │              │              │                      │
│          └──────────────┼──────────────┘                      │
│                         │                                      │
│          ┌──────────────┼──────────────┐                      │
│          │              │              │                      │
│  ┌───────▼──────┐ ┌────▼──────┐ ┌────▼───────┐              │
│  │ PostgreSQL   │ │   Redis    │ │  Secrets   │              │
│  │  (Primary +  │ │  Cluster   │ │  Manager   │              │
│  │   Replicas)  │ │            │ │            │              │
│  └──────────────┘ └────────────┘ └────────────┘              │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

### 13.2 Scaling Strategy

```
Horizontal Scaling:
- API service instances: Auto-scale based on CPU/memory (3-20 instances)
- Each instance maintains its own ProviderRegistry cache
- Stateless design allows seamless scale up/down

Vertical Scaling:
- PostgreSQL: Read replicas for analytics queries
- Redis: Cluster mode for high availability

Connection Pool Sizing:
- Per instance: max_connections = 100
- Total across N instances: 100 * N connections to external LLMs
- Monitor and adjust based on rate limits
```

---

## 14. Migration & Rollout Plan

### 14.1 Phase 1: Foundation (Week 1-2)

```
✓ Set up PostgreSQL schema
✓ Set up Redis cluster
✓ Implement BaseProvider interface
✓ Implement HTTPClientFactory
✓ Implement ProviderRegistry
✓ Build OpenAI provider (reference implementation)
✓ Unit tests for core components
```

### 14.2 Phase 2: Multi-Provider (Week 3-4)

```
✓ Implement Anthropic provider
✓ Implement Bedrock provider
✓ Implement Azure OpenAI provider
✓ Admin API for configuration management
✓ Integration tests
✓ Configuration change notification system
```

### 14.3 Phase 3: Production Readiness (Week 5-6)

```
✓ Structured logging implementation
✓ Metrics and monitoring
✓ Distributed tracing
✓ Rate limiting
✓ Circuit breaker
✓ Health checks
✓ Load testing
✓ Security audit
✓ Documentation
```

### 14.4 Phase 4: Launch (Week 7-8)

```
✓ Staging deployment
✓ Beta testing with select tenants
✓ Production deployment
✓ Monitoring and alerts
✓ Runbooks and incident response
✓ Performance optimization
```

---

## 15. Operational Excellence

### 15.1 Monitoring Dashboards

```
Dashboard 1: Request Performance
- Request rate (per second)
- Latency percentiles (p50, p95, p99)
- Error rate by provider
- Token usage by tenant

Dashboard 2: Provider Health
- Provider availability (uptime %)
- Error distribution by provider
- Retry count trends
- Circuit breaker status

Dashboard 3: Resource Utilization
- Connection pool usage
- Redis cache hit rate
- Database connection count
- Memory/CPU by instance
```

### 15.2 Alerting Rules

```
CRITICAL Alerts:
- Provider error rate > 5% for 5 minutes
- API latency p95 > 5 seconds for 5 minutes
- PostgreSQL connection pool exhausted
- Redis cluster down

WARNING Alerts:
- Provider error rate > 2% for 10 minutes
- Cache hit rate < 80% for 15 minutes
- Rate limit approaching (>80% utilized)
- Unusual cost spike (>50% increase)
```

---

## 16. Cost Optimization

### 16.1 Cost Tracking

```
Cost Attribution:
┌────────────────────────────────────────────────────┐
│ Every request logged with:                         │
│ - tenant_id (for billing)                          │
│ - deployment_id (for optimization)                 │
│ - model_name                                       │
│ - tokens used (prompt + completion)                │
│ - estimated_cost_usd                               │
│                                                     │
│ Aggregation:                                       │
│ - Real-time cost tracking per tenant               │
│ - Daily cost reports                               │
│ - Budget alerts                                    │
└────────────────────────────────────────────────────┘
```

### 16.2 Optimization Strategies

```
1. Model Right-Sizing
   - Track which deployments use expensive models
   - Suggest cheaper alternatives for simple tasks
   - A/B test model quality vs cost

2. Caching
   - Cache identical requests (optional feature)
   - Semantic similarity cache for similar prompts
   - Response time: <10ms for cache hits

3. Batch Processing
   - Group multiple requests where supported
   - Reduce per-request overhead

4. Connection Pooling
   - Reuse TCP/TLS connections
   - Reduce handshake overhead
```

---

## 17. Summary & Key Decisions

### 17.1 Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| Singleton pattern per (tenant, deployment) | Ensures efficient resource usage, shared connection pools |
| PostgreSQL for configuration | ACID guarantees, relational data, audit trail |
| Redis for caching | Sub-millisecond lookups, pub/sub for notifications |
| Async HTTP with connection pooling | High concurrency, connection reuse, performance |
| Immutable provider configs | Thread safety, predictable behavior |
| Separate credentials table | Security isolation, easier key rotation |
| Pub/Sub for config changes | Real-time updates without polling |
| Structured logging | Searchable, aggregatable, machine-readable |

### 17.2 Scalability Targets

```
Performance Targets:
- Latency (p95): < 2 seconds (excluding provider)
- Throughput: > 1000 requests/second per instance
- Cache hit rate: > 90%
- Availability: 99.9% uptime

Scale Limits:
- Tenants: 10,000+
- Deployments per tenant: 100+
- Concurrent requests: 100,000+
- Providers: Unlimited (plugin architecture)
```

---

## Appendix A: Technology Stack

```
Language: Python 3.11+
Web Framework: FastAPI 0.109+
Async HTTP: httpx 0.26+
Database: PostgreSQL 15+
Cache: Redis 7+
ORM: SQLAlchemy 2.0+ (async)
Validation: Pydantic 2.0+
Logging: structlog
Metrics: Prometheus client
Tracing: OpenTelemetry
Containerization: Docker
Orchestration: Kubernetes
Secrets: AWS Secrets Manager / HashiCorp Vault
```

---

## Appendix B: Glossary

| Term | Definition |
|------|------------|
| **Tenant** | An organization or customer using the service |
| **Deployment** | A specific (tenant, provider, model) configuration |
| **Provider** | An LLM service (OpenAI, Anthropic, Bedrock, etc.) |
| **Singleton** | One provider instance per (tenant, deployment) pair |
| **Connection Pool** | Reusable HTTP connections to external services |
| **Registry** | In-memory cache of provider instances |
| **Configuration** | Static settings for a deployment |
| **Request** | Dynamic parameters for a single LLM call |

---

**End of Document**
