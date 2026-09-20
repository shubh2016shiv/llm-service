#!/bin/sh
# Vault bootstrap for a single-node, self-hosted stack (runs on your machine).
#
# Runs on every start via the `vault-init` compose service, so every step is
# idempotent: it must be safe to re-run against a Vault that already holds real
# credentials. Reusable across projects — everything project-specific arrives
# through the environment (see the contract below), nothing is hardcoded here.
#
# HOW TO READ THIS FILE
#   Each numbered step below is one small function, and `main` at the bottom
#   calls them in order. Every function has the same shape: CHECK whether the
#   work is already done, and only ACT if it is not. That check-then-act shape
#   is what makes a re-run harmless, so keep it when you add a step.
#
# What this does:
#   1. Waits for the Vault API to answer (sealed or uninitialized both count —
#      those are the states this script exists to resolve).
#   2. Initializes the cluster on first run; the unseal key and generated root
#      token go to a SEPARATE bootstrap volume, not the data volume.
#   3. Unseals Vault if sealed.
#   4. Optionally mints a stable operator token whose ID is VAULT_ROOT_TOKEN.
#   5. Turns on the file audit device, so every later step is on record.
#   6. Enables the KV v2 secrets engine at VAULT_MOUNT_PATH.
#   7. Renders and writes the read and write policies from policies/*.tpl.
#   8. Enables userpass auth and creates the read and write accounts, with
#      bounded token lifetimes.
#
# What it deliberately does NOT do: seed secret values. A placeholder
# credential in a real store is indistinguishable from a real one to the code
# reading it.
#
# ---------------------------------------------------------------------------
# Environment contract
# ---------------------------------------------------------------------------
#   Required
#     VAULT_ADDR              API address, e.g. http://vault:8200
#     VAULT_KV_PREFIX         path prefix all secrets live under; also the
#                             prefix the policies grant access to
#     VAULT_POLICY_BASENAME   policies are named <basename>-read / <basename>-write
#     VAULT_SERVICE_USERNAME  read-only account the runtime service logs in as
#     VAULT_SERVICE_PASSWORD
#   Optional
#     VAULT_MOUNT_PATH        KV v2 mount (default: secret)
#     VAULT_ADMIN_USERNAME    write-only account for whatever creates secrets;
#     VAULT_ADMIN_PASSWORD    both or neither
#     VAULT_ROOT_TOKEN        ID for a stable operator token (default: none;
#                             when unset, no standing root-policy token exists)
#     VAULT_TOKEN_TTL         lifetime of a login token (default: 1h)
#     VAULT_TOKEN_MAX_TTL     hard cap even with renewal (default: 24h)
#     VAULT_AUDIT_LOG_PATH    audit log file, on the SERVER (default:
#                             /vault/logs/audit.log)
#     VAULT_POLICY_DIR        where the *.tpl files are mounted (default: /policies)
#     VAULT_CLUSTER_INIT_FILE where init output is stored
#                             (default: /vault/bootstrap/cluster-init.json)
#
# ---------------------------------------------------------------------------
# What is production-grade here, and what is not
# ---------------------------------------------------------------------------
#   Kept from real deployments: least-privilege read/write split, no default
#   passwords (compose refuses to start without them), an audit trail, bounded
#   token lifetimes, and a key file kept away from the data it unlocks.
#
#   Still a single-machine compromise: one key share, and the unseal key sits
#   on disk so the stack recovers from a restart unattended. Anyone who can
#   read BOTH volumes can unlock Vault. Cloud-KMS auto-unseal, TLS, and Raft
#   storage are what remove that limit; see the README's "Production" section.

set -e
# Files this script creates (the key file) must never be group/world readable.
umask 077

VAULT_ADDR="${VAULT_ADDR:-http://vault:8200}"
VAULT_MOUNT_PATH="${VAULT_MOUNT_PATH:-secret}"
VAULT_POLICY_DIR="${VAULT_POLICY_DIR:-/policies}"
VAULT_TOKEN_TTL="${VAULT_TOKEN_TTL:-1h}"
VAULT_TOKEN_MAX_TTL="${VAULT_TOKEN_MAX_TTL:-24h}"
VAULT_AUDIT_LOG_PATH="${VAULT_AUDIT_LOG_PATH:-/vault/logs/audit.log}"
CLUSTER_INIT_FILE="${VAULT_CLUSTER_INIT_FILE:-/vault/bootstrap/cluster-init.json}"
# Where earlier versions of this script kept the key: on the data volume. Only
# read by migrate_legacy_key_file, so an existing stack upgrades in place.
LEGACY_INIT_FILE="/vault/file/cluster-init.json"

export VAULT_ADDR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[init] $*"; }

fail() {
  echo "[init] ERROR: $*" >&2
  exit 1
}

# A missing variable would otherwise become an empty password or an empty path
# segment, and Vault would accept both without complaint.
require_var() {
  eval "value=\${$1:-}"
  [ -n "${value}" ] || fail "required environment variable $1 is not set."
}

# Mount, prefix, and policy names are substituted into HCL and used as paths.
# Restricting them to a conservative alphabet closes off sed and HCL
# metacharacters, and keeps a value like "../" from widening a policy.
require_safe_path() {
  case "$2" in
    /* | */ | *..* | *[!A-Za-z0-9._/-]*)
      fail "$1='$2' must use only letters, digits, '.', '_', '-', '/', with no leading/trailing '/' and no '..'."
      ;;
  esac
}

# A duration is digits plus one unit (30m, 1h, 24h). Anything else would be
# passed to Vault, which reads bare numbers as SECONDS — "24" would silently
# become a 24-second token.
require_duration() {
  case "$2" in
    [0-9]*[smh]) ;;
    *) fail "$1='$2' must be a number followed by s, m, or h (for example 30m or 24h)." ;;
  esac
}

# Only the audit path is checked as a path here: it is handed to the server.
require_safe_file_path() {
  case "$2" in
    /* ) ;;
    *) fail "$1='$2' must be an absolute path." ;;
  esac
  case "$2" in
    *..* | *[!A-Za-z0-9._/-]*) fail "$1='$2' contains characters outside letters, digits, '.', '_', '-', '/'." ;;
  esac
}

# Render one policy template: substitute the two placeholders from the same
# variables the application builds its KV paths from.
render_policy() {
  [ -f "$1" ] || fail "policy template not found: $1"
  sed -e "s|__MOUNT__|${VAULT_MOUNT_PATH}|g" -e "s|__PREFIX__|${VAULT_KV_PREFIX}|g" "$1"
}

# `vault <x> list` as flattened JSON. No jq in this image, and the output is
# pretty-printed across lines, so whitespace is stripped first — safe because
# mount and auth paths contain none.
flat_secrets() { vault secrets list -format=json | tr -d ' \n\r'; }
flat_auth() { vault auth list -format=json | tr -d ' \n\r'; }
flat_audit() { vault audit list -format=json | tr -d ' \n\r'; }

validate_environment() {
  require_var VAULT_KV_PREFIX
  require_var VAULT_POLICY_BASENAME
  require_var VAULT_SERVICE_USERNAME
  require_var VAULT_SERVICE_PASSWORD
  require_safe_path VAULT_MOUNT_PATH "${VAULT_MOUNT_PATH}"
  require_safe_path VAULT_KV_PREFIX "${VAULT_KV_PREFIX}"
  require_safe_path VAULT_POLICY_BASENAME "${VAULT_POLICY_BASENAME}"
  require_duration VAULT_TOKEN_TTL "${VAULT_TOKEN_TTL}"
  require_duration VAULT_TOKEN_MAX_TTL "${VAULT_TOKEN_MAX_TTL}"
  require_safe_file_path VAULT_AUDIT_LOG_PATH "${VAULT_AUDIT_LOG_PATH}"
  # The admin account is one identity: half of it configured is a mistake.
  if [ -n "${VAULT_ADMIN_USERNAME:-}" ] || [ -n "${VAULT_ADMIN_PASSWORD:-}" ]; then
    require_var VAULT_ADMIN_USERNAME
    require_var VAULT_ADMIN_PASSWORD
  fi
}

# ---------------------------------------------------------------------------
# Step 1 — wait for the Vault API to answer
#
# `vault status` exit codes: 0 = unsealed, 2 = sealed, 1 = cannot reach.
# Sealed and uninitialized are expected here — only exit 1 means keep waiting.
# ---------------------------------------------------------------------------
wait_for_vault() {
  log "Waiting for Vault at ${VAULT_ADDR} ..."
  attempt=0
  status_code=1
  while [ "${attempt}" -lt 60 ]; do
    vault status >/dev/null 2>&1 && status_code=0 || status_code=$?
    [ "${status_code}" -ne 1 ] && break
    attempt=$((attempt + 1))
    sleep 2
  done
  [ "${status_code}" -ne 1 ] || fail "Vault did not become reachable in time."
  log "Vault API is reachable."
}

# ---------------------------------------------------------------------------
# Step 2 — initialize on first run, reuse the stored keys afterwards
#
# `operator init -status` exit codes: 0 = initialized, 2 = not initialized,
# anything else = the check itself failed. Treating "anything else" as "not
# initialized" would re-run init against a healthy cluster.
# ---------------------------------------------------------------------------

# Earlier versions kept the key on the data volume, next to the data it
# unlocks — so a backup of that volume was also a copy of the key. Move it to
# the bootstrap volume once; a fresh install has nothing to move.
migrate_legacy_key_file() {
  [ -f "${LEGACY_INIT_FILE}" ] || return 0
  [ ! -f "${CLUSTER_INIT_FILE}" ] || return 0
  log "Moving the unseal key off the data volume to ${CLUSTER_INIT_FILE} ..."
  mv "${LEGACY_INIT_FILE}" "${CLUSTER_INIT_FILE}"
  chmod 600 "${CLUSTER_INIT_FILE}"
}

initialize_if_needed() {
  vault operator init -status >/dev/null 2>&1 && init_code=0 || init_code=$?
  case "${init_code}" in
    0) log "Vault is already initialized." ;;
    2) run_operator_init ;;
    *) fail "could not determine whether Vault is initialized (exit ${init_code})." ;;
  esac
  if [ ! -f "${CLUSTER_INIT_FILE}" ]; then
    echo "[init] Vault reports initialized but ${CLUSTER_INIT_FILE} is missing." >&2
    fail "the unseal key cannot be recovered; delete the Vault data volume and start again."
  fi
}

run_operator_init() {
  log "Vault is uninitialized — running operator init ..."
  vault operator init -key-shares=1 -key-threshold=1 -format=json > "${CLUSTER_INIT_FILE}"
  chmod 600 "${CLUSTER_INIT_FILE}"
  log "Cluster initialized; keys stored in ${CLUSTER_INIT_FILE}."
}

# `operator init -format=json` pretty-prints across lines, so the payload is
# flattened first — otherwise the array values sit on a different line from
# their key and no single-line pattern can match them. Stripping spaces and
# newlines is safe: base64 key material and the hvs.* token contain neither.
load_key_material() {
  init_json_flat=$(tr -d ' \n\r' < "${CLUSTER_INIT_FILE}")
  UNSEAL_KEY=$(echo "${init_json_flat}" | grep -o '"unseal_keys_b64":\["[^"]*"' | sed 's/.*\["//; s/"$//')
  GENERATED_ROOT_TOKEN=$(echo "${init_json_flat}" | grep -o '"root_token":"[^"]*"' | sed 's/"root_token":"//; s/"$//')
  if [ -z "${UNSEAL_KEY}" ] || [ -z "${GENERATED_ROOT_TOKEN}" ]; then
    fail "could not parse the unseal key or root token from ${CLUSTER_INIT_FILE}."
  fi
}

# ---------------------------------------------------------------------------
# Step 3 — unseal if sealed
# ---------------------------------------------------------------------------
unseal_if_sealed() {
  vault status >/dev/null 2>&1 && seal_code=0 || seal_code=$?
  case "${seal_code}" in
    0) log "Vault is already unsealed." ;;
    2)
      log "Vault is sealed — unsealing ..."
      vault operator unseal "${UNSEAL_KEY}" >/dev/null
      log "Vault unsealed."
      ;;
    *) fail "could not read Vault seal status (exit ${seal_code})." ;;
  esac
}

# ---------------------------------------------------------------------------
# Step 4 — optionally mint a stable operator token
#
# `operator init` generates a root token that nothing outside the bootstrap
# volume knows. If an operator wants a token they can keep in their own
# password manager, VAULT_ROOT_TOKEN gives it a fixed ID. Left unset (the
# default), no standing root-policy token is created at all — the safer
# choice, since a root token is the one credential that can undo every policy
# below it. This never runs with a well-known default value.
# ---------------------------------------------------------------------------
ensure_operator_token() {
  [ -n "${VAULT_ROOT_TOKEN:-}" ] || { log "VAULT_ROOT_TOKEN not set — no standing operator token."; return 0; }
  if vault token lookup "${VAULT_ROOT_TOKEN}" >/dev/null 2>&1; then
    log "Stable operator token already present."
    return 0
  fi
  log "Creating stable root-policy token from VAULT_ROOT_TOKEN ..."
  vault token create \
    -id="${VAULT_ROOT_TOKEN}" \
    -policy=root \
    -display-name=local-operator \
    -period=768h >/dev/null
  log "Stable operator token created."
}

# ---------------------------------------------------------------------------
# Step 5 — audit device
#
# Records every request and response (secret values are HMAC'd, not written in
# clear). Enabled before any other configuration so the bootstrap itself is on
# record. NOTE for operators: once an audit device is on, Vault refuses
# requests if it cannot write to it — a full disk or a bad path stops the
# stack rather than letting activity go unrecorded. That is the intended trade.
# ---------------------------------------------------------------------------
ensure_audit_device() {
  if flat_audit | grep -q '"file/":'; then
    log "Audit device already enabled — skipping."
    return 0
  fi
  log "Enabling file audit device at ${VAULT_AUDIT_LOG_PATH} ..."
  vault audit enable file file_path="${VAULT_AUDIT_LOG_PATH}"
}

# ---------------------------------------------------------------------------
# Step 6 — enable the KV v2 secrets engine
#
# Checked rather than attempted-and-ignored: swallowing every error from
# `secrets enable` would also swallow a permission failure and report it as
# "already enabled".
# ---------------------------------------------------------------------------
ensure_kv_engine() {
  if flat_secrets | grep -q "\"${VAULT_MOUNT_PATH}/\":"; then
    log "Secrets engine already mounted at '${VAULT_MOUNT_PATH}/' — skipping."
    return 0
  fi
  log "Enabling KV v2 secrets engine at '${VAULT_MOUNT_PATH}/' ..."
  vault secrets enable -path="${VAULT_MOUNT_PATH}" -version=2 kv
}

# ---------------------------------------------------------------------------
# Step 7 — render and write the read and write policies
#
# `vault policy write` replaces the policy each time, so a changed template or
# prefix takes effect on the next run without any manual cleanup.
# ---------------------------------------------------------------------------
write_policies() {
  log "Writing policy '${READ_POLICY}' (${VAULT_MOUNT_PATH}/data/${VAULT_KV_PREFIX}/*) ..."
  render_policy "${VAULT_POLICY_DIR}/service-read.hcl.tpl" | vault policy write "${READ_POLICY}" - >/dev/null
  log "Writing policy '${WRITE_POLICY}' ..."
  render_policy "${VAULT_POLICY_DIR}/service-write.hcl.tpl" | vault policy write "${WRITE_POLICY}" - >/dev/null
}

# ---------------------------------------------------------------------------
# Step 8 — enable userpass auth and create the accounts
#
# token_ttl / token_max_ttl bound how long a stolen token is useful. The
# application already re-logs-in before its token's lease runs out, so a short
# lease costs nothing at runtime. (AppRole or cloud IAM auth would remove the
# static password entirely; see the README's "Production" section.)
# ---------------------------------------------------------------------------
ensure_userpass() {
  if flat_auth | grep -q '"userpass/":'; then
    log "userpass auth already enabled — skipping."
    return 0
  fi
  log "Enabling userpass auth method ..."
  vault auth enable userpass
}

# Passwords are read from stdin (`password=-`) so they never appear in the
# process list of the container.
upsert_user() {
  log "Creating account '$1' with policy '$3' ..."
  printf '%s' "$2" | vault write "auth/userpass/users/$1" \
    password=- \
    policies="$3" \
    token_ttl="${VAULT_TOKEN_TTL}" \
    token_max_ttl="${VAULT_TOKEN_MAX_TTL}" >/dev/null
}

create_accounts() {
  upsert_user "${VAULT_SERVICE_USERNAME}" "${VAULT_SERVICE_PASSWORD}" "${READ_POLICY}"
  if [ -n "${VAULT_ADMIN_USERNAME:-}" ]; then
    upsert_user "${VAULT_ADMIN_USERNAME}" "${VAULT_ADMIN_PASSWORD}" "${WRITE_POLICY}"
  fi
}

# ---------------------------------------------------------------------------
# main — the steps above, in dependency order
# ---------------------------------------------------------------------------
main() {
  validate_environment
  READ_POLICY="${VAULT_POLICY_BASENAME}-read"
  WRITE_POLICY="${VAULT_POLICY_BASENAME}-write"

  wait_for_vault
  migrate_legacy_key_file
  initialize_if_needed
  load_key_material
  unseal_if_sealed

  # Every command from here on runs as the init-generated root token.
  export VAULT_TOKEN="${GENERATED_ROOT_TOKEN}"

  ensure_audit_device
  ensure_operator_token
  ensure_kv_engine
  write_policies
  ensure_userpass
  create_accounts

  log "Vault bootstrap complete."
  log "Login: ${VAULT_ADDR}/v1/auth/userpass/login/${VAULT_SERVICE_USERNAME}"
}

main
