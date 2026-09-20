# Vault ACL policy template: <VAULT_POLICY_BASENAME>-write
#
# Rendered by scripts/init.sh from VAULT_MOUNT_PATH and VAULT_KV_PREFIX; see
# service-read.hcl.tpl for why the path is derived rather than hardcoded.
#
# Grants a separate, admin-only identity write access to the namespace the read
# identity can read. Deliberately split from the runtime identity: the hot path
# that serves requests never gains write capability, so a compromised request
# cannot plant or overwrite a credential. Give this identity only to the
# component that creates credentials (an admin/management API), never to
# whatever reads them.
#
# Path convention: __MOUNT__/data/__PREFIX__/<anything you choose>
#
# No delete or list: rotation always writes a new KV version instead of
# removing history, and this identity never needs to enumerate secrets.

path "__MOUNT__/data/__PREFIX__/*" {
  capabilities = ["create", "update"]
}
