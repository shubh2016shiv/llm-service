#!/bin/sh
# Vault bootstrap — runs once via the vault-init docker-compose service.
#
# What this does:
#   1. Waits for Vault to be ready and unsealed (dev mode starts pre-unsealed).
#   2. Authenticates as root using VAULT_ROOT_TOKEN.
#   3. Enables KV v2 secrets engine at the 'secret/' mount.
#   4. Writes the llm-service-read policy from the mounted policy file.
#   5. Enables the userpass auth method.
#   6. Creates the service account (VAULT_SERVICE_USERNAME / VAULT_SERVICE_PASSWORD)
#      bound to the llm-service-read policy.
#
# Required environment variables (set by docker-compose):
#   VAULT_ADDR             — e.g. http://vault:8200
#   VAULT_ROOT_TOKEN       — dev root token (VAULT_DEV_ROOT_TOKEN_ID value)
#   VAULT_SERVICE_USERNAME — username the LLM service authenticates with
#   VAULT_SERVICE_PASSWORD — password for that user
#   VAULT_KV_PREFIX        — KV path prefix, e.g. llm-provider-service

set -e

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
POLICY_NAME="llm-service-read"
POLICY_FILE="/policies/llm_service_read.hcl"

export VAULT_ADDR
export VAULT_TOKEN="${VAULT_ROOT_TOKEN}"

# ---------------------------------------------------------------------------
# Step 1 — wait for Vault to accept connections
# ---------------------------------------------------------------------------
echo "[init] Waiting for Vault at ${VAULT_ADDR} ..."
until vault status > /dev/null 2>&1; do
  sleep 2
done
echo "[init] Vault is ready."

# ---------------------------------------------------------------------------
# Step 2 — enable KV v2 secrets engine
# KV v2 is already mounted at 'secret/' in dev mode; the command is
# idempotent — if it's already enabled it exits 0 with a warning.
# ---------------------------------------------------------------------------
echo "[init] Enabling KV v2 secrets engine at 'secret/' ..."
vault secrets enable -path=secret -version=2 kv 2>/dev/null || \
  echo "[init] KV v2 already enabled — skipping."

# ---------------------------------------------------------------------------
# Step 3 — write the read-only service policy
# ---------------------------------------------------------------------------
echo "[init] Writing policy '${POLICY_NAME}' ..."
vault policy write "${POLICY_NAME}" "${POLICY_FILE}"
echo "[init] Policy written."

# ---------------------------------------------------------------------------
# Step 4 — enable userpass auth method
# ---------------------------------------------------------------------------
echo "[init] Enabling userpass auth method ..."
vault auth enable userpass 2>/dev/null || \
  echo "[init] userpass already enabled — skipping."

# ---------------------------------------------------------------------------
# Step 5 — create the LLM service account
# The account is bound exclusively to llm-service-read. It cannot write,
# delete, or access any path outside secret/data/llm-provider-service/*.
# ---------------------------------------------------------------------------
echo "[init] Creating service account '${VAULT_SERVICE_USERNAME}' ..."
vault write \
  auth/userpass/users/"${VAULT_SERVICE_USERNAME}" \
  password="${VAULT_SERVICE_PASSWORD}" \
  policies="${POLICY_NAME}"
echo "[init] Service account created."

# ---------------------------------------------------------------------------
# Step 6 — write placeholder secrets for local development
# In production, secrets are loaded by the secrets-loading pipeline, not here.
# Remove or guard this block when promoting to staging/production.
# ---------------------------------------------------------------------------
echo "[init] Writing placeholder secrets for local development ..."

PREFIX="${VAULT_KV_PREFIX:-llm-provider-service}"

vault kv put "secret/${PREFIX}/providers/openai/default" \
  api_key="${OPENAI_API_KEY:-sk-replace-me-openai}"

vault kv put "secret/${PREFIX}/providers/anthropic/default" \
  api_key="${ANTHROPIC_API_KEY:-sk-ant-replace-me}"

vault kv put "secret/${PREFIX}/providers/azure-openai/default" \
  api_key="${AZURE_OPENAI_API_KEY:-replace-me-azure}"

echo "[init] Placeholder secrets written."

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo "[init] Vault bootstrap complete."
echo "[init] Service account '${VAULT_SERVICE_USERNAME}' is ready."
echo "[init] Auth endpoint: ${VAULT_ADDR}/v1/auth/userpass/login/${VAULT_SERVICE_USERNAME}"
