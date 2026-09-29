# User services

## The short version

This package manages two related but different things:

1. **User accounts** — identity, profile, platform role, status, and password
   hash.
2. **User entitlements** — exact permission and credential records used when a
   user invokes one tenant deployment.

A user account answers “who is this person on the platform?” An entitlement
answers “which exact provider/model credential may this person use for this
tenant route?”

## Vocabulary

| Term | Plain-language meaning |
| --- | --- |
| User | A platform identity that can sign in and receive roles. |
| Platform role | A global privilege level such as developer, operator, admin, or owner. |
| Account status | Whether a user is active or suspended. |
| Password hash | A one-way value used to verify a password without storing the password itself. |
| Entitlement | An exact user-specific grant for a tenant deployment, provider, model, and credential reference. |
| Deployment credential | The secret already configured on the tenant deployment. |
| Personal override | A credential supplied specifically for one user's entitlement. |
| Route invalidation | Making cached authorization for one tenant/user/deployment tuple stale. |

## User account lifecycle

`UserService` creates, lists, counts, reads, updates, suspends, activates, and
deletes platform users.

During creation:

```text
Validated plaintext password
    -> hash in a worker thread
    -> persist only the encoded hash
    -> remove password fields from returned row
```

The password is represented by Pydantic `SecretStr` at the request boundary and
is read only when hashing. Hashing is CPU-expensive by design, so it runs in a
worker thread rather than blocking the asynchronous event loop.

Profile updates deliberately do not change passwords. Password rotation needs
its own security flow instead of being hidden inside an ordinary profile
PATCH.

## Password hashing

`password_hashing.py` is shared by user creation and sign-in. One module owns
the format so the writer and verifier cannot drift apart.

The stored format is:

```text
pbkdf2_sha256$<iterations>$<salt hex>$<derived key hex>
```

- the random salt prevents equal passwords from producing equal stored values;
- the iteration count makes guessing expensive;
- parameters travel with the hash so older rows remain verifiable after future
  cost changes;
- `hmac.compare_digest()` avoids timing differences from ordinary equality;
- malformed stored hashes fail authentication instead of crashing the service.

Never log plaintext passwords, encoded hashes, salts, or derived keys.

## What an entitlement represents

An entitlement is not a general preference and routing does not search for one.
Authorization approves one exact `entitlement_id`, and inference routing later
re-reads that same record.

An entitlement must agree with its source deployment:

- tenant and deployment key identify the source route;
- provider ID must match the deployment;
- model ID must match the deployment.

This prevents an administrator from combining a deployment key with an
unrelated provider or model.

## Credential inheritance and overrides

When creating an entitlement:

- if no credential is supplied, it inherits the deployment's existing secret
  reference;
- if a credential is supplied, it is written to its own user-entitlement path
  and the entitlement stores that new reference.

The service never copies plaintext secrets out of the secret backend. For AWS
SigV4, the inherited reference may be the `iam:default` marker used for ambient
credentials.

## Entitlement authorization and invalidation

- Creation requires tenant-admin access for the target tenant.
- Listing/counting requires both self-or-platform-admin access for the target
  user and tenant-read access.
- Getting one entitlement verifies user ownership and then tenant-read access.
- Updating/deleting first loads the existing entitlement and requires
  tenant-admin access for its current tenant.

After create, update, or delete, the service invalidates exactly:

```text
(tenant_id, user_id, deployment_key)
```

That prevents an old cached authorization decision from surviving an
entitlement or credential change.

## Errors and safe output

- Missing users, deployments, or entitlements raise
  `ResourceNotFoundError`.
- Provider/model mismatches and invalid writes become typed management errors.
- Persistence errors are translated rather than exposed directly.
- All returned rows are copied through `clean_row()` or `clean_rows()` so
  password hashes and secret references do not escape the service layer.

## File map

1. [`user.py`](user.py) — platform-user lifecycle and password hashing during
   creation.
2. [`entitlement.py`](entitlement.py) — user-specific routing grants,
   credential inheritance/overrides, and route invalidation.
3. [`password_hashing.py`](password_hashing.py) — the shared PBKDF2 encoding,
   verification, and unknown-user timing defense.
4. [`__init__.py`](__init__.py) — package overview.
5. [`../authentication/sign_in_service.py`](../authentication/sign_in_service.py)
   — consumes password verification during sign-in.
6. [`../../inference_routing/README.md`](../../inference_routing/README.md) —
   explains how an approved entitlement becomes an execution route.
7. [`../management_helpers.py`](../management_helpers.py) — row cleaning and
   persistence-error translation.

The matching HTTP routers are
[`../../api/management_routers/user_router.py`](../../api/management_routers/user_router.py)
and
[`../../api/management_routers/entitlement_router.py`](../../api/management_routers/entitlement_router.py).

## Rules to preserve

1. Store only password hashes, never plaintext passwords.
2. Keep the hash writer and verifier in the same module.
3. Never return password hashes or secret references from service methods.
4. Keep password changes out of ordinary profile updates.
5. Require entitlement provider/model values to match the source deployment.
6. Never let routing substitute a different entitlement.
7. Invalidate the exact authorization route after every entitlement mutation.

