"""
HashiCorp Vault connection diagnostic for local development.

Reads credentials from the project .env file — no arguments required.
Run from anywhere inside the repository.

Usage:
    python infrastructure/test_vault_connection.py

Exit codes:
    0  connection succeeded and Vault is unsealed
    1  connection failed or Vault is sealed / unreachable
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import _ansi

VAULT_DEFAULT_ADDR = "http://localhost:8200"


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
# Async Vault checks
# ---------------------------------------------------------------------------

async def _check_health(addr: str) -> dict[str, Any]:
    """
    GET /v1/sys/health — Vault returns a body even for non-2xx responses
    (sealed, standby), so we parse regardless of status code.
    """
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is not installed. Run: uv pip install httpx") from exc

    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(f"{addr}/v1/sys/health")

    return response.json()


async def _authenticate_userpass(addr: str, username: str, password: str) -> dict[str, Any]:
    """Authenticate via the userpass auth method and return token metadata."""
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("httpx is not installed. Run: uv pip install httpx") from exc

    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            f"{addr}/v1/auth/userpass/login/{username}",
            json={"password": password},
        )
        response.raise_for_status()
        token = response.json()["auth"]["client_token"]

        lookup = await client.get(
            f"{addr}/v1/auth/token/lookup-self",
            headers={"X-Vault-Token": token},
        )
        lookup.raise_for_status()

    data = lookup.json()["data"]
    return {
        "token_prefix": token[:8] + "...",
        "policies":     data.get("policies", []),
        "ttl":          data.get("ttl"),
        "renewable":    data.get("renewable"),
        "display_name": data.get("display_name"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print(_ansi.bold("HashiCorp Vault Connection Diagnostic"))
    print("=====================================")

    project_root = _find_project_root()
    env = _load_env(project_root / ".env")

    _ansi.header("Credentials  (.env)")
    addr     = env.get("VAULT_ADDR", VAULT_DEFAULT_ADDR)
    username = env.get("VAULT_SERVICE_USERNAME") or env.get("VAULT_USERNAME", "")
    password = env.get("VAULT_SERVICE_PASSWORD") or env.get("VAULT_PASSWORD", "")

    if not username or not password:
        _ansi.fail("VAULT_SERVICE_USERNAME or VAULT_SERVICE_PASSWORD is not set in .env")
        return 1

    print(f"  address  : {addr}")
    print(f"  username : {username}")
    print(f"  password : {'*' * min(len(password), 8)}  ({len(password)} chars)")

    _ansi.header("Health check  (GET /v1/sys/health)")
    print(f"  Reaching {addr} ...")
    try:
        health = asyncio.run(_check_health(addr))
    except Exception as exc:
        _ansi.fail(f"Cannot reach Vault: {exc}")
        return 1

    initialized = health.get("initialized", False)
    sealed      = health.get("sealed", True)
    version     = health.get("version", "unknown")
    cluster     = health.get("cluster_name", "")

    print(f"  Version      : {version}")
    print(f"  Cluster      : {cluster or '(dev mode - no cluster name)'}")
    print(f"  Initialized  : {initialized}")
    print(f"  Sealed       : {sealed}")

    if not initialized:
        _ansi.fail("Vault is not initialized.")
        return 1

    if sealed:
        _ansi.fail("Vault is sealed. Run 'vault operator unseal' or restart in dev mode.")
        return 1

    _ansi.ok("Vault is reachable and unsealed.")

    _ansi.header(f"Authentication  (userpass / {username})")
    print(f"  Authenticating as '{username}' ...")
    try:
        token_info = asyncio.run(_authenticate_userpass(addr, username, password))
    except Exception as exc:
        _ansi.fail(f"Authentication failed: {exc}")
        return 1

    _ansi.header("Result")
    _ansi.ok("Authenticated successfully.")
    print()
    print(f"  Token (prefix)  : {token_info['token_prefix']}")
    print(f"  Display name    : {token_info['display_name']}")
    print(f"  Policies        : {', '.join(token_info['policies'])}")
    print(f"  TTL             : {token_info['ttl']}s")
    print(f"  Renewable       : {token_info['renewable']}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
