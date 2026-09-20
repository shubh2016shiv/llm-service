# Vault ACL policy template: <VAULT_POLICY_BASENAME>-read
#
# Rendered by scripts/init.sh, which replaces the two placeholders below from
# VAULT_MOUNT_PATH and VAULT_KV_PREFIX. Deriving the path from the same
# variables the application uses to build its KV paths is what keeps policy
# and client in agreement: hardcoding the prefix here meant changing
# VAULT_KV_PREFIX made the app write somewhere the policy silently denied.
#
# Grants the runtime service read-only access to its own secret namespace. It
# can never write or delete, and cannot see anything outside this prefix.
#
# Path convention: __MOUNT__/data/__PREFIX__/<anything you choose>
#
# KV v2 note: reads go through the /data/ sub-path; metadata is granted
# separately, and only for listing, so the service can enumerate its own
# secrets without being able to read another namespace's.

path "__MOUNT__/data/__PREFIX__/*" {
  capabilities = ["read"]
}

path "__MOUNT__/metadata/__PREFIX__/*" {
  capabilities = ["list"]
}
