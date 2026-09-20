"""Start and verify the sibling llm_token_manager stack.

Architecture:
    LocalInfrastructureManager -> TokenManagerStack -> llm_token_manager/infra/scripts/*

Why this module exists
    Every inference call (chat, embed, rerank) reserves capacity from
    llm_token_manager before a provider is dialled, so a developer who starts
    only llm_services gets a working management API and a Playground that fails
    with "Token manager is unreachable." on the first prompt. That gap is not
    discoverable from anything llm_services prints, so `start` now brings the
    sibling stack up as well.

Why it delegates instead of running Compose itself
    llm_token_manager owns a five-file Compose chain, generates a PgBouncer
    userlist before booting, and runs its own preflight. Reimplementing any of
    that here would create a second definition of "started" that drifts. This
    module therefore does only the two things llm_services is uniquely
    positioned to do — reconcile the shared `.env` values, and invoke the
    documented entry point — and treats the sibling's own script as the
    authority for everything else.

Why failures are advisory
    The two stacks are separate repositories with separate lifecycles. A
    missing or broken sibling must not prevent llm_services from starting:
    management endpoints, the dashboard, and schema work are all still useful.
    Every failure here therefore prints the manual steps and lets the caller
    continue.
"""

from __future__ import annotations

import platform
import subprocess
from typing import TYPE_CHECKING
from urllib.error import URLError
from urllib.request import urlopen

from infrastructure.local_stack import console
from infrastructure.local_stack.constants import (
    TOKEN_MANAGER_HEALTH_URL,
    TOKEN_MANAGER_ROOT,
    TOKEN_MANAGER_SHARED_KEYS,
)
from infrastructure.local_stack.environment import parse_env_value

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


class TokenManagerStack:
    """Reconcile shared configuration and drive the sibling stack's own tooling."""

    def __init__(self, root: Path = TOKEN_MANAGER_ROOT) -> None:
        """Bind to the sibling repository root, which need not exist."""
        self.root = root
        self.env_file = root / ".env"
        self.powershell_script = root / "infra" / "scripts" / "infra.ps1"
        self.shell_script = root / "infra" / "scripts" / "infra.sh"

    @property
    def is_available(self) -> bool:
        """Return whether the sibling repository is present and startable."""
        return (
            self.root.is_dir()
            and self.env_file.is_file()
            and (self.powershell_script.is_file() or self.shell_script.is_file())
        )

    def is_healthy(self, timeout_seconds: float = 2.0) -> bool:
        """Return whether the token-manager API answers its health endpoint."""
        try:
            with urlopen(TOKEN_MANAGER_HEALTH_URL, timeout=timeout_seconds) as response:
                return bool(200 <= response.status < 300)
        except (URLError, OSError, ValueError):
            return False

    def synchronize_shared_values(self, services_values: Mapping[str, str]) -> tuple[str, ...]:
        """Copy the values both stacks must agree on into the sibling `.env`.

        The database trio lets the token manager read llm_services' database,
        and the JWT secret lets it verify the service token llm_services signs.
        A mismatch in either is silent until an inference call fails, so the
        values are reconciled every start rather than documented and hoped for.

        Returns:
            The sibling-side key names that were changed, for reporting.
        """
        desired = {
            target_key: services_values[source_key]
            for source_key, target_key in TOKEN_MANAGER_SHARED_KEYS.items()
            if services_values.get(source_key)
        }
        return update_env_values(self.env_file, desired)

    def start(self) -> None:
        """Run the sibling repository's documented startup entry point.

        Raises:
            FileNotFoundError: No usable launcher script was found.
            subprocess.CalledProcessError: The sibling's own tooling failed.
        """
        command = self.launcher_command("up")
        console.step(f"Running {' '.join(command)}")
        subprocess.run(command, cwd=self.root, check=True)

    def launcher_command(self, action: str) -> list[str]:
        """Return the platform-appropriate invocation of the sibling launcher.

        PowerShell is preferred on Windows because infra.ps1 is that
        repository's primary script; the shell twin is behaviourally identical
        and is used everywhere else.
        """
        if platform.system() == "Windows" and self.powershell_script.is_file():
            return [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.powershell_script),
                action,
            ]
        if self.shell_script.is_file():
            return ["bash", str(self.shell_script), action]
        raise FileNotFoundError(
            f"No token-manager launcher found under {self.root / 'infra' / 'scripts'}."
        )

    def print_manual_steps(self) -> None:
        """Explain how to start the sibling stack by hand, and what breaks until then."""
        console.warning("llm_token_manager is not running; inference calls will fail with")
        console.warning('  "Token manager is unreachable."  (management endpoints still work)')
        print("\nTo start it:")
        if self.root.is_dir():
            print(f"  1. cd {self.root}")
            print("  2. Windows:       .\\infra\\scripts\\infra.ps1 doctor")
            print("                    .\\infra\\scripts\\infra.ps1 up")
            print("     Linux/macOS:   sh infra/scripts/infra.sh doctor")
            print("                    sh infra/scripts/infra.sh up")
            print("  3. Re-run `python -m infrastructure verify` to confirm.")
        else:
            print(f"  1. Clone llm_token_manager next to this repository ({self.root}).")
            print("  2. Copy its .env.example to .env.")
            print("  3. Re-run `python -m infrastructure start`, which syncs the shared")
            print("     database credentials and JWT secret, then starts it.")
        print(f"\nIts README: {self.root / 'infra' / 'README.md'}\n")


def update_env_values(path: Path, values: Mapping[str, str]) -> tuple[str, ...]:
    """Set keys in an existing `.env` in place, leaving every other byte alone.

    This edits a file owned by another repository, so it is deliberately
    conservative: only the right-hand side of an already-present key is
    rewritten, comments and ordering survive, and each line keeps its original
    terminator. That last detail matters more than it looks — the sibling
    `.env` uses CRLF, and a value that gains or loses a carriage return becomes
    a password that is wrong in a way no editor will show you.

    Keys that are absent entirely are appended under a marked section.

    Returns:
        The keys whose value actually changed, in file order then append order.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Environment file not found: {path}")

    # newline="" on both the read and the write disables universal-newline
    # translation, so each line keeps the terminator it already had. Reading
    # with the default instead would turn every CRLF into LF in memory and
    # rewrite the entire file on the next save — a diff touching every line of
    # a file this repository does not own.
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = handle.readlines()
    pending = dict(values)
    changed: list[str] = []

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in pending:
            continue
        desired = pending.pop(key)
        if parse_env_value(stripped.split("=", 1)[1]) == desired:
            continue
        body = line.rstrip("\r\n")
        terminator = line[len(body) :]
        lines[index] = f"{key}={desired}{terminator}"
        changed.append(key)

    if pending:
        # Append in the file's own line ending so a CRLF file stays CRLF
        # throughout rather than becoming mixed.
        ending = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines.append(ending)
        lines.append(f"{ending}# Values synchronized from llm_services/.env.{ending}")
        lines.extend(f"{key}={value}{ending}" for key, value in pending.items())
        changed.extend(pending)

    if changed:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write("".join(lines))
    return tuple(changed)
