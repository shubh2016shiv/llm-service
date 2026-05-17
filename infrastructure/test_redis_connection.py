"""
Redis connection diagnostic for local development.

Reads credentials from the project .env file — no arguments required.
Run from anywhere inside the repository.

Usage:
    python infrastructure/test_redis_connection.py

Exit codes:
    0  connection succeeded
    1  connection failed (reason printed to stdout)
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import _ansi

REDIS_HOST = "localhost"
REDIS_PORT = 6379


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
# Async connection test
# ---------------------------------------------------------------------------

async def _connect(host: str, port: int, password: str) -> dict[str, Any]:
    try:
        import redis.asyncio as aioredis
    except ImportError as exc:
        raise RuntimeError("redis is not installed. Run: uv pip install redis") from exc

    client = aioredis.Redis(host=host, port=port, password=password, socket_timeout=5)
    try:
        await client.ping()
        info     = await client.info()
        keyspace = await client.info("keyspace")
    finally:
        await client.close()

    return {
        "redis_version":     info.get("redis_version"),
        "uptime_seconds":    info.get("uptime_in_seconds"),
        "used_memory":       info.get("used_memory_human"),
        "connected_clients": info.get("connected_clients"),
        "role":              info.get("role"),
        "databases":         {k: v for k, v in keyspace.items() if k.startswith("db")},
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(_ansi.bold("Redis Connection Diagnostic"))
    print("===========================")

    project_root = _find_project_root()
    env = _load_env(project_root / ".env")

    _ansi.header("Credentials  (.env)")
    password = env.get("REDIS_PASSWORD", "")

    if not password:
        _ansi.fail("REDIS_PASSWORD is not set in .env")
        return 1

    print(f"  host     : {REDIS_HOST}:{REDIS_PORT}")
    print(f"  password : {'*' * min(len(password), 8)}  ({len(password)} chars)")

    _ansi.header("Connection test")
    print(f"  Connecting to {REDIS_HOST}:{REDIS_PORT} ...")
    try:
        info = asyncio.run(_connect(REDIS_HOST, REDIS_PORT, password))
    except Exception as exc:
        _ansi.fail(str(exc))
        return 1

    _ansi.header("Result")
    _ansi.ok("Connected successfully.")
    print()
    print(f"  Redis version     : {info['redis_version']}")
    print(f"  Role              : {info['role']}")
    print(f"  Uptime            : {info['uptime_seconds']}s")
    print(f"  Used memory       : {info['used_memory']}")
    print(f"  Connected clients : {info['connected_clients']}")

    databases = info["databases"]
    if databases:
        print(f"  Databases         : {', '.join(str(k) for k in databases)}")
    else:
        print("  Databases         : (empty - no keys stored yet)")

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
