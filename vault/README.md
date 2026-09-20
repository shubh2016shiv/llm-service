# Vault (single machine, self-hosted)

A self-contained way to run HashiCorp Vault on a machine you control, with
**persistent secrets, split read/write identities, an audit trail, and a
bootstrap that is safe to rerun**. Nothing project-specific is hardcoded: names,
mount, and prefix all arrive as environment variables, so the same three files
work for any project.

> **Read [What is and is not production-grade](#what-is-and-is-not-production-grade)
> before you rely on this for real secrets.** It is hardened for one machine. It
> is not a multi-host deployment, and it says exactly where it stops.

## Quickstart (five minutes)

```sh
# 1. Create your .env and set the two passwords (compose refuses to start without them)
cp .env.example .env            # then edit VAULT_SERVICE_PASSWORD and VAULT_ADMIN_PASSWORD
                                # generate values with:  openssl rand -base64 24

# 2. Start Vault and run the bootstrap
docker compose up -d vault vault-init

# 3. Confirm it worked: vault-init should have exited 0
docker compose logs vault-init | tail -3      # ... "Vault bootstrap complete."
```

In this repository, `python -m infrastructure` already generates a `.env` with
random passwords, so step 1 is only needed when you start Compose by hand.

Open the UI at <http://127.0.0.1:8200>. Log in with **Method: Username**, the
`VAULT_ADMIN_USERNAME` account can create secrets; the service account can read
them. To try it from the shell, see [Verify it](#verify-it).

## What's in this folder

```text
vault/
├── config/vault.hcl                 Server config: file storage, no TLS, mlock off
├── policies/service-read.hcl.tpl    Read-only policy template
├── policies/service-write.hcl.tpl   Create/update-only policy template
└── scripts/init.sh                  Idempotent bootstrap (init, unseal, audit, mount, policies, users)
```

It also needs two Compose services and three volumes, which live in your
`docker-compose.yml`, not in this folder:

| Compose piece | Role |
|---|---|
| `vault` service | Runs the server. Mounts `vault_data` (secrets) and `vault_audit` (audit log) |
| `vault-init` service | Runs `init.sh` once and exits. Mounts `vault_bootstrap` (the unseal key) |
| `vault_data` volume | The encrypted secret store |
| `vault_bootstrap` volume | The unseal key and init root token. **Only `vault-init` mounts it** |
| `vault_audit` volume | The audit log |

## How it works

A new Vault server is not usable: it starts **uninitialized**, and after every
restart it comes up **sealed** (encrypted, and refusing to serve until given a
key). It also has no secret store, no permissions, and no accounts. `init.sh`
fixes all of that automatically each time the stack starts.

Every step **checks whether it is already done, then acts only if it is not**.
That is why rerunning it against a Vault that holds real secrets changes
nothing. When you add a step, keep that shape.

1. Wait for the API (sealed or uninitialized both count as reachable).
2. Initialize on first run; reuse the stored key afterwards. An unseal key left
   on the data volume by an older version is moved to the bootstrap volume.
3. Unseal if sealed.
4. Optionally mint a stable operator token whose ID is `VAULT_ROOT_TOKEN`.
5. Turn on the file **audit device**, so every later step is on record.
6. Enable the KV v2 engine at `VAULT_MOUNT_PATH`.
7. Render the two policy templates and write them.
8. Enable `userpass` and create a **read-only** and a **write-only** account,
   each with a bounded token lifetime.

It deliberately does **not** seed secret values: a placeholder credential in a
real store looks exactly like a real one to the code reading it.

### The two identities

| Account | Policy | Can | Cannot |
|---|---|---|---|
| Service (`VAULT_SERVICE_USERNAME`) | `<basename>-read` | read and list under the prefix | write, delete, or see other prefixes |
| Admin (`VAULT_ADMIN_USERNAME`, optional) | `<basename>-write` | create and update under the prefix | read, delete, or list |

Give the service account to whatever serves requests, and the admin account only
to the component that creates credentials. A compromised request path then
cannot plant or overwrite a secret.

### Why the policy path is derived, not written by hand

The policy paths come from `VAULT_MOUNT_PATH` and `VAULT_KV_PREFIX` — the same
values your application uses to build its secret paths. If a policy hardcodes the
prefix while the app reads it from configuration, changing the variable makes the
app write somewhere the policy silently denies, and nothing tells you why.

The app-side contract is simply that a secret reference resolves to
`<VAULT_MOUNT_PATH>/data/<VAULT_KV_PREFIX>/<reference>`.

## Environment contract

| Variable | Required | Meaning |
|---|---|---|
| `VAULT_ADDR` | yes | API address inside the Compose network, e.g. `http://vault:8200` |
| `VAULT_KV_PREFIX` | yes | Path prefix every secret lives under, and the prefix the policies grant |
| `VAULT_POLICY_BASENAME` | yes | Policies are named `<basename>-read` / `<basename>-write` |
| `VAULT_SERVICE_USERNAME` / `VAULT_SERVICE_PASSWORD` | yes | The read-only account. **No default password**; compose refuses to start without one |
| `VAULT_ADMIN_USERNAME` / `VAULT_ADMIN_PASSWORD` | no | The write-only account; set **both or neither** |
| `VAULT_MOUNT_PATH` | no (`secret`) | KV v2 mount |
| `VAULT_TOKEN_TTL` | no (`1h`) | Lifetime of a login token |
| `VAULT_TOKEN_MAX_TTL` | no (`24h`) | Hard cap on a token, even if renewed |
| `VAULT_ROOT_TOKEN` | no (unset) | Fixed ID for a standing root-policy token. **Unset means none exists** |
| `VAULT_AUDIT_LOG_PATH` | no (`/vault/logs/audit.log`) | Audit file, a path **inside the server container** |
| `VAULT_POLICY_DIR` | no (`/policies`) | Where the `.tpl` files are mounted |
| `VAULT_CLUSTER_INIT_FILE` | no (`/vault/bootstrap/cluster-init.json`) | Where the unseal key is stored |

The script refuses to start on a missing required value, half an admin account,
or a bad value. Mount, prefix and basename may contain only letters, digits and
`. _ - /` (no leading or trailing `/`, no `..`), because they are substituted
into policy text and used as paths — a value like `../` must not be able to
widen a policy. Durations must be a number plus `s`, `m` or `h`, because Vault
reads a bare `24` as 24 **seconds**.

## Reusing it in another project

1. **Copy this folder** (`vault/`) into the new repository.
2. **Copy two services and three volumes** into its `docker-compose.yml`. Take
   them from this repository's `docker-compose.yml`: the `vault` and
   `vault-init` services, and `vault_data`, `vault_bootstrap`, `vault_audit`.
3. **Pick names** — nothing else to edit:

   ```yaml
   VAULT_KV_PREFIX: acme-billing
   VAULT_MOUNT_PATH: secret
   VAULT_POLICY_BASENAME: billing-app
   VAULT_SERVICE_USERNAME: billing-reader
   VAULT_ADMIN_USERNAME: billing-writer
   ```

   Also give the volumes project-specific `name:` values, or two projects on one
   machine will share a Vault's storage.
4. **Make the app use the same prefix and mount**, and log in as the service
   account (`POST /v1/auth/userpass/login/<username>`).
5. **Start it and verify** (below).

The Compose details below each cost real debugging time; keep them:

- `SKIP_SETCAP: "true"` on the server. The image's `setcap` call hangs on Docker
  Desktop's filesystem, and the capability only exists to allow `mlock`, which
  `vault.hcl` disables anyway.
- `command: ["server"]` with **no** `-config` flag. The entrypoint already adds
  `-config=/vault/config`; passing it again loads the listener twice and Vault
  fails to bind to itself.
- The health check must accept sealed and uninitialized states
  (`?standbyok=true&sealedcode=200&uninitcode=200`). `vault status` exits
  non-zero while sealed, but sealed is exactly the state `vault-init` needs to
  connect to in order to unseal it.
- `ports: "127.0.0.1:8200:8200"`. Containers on the Compose network reach Vault
  as `http://vault:8200` and ignore this mapping; it only controls who on the
  host network can reach the API. Widening it without TLS sends passwords and
  tokens in clear text.
- `${VAR:?message}` for passwords, not `${VAR:-default}`. A default is a
  password everyone who has read the repo knows.

Anything that waits for Vault to be *usable* (as opposed to reachable) should
depend on `vault-init` with `condition: service_completed_successfully`.

## Verify it

```sh
# Both accounts can log in
vault write auth/userpass/login/<service-user> password=...

# Read account: read ok, write denied
vault kv get -mount=<mount> <prefix>/anything
vault kv put -mount=<mount> <prefix>/anything k=v      # -> permission denied

# Write account: write ok, read denied
vault kv put -mount=<mount> <prefix>/anything k=v      # -> ok
vault kv get -mount=<mount> <prefix>/anything          # -> permission denied

# Neither can see a different prefix
vault kv get -mount=<mount> some-other-prefix/x        # -> permission denied
```

Rerun `docker compose up --force-recreate vault-init` at any time; it should
report "already initialized / already unsealed / skipping" and exit 0.

## Operating it

| Task | How |
|---|---|
| **Read the audit log** | `docker exec llm_vault tail -f /vault/logs/audit.log`. One JSON line per request; secret values are HMAC'd, never in clear |
| **Rotate a password** | Change the value in `.env`, restart the app, then `docker compose up --force-recreate vault-init`. It rewrites the account |
| **Change token lifetime** | Set `VAULT_TOKEN_TTL` / `VAULT_TOKEN_MAX_TTL`, rerun `vault-init`. The app re-logs-in on its own before expiry |
| **Change the prefix** | Change `VAULT_KV_PREFIX` in both the app and Compose, then rerun `vault-init` so the policies follow |
| **Back up** | Back up `vault_data` and `vault_bootstrap` **to different places**. The data is useless without the key, and the key is a master key to the data |
| **Restore** | Restore both volumes, then `docker compose up -d vault vault-init` |
| **Restart the host** | Nothing to do: Vault starts sealed and `vault-init` unseals it. Anything that needs secrets should depend on `vault-init` completing |

> **Audit device caveat.** With an audit device on, Vault refuses requests it
> cannot log. A full disk or an unwritable log path stops the stack rather than
> letting activity go unrecorded. That is intended; watch the `vault_audit` volume
> size.

## What is and is not production-grade

Done here, and worth keeping in any environment:

| Practice | How it is done here |
|---|---|
| Least privilege | Separate read-only and write-only accounts, each scoped to one prefix |
| No default credentials | Compose refuses to start without passwords; no default root token exists |
| Audit trail | File audit device enabled first, before any other bootstrap step |
| Bounded token lifetime | `VAULT_TOKEN_TTL` / `VAULT_TOKEN_MAX_TTL` set on every account |
| Key kept apart from data | Unseal key on `vault_bootstrap`, which the server never mounts |
| Reduced exposure | API published to loopback only |
| Input validation | Names, paths and durations validated before they reach a policy or Vault |
| Safe reruns | Every step checks before it acts |

**Not done, because a single machine cannot do it honestly.** Each of these
needs something this setup does not have:

| Gap | Why it remains | What closes it |
|---|---|---|
| One unseal key, stored on disk | The stack must recover from a restart with nobody present, and there is no second party or KMS to hold a key. Anyone who can read **both** volumes can unlock Vault | Cloud-KMS auto-unseal (a `seal "azurekeyvault"`, `"awskms"` or `"gcpckms"` stanza in `vault.hcl`): the key never touches disk |
| No TLS | Needs a certificate authority the app trusts. Loopback binding limits exposure, but does not encrypt traffic | Certificates on the `listener` stanza, an `https://` `VAULT_ADDR`, and the CA in the app's trust store |
| File storage | Raft is the supported storage for anything you cannot rebuild, but switching an existing volume needs `vault operator migrate` | Integrated Raft storage, ideally 3+ nodes |
| `userpass` static passwords | The app's Vault client logs in with userpass only | AppRole, Kubernetes, or cloud-IAM auth with short-lived identities. This needs a client change, not just config |
| Standing root token if `VAULT_ROOT_TOKEN` is set | Convenient for an operator, and a permanent skeleton key | Leave it unset; generate a root token on demand with `vault operator generate-root` |
| `mlock` off | The container is not granted `IPC_LOCK` | Run with the capability on a host where the image's `setcap` works, and enable mlock |

The policy templates carry over unchanged to a multi-host deployment — the
read/write split is the part worth keeping.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `docker compose up` stops with `Set VAULT_SERVICE_PASSWORD in .env` | The password is missing or empty. Set it in `.env` |
| `vault-init` exits `ERROR: required environment variable ... is not set` | A required variable is missing from the Compose `environment:` block |
| `ERROR: VAULT_KV_PREFIX='...' must use only ...` | The value contains a character outside the allowed set |
| `ERROR: VAULT_TOKEN_TTL='24' must be a number followed by s, m, or h` | Bare numbers mean seconds in Vault, so a unit is required |
| `Vault reports initialized but .../cluster-init.json is missing` | The data volume has secrets but the `vault_bootstrap` volume (the key) is gone. The key cannot be recovered; delete the Vault data volume and start again |
| App gets `permission denied` after changing the prefix | The app and `VAULT_KV_PREFIX` disagree, or `vault-init` has not rerun since the change |
| App gets `permission denied` at login after changing a password | The app and `vault-init` read different values, or `vault-init` has not rerun since the change |
| Vault container never becomes healthy on Docker Desktop | Missing `SKIP_SETCAP: "true"` |
| `address already in use` inside the Vault container | `-config` passed explicitly in `command:` |
| Every request fails with an audit error | The audit log cannot be written: disk full, or `VAULT_AUDIT_LOG_PATH` is not writable by the `vault` user |
| Vault is sealed after a Docker restart | Expected: it starts sealed every time. `vault-init` unseals it |
