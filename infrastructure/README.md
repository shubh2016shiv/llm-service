# Local Infrastructure Control Plane

This package provides one safe workflow for PostgreSQL, Redis, Vault, and FastAPI on
Windows, Linux, and macOS. Docker Compose owns the platform differences; the
Python control plane performs validation, initialization, readiness checks,
Vault bootstrap, and ordered schema application.

```text
infrastructure/
├── README.md          Operator guide
├── manage.ps1         Windows launcher
├── manage.sh          Linux/macOS launcher
├── local_stack/       Lifecycle and preflight implementation
└── diagnostics/       Optional deep service diagnostics
```

## Prerequisites

- Python 3.12 or newer
- Docker Desktop on Windows/macOS, or Docker Engine with Compose v2 on Linux
- The current user must be allowed to communicate with the Docker engine

No host installation of PostgreSQL, Redis, Vault, `psql`, or `redis-cli` is
required. Native clients run inside their containers.

## First run

From the `llm_services` directory:

```text
python -m infrastructure check
python -m infrastructure start
python -m infrastructure verify
python -m infrastructure connection-info
```

The first command is read-only. `start` runs the same preflight checks before it
creates missing `.env` values or starts containers.

`start` brings up the whole local platform, in this order:

1. Preflight (refuses to continue on any failure).
2. Reclaim the fixed host ports, **asking before it stops or kills anything**.
3. Create only the missing `.env` values.
4. PostgreSQL, Redis, Vault — each waited for individually.
5. Vault bootstrap (policies, auth, unseal).
6. The ordered SQL schema, with `ON_ERROR_STOP=1`.
7. Seed the `dashboard-owner` user that the printed JWT belongs to.
8. Build and start the FastAPI app, then wait for `/health`.
9. Start the sibling **llm_token_manager** stack.

Step 9 is not optional and has no flag: chat, embed, and rerank all reserve
capacity from that service first, so without it the API returns
`Token manager is unreachable.` on the first prompt — long after a developer
believes setup finished. If the sibling repository is missing or fails to
start, `start` prints the manual steps and continues; everything else in this
stack is still usable for management and schema work.

Platform launchers are also available:

```powershell
# Windows PowerShell
.\infrastructure\manage.ps1 check
.\infrastructure\manage.ps1 start
```

```sh
# Linux or macOS
sh infrastructure/manage.sh check
sh infrastructure/manage.sh start
```

Both launchers use `uv` when available and otherwise use the platform Python.

## Commands

| Command | Behavior |
|---|---|
| `check` | Read-only checks for OS, Python, project files, `.env`, schema manifest, Docker, Compose configuration, daemon access, and port conflicts. |
| `init` | Appends only missing `.env` defaults and randomly generated local secrets. Existing values are never overwritten. |
| `start` | The nine-step workflow above, ending with the sibling token manager. |
| `verify` | Runs `pg_isready`, `redis-cli ping`, and `vault status` inside the running containers, then reports token-manager health. |
| `status` | Displays Compose state and connection details without modifying `.env`. Use `--hide-secrets` to mask credentials. |
| `connection-info` | Prints the running stack's ports, usernames, passwords, Vault tokens, URLs, and a fresh dashboard JWT. |
| `stop` | Stops containers while preserving named volumes. |
| `restart` | Runs `stop`, then the complete validated `start` workflow. |
| `schema` | Validates and applies the ordered PostgreSQL schema manifest. |
| `connect postgres` | Opens `psql` inside the PostgreSQL container. |
| `connect redis` | Opens `redis-cli` inside the Redis container. |
| `reset --confirm-delete-volumes` | Deletes containers and named volumes. This permanently removes local data. |

### Global options

| Option | Effect |
|---|---|
| `--timeout SECONDS` | Readiness deadline for each service (default 90). |
| `--yes` | Pre-approve port reclaim. Required for non-interactive runs, which otherwise decline every destructive prompt and stop with instructions. |

`init`, `start`, `restart`, `status`, and `connection-info` print a connection table containing the
local ports, usernames, databases, passwords, tokens, and ready-to-copy URLs.
Use `--hide-secrets` when the terminal output will be recorded or shared.

The output also includes `FASTAPI_URL`, `FASTAPI_HEALTH_URL`, and
`FASTAPI_DOCS_URL`. The `DASHBOARD_JWT_TOKEN` value is a raw access token ready to paste into the
Lantern connection dialog. Infrastructure generates it independently from the
backend application using the infra-managed `JWT_SECRET_KEY`. The associated
local identity and role are persisted as `LOCAL_DASHBOARD_JWT_USER_ID` and
`LOCAL_DASHBOARD_JWT_ROLE` in `.env`; change them if the token needs to match a
specific seeded user. Tenant membership and deployment entitlements are still
enforced by the API for Playground inference calls.

Use `--timeout SECONDS` before the command to change the readiness deadline:

```text
python -m infrastructure --timeout 180 start
```

## What `check` validates

The report collects all safe findings instead of stopping at the first error:

1. Supported operating system and Python version.
2. Required Compose, Vault bootstrap, and schema-manifest files.
3. `.env` syntax, duplicate keys, missing values, and write access.
4. Every schema manifest entry, including duplicates, missing files, and path traversal.
5. Docker CLI discovery, Docker engine access, and Compose v2 availability.
6. Fully interpolated Compose configuration.
7. Host ports `5432`, `6379`, and `8200`, distinguishing this project's running
   containers from conflicts that `start` will reclaim.

Warnings are safe conditions that `init` or `start` can resolve, such as a
missing `.env`. Failures block `start` before mutations occur and include a
specific remediation message.

## Safety behavior

- Existing `.env` values are preserved.
- Reclaiming a busy port **asks first**, naming the container, or the process
  name, PID, and command line, before anything is stopped or killed. Declining
  (or running without a terminal and without `--yes`) aborts with instructions
  instead of proceeding. The Docker daemon and this process itself can never be
  terminated, with or without `--yes`.
- Generated passwords and tokens are unique per machine.
- Values interpolated into SQL are validated at the `.env` boundary and passed
  to `psql` as variables, never as string-formatted statements.
- Credentials are URL-encoded when application connection URLs are derived.
- `status` is read-only and never initializes configuration.
- SQL paths cannot escape `postgres_schema/`.
- SQL execution uses `ON_ERROR_STOP=1`.
- Destructive volume removal requires an explicit confirmation flag.

## Optional deep diagnostics

Use these only when `verify` identifies a service that needs deeper inspection:

```text
python -m infrastructure.diagnostics.postgres
python -m infrastructure.diagnostics.redis
python -m infrastructure.diagnostics.vault
python -m infrastructure.diagnostics.database_persistence
```

The first three inspect connection metadata and exit non-zero on failure.

The fourth exercises every persistence class against the running database in
116 steps — CRUD, secret isolation, routing lookups, duplicate detection, and
input-validation guards — and is the only check that covers the persistence
layer against real PostgreSQL rather than fakes. It writes temporary rows with
a per-run suffix. Set `CLEANUP = True` at the top of the file to delete them
afterwards; the default leaves them for inspection.

## The `.env` file

`init` appends only the keys that are missing, under a marked section, and never
rewrites a value you already set. Two parsing rules match Docker Compose, and
matter when copying from `.env.example`:

- An unquoted value ends at the first `#` that follows whitespace, so
  `APP_ENVIRONMENT=development   # dev | prod` means `development`.
- A quoted value keeps everything inside the quotes, including any `#`.

A `#` with no space before it is part of the value, so passwords containing `#`
survive unchanged.

`start` also seeds a `dashboard-owner` user whose `user_id` is
`LOCAL_DASHBOARD_JWT_USER_ID`. That is what makes the printed
`DASHBOARD_JWT_TOKEN` work immediately: management writes check that the
caller's user exists in `users`, which a freshly minted token alone cannot
satisfy. The token is valid for `JWT_ACCESS_TOKEN_EXPIRE_HOURS` (1 by default,
and the API rejects anything longer than `JWT_MAX_TOKEN_AGE_SECONDS`), so rerun
`connection-info` when it expires.

## The sibling token manager

`start` reconciles the values both repositories must agree on before starting
llm_token_manager, writing them into `../llm_token_manager/.env`:

| This repository | llm_token_manager | Why |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `DATABASE_USER` / `DATABASE_PASSWORD` / `DATABASE_NAME` | It reads this stack's database; it has none of its own. |
| `JWT_SECRET_KEY` | `JWT_SECRET_KEY` | This service signs the internal token that one verifies. |

Only those lines are rewritten; comments, ordering, and line endings are left
byte-identical. A mismatch is otherwise invisible until an inference call fails
with `Token manager rejected service authentication`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `Port 5432 is held by <name> (PID n) and was left running` | Something outside this project owns the port. Stop it, or rerun with `--yes` to let the tooling terminate it. |
| A prompt answers itself with `n (not a terminal)` | Non-interactive shell. Pass `--yes` to pre-authorize. |
| `Token manager is unreachable.` on chat/embed/rerank | The sibling stack is not running. `python -m infrastructure verify` reports it, and `start` prints the manual steps. |
| `Token manager rejected service authentication` | `JWT_SECRET_KEY` differs between the two `.env` files. Rerun `start`, which resyncs it. |
| `verify` fails on Vault after a Docker restart | Vault starts sealed on every boot; `vault-init` unseals it. Run `start`. |
| `.env validation failed: duplicate keys` | The same key is assigned twice. Compose honours the last one; delete the earlier line. |
| `LOCAL_DASHBOARD_JWT_ROLE must be one of…` | That value must be `developer`, `operator`, `admin`, or `owner`. |
