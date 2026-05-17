# Vault ACL policy: llm-service-read
#
# Grants the LLM Provider Service read-only access to its own secret namespace.
# The service can never write, delete, or list secrets outside this path.
#
# Path convention:
#   secret/data/llm-provider-service/<tenant_id>/<provider_name>
#   e.g. secret/data/llm-provider-service/providers/openai/acme-corp
#
# KV v2 note: all reads go through the /data/ sub-path; the metadata
# sub-path is listed separately so the service can enumerate its own secrets.

path "secret/data/llm-provider-service/*" {
  capabilities = ["read"]
}

path "secret/metadata/llm-provider-service/*" {
  capabilities = ["list"]
}
