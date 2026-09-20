# Vault server configuration — local persistent mode
#
# Replaces `-dev` mode. Dev mode starts pre-unsealed with a fixed root token
# and keeps every secret in memory only, so a container restart silently
# destroyed every credential written through the management API since boot —
# leaving deployments pointing at Vault paths that no longer existed.
#
# The file storage backend below writes to /vault/file, which docker-compose
# backs with the named volume `vault_data`, so secrets now survive restarts.
# `/vault/file` specifically (rather than an arbitrary path) because the
# official image already creates it owned by the `vault` user, which is what
# lets an empty named volume inherit the correct ownership on first run.
#
# Consequence of leaving dev mode: this Vault now starts sealed and
# uninitialized like a real one, so vault/scripts/init.sh performs the
# init-if-needed and unseal steps dev mode used to do implicitly.

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr = "http://0.0.0.0:8200"
ui       = true

# Single machine you run yourself: no HA, and no cloud KMS for auto-unseal.
# init.sh unseals with one scripted key share so the stack recovers from a
# restart with nobody present. The key is kept on a different volume from this
# storage path (vault_bootstrap, not vault_data), but it is still on the same
# disk — anyone who can read both volumes can unlock Vault. Cloud-KMS
# auto-unseal, which removes that limit, is what a multi-host deployment adds
# here (a `seal "azurekeyvault" { ... }` stanza); see the README's Production
# section.
#
# mlock is off because the container is not granted IPC_LOCK (see SKIP_SETCAP
# in docker-compose.yml). Without mlock the OS may swap Vault's memory to disk,
# so on a machine with swap enabled, keep the disk encrypted.
disable_mlock = true
