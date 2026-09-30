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
# Compensation may permanently remove a freshly minted, version-unique path
# when the owning PostgreSQL write fails. It cannot delete arbitrary metadata
# or list/read secret values. Older versions referenced by live rows are kept.

path "__MOUNT__/data/__PREFIX__/*" {
  capabilities = ["create", "update"]
}

path "__MOUNT__/metadata/__PREFIX__/tenant-deployments/+/+/versions/+" {
  capabilities = ["delete"]
}

path "__MOUNT__/metadata/__PREFIX__/user-entitlements/+/+/+/versions/+" {
  capabilities = ["delete"]
}
