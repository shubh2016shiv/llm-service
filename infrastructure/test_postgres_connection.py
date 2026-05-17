"""
PostgreSQL connection diagnostic for local development.

Reads credentials from the project .env file — no arguments required.
Run from anywhere inside the repository.

Usage:
    python infrastructure/test_postgres_connection.py

Exit codes:
    0  connection succeeded
    1  connection failed (reason printed to stdout)
"""

from __future__ import annotations

import asyncio
import platform
import subprocess
from pathlib import Path
from typing import Any

import _ansi

POSTGRES_HOST = "localhost"
POSTGRES_PORT = 5432


# ---------------------------------------------------------------------------
# Project root / .env
# ---------------------------------------------------------------------------

def _find_project_root() -> Path:
    try:
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(root)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return Path.cwd()


def _load_env(env_path: Path) -> dict[str, str]:
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        result[key.strip()] = raw.strip().strip("'\"")
    return result


# ---------------------------------------------------------------------------
# Port-conflict detection
# ---------------------------------------------------------------------------

def _pids_on_port(port: int) -> list[str]:
    try:
        if platform.system() == "Windows":
            out = subprocess.check_output(
                ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL,
            )
            pids: list[str] = []
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and f":{port}" in parts[1] and parts[3] == "LISTENING":
                    pids.append(parts[4])
            return list(dict.fromkeys(pids))
        else:
            out = subprocess.check_output(
                ["ss", "-tlnp", f"sport = :{port}"],
                text=True, stderr=subprocess.DEVNULL,
            )
            return [line.split()[-1] for line in out.splitlines()[1:] if line.strip()]
    except Exception:
        return []


def _warn_port_conflict(port: int) -> bool:
    pids = _pids_on_port(port)
    if len(pids) <= 1:
        return False

    _ansi.warn(
        f"{len(pids)} processes are bound to port {port}: PIDs {', '.join(pids)}\n"
        "\n"
        "  A native PostgreSQL installation is most likely intercepting connections\n"
        "  before Docker's port forwarder. The host will hit the native database,\n"
        "  not the Docker container.\n"
        "\n"
        "  Fix: stop the native service, then retry."
    )
    if platform.system() == "Windows":
        print("    PowerShell (run as Administrator):")
        print("      Stop-Service -Name postgresql*")
        print("      Set-Service -Name postgresql* -StartupType Disabled")
    else:
        print("    sudo systemctl stop postgresql && sudo systemctl disable postgresql")
    print()
    return True


# ---------------------------------------------------------------------------
# Async connection test
# ---------------------------------------------------------------------------

async def _connect(host: str, port: int, database: str, user: str, password: str) -> dict[str, Any]:
    try:
        import asyncpg
    except ImportError as exc:
        raise RuntimeError("asyncpg is not installed. Run: uv pip install asyncpg") from exc

    conn = await asyncpg.connect(
        host=host, port=port, database=database,
        user=user, password=password, timeout=5,
    )
    try:
        row = await conn.fetchrow(
            """
            SELECT
                current_user                         AS db_user,
                current_database()                   AS db_name,
                version()                            AS pg_version,
                inet_server_addr()::text             AS server_addr,
                inet_client_addr()::text             AS client_addr,
                (SELECT count(*)::int
                 FROM information_schema.tables
                 WHERE table_schema = 'public')      AS public_tables
            """
        )
    finally:
        await conn.close()

    return dict(row)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(_ansi.bold("PostgreSQL Connection Diagnostic"))
    print("================================")

    project_root = _find_project_root()
    env = _load_env(project_root / ".env")

    _ansi.header("Credentials  (.env)")
    user     = env.get("POSTGRES_USER", "llm_user")
    password = env.get("POSTGRES_PASSWORD", "")
    database = env.get("POSTGRES_DB", "llm_services")

    if not password:
        _ansi.fail("POSTGRES_PASSWORD is not set in .env")
        return 1

    print(f"  host      : {POSTGRES_HOST}:{POSTGRES_PORT}")
    print(f"  database  : {database}")
    print(f"  user      : {user}")
    print(f"  password  : {'*' * min(len(password), 8)}  ({len(password)} chars)")

    _ansi.header(f"Port {POSTGRES_PORT} listeners")
    conflict = _warn_port_conflict(POSTGRES_PORT)
    if not conflict:
        _ansi.ok("No port conflict detected.")

    _ansi.header("Connection test")
    print(f"  Connecting to {POSTGRES_HOST}:{POSTGRES_PORT}/{database} ...")
    try:
        info = asyncio.run(_connect(POSTGRES_HOST, POSTGRES_PORT, database, user, password))
    except Exception as exc:
        _ansi.fail(str(exc))
        if conflict:
            print()
            print("  The port conflict above is the most likely cause.")
            print("  Stop the native PostgreSQL service and retry.")
        return 1

    _ansi.header("Result")
    _ansi.ok("Connected successfully.")
    print()
    print(f"  PostgreSQL   : {info['pg_version']}")
    print(f"  Server addr  : {info['server_addr'] or '(unix socket)'}")
    print(f"  Client addr  : {info['client_addr']}")
    print(f"  User         : {info['db_user']}")
    print(f"  Database     : {info['db_name']}")
    print(f"  Public tables: {info['public_tables']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
