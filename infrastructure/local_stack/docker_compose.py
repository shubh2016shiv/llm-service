"""Cross-platform subprocess adapter for Docker Compose v2."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from infrastructure.local_stack.errors import CommandFailedError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class DockerComposeClient:
    """Build and execute Docker Compose commands without shell-specific syntax."""

    def __init__(self, project_root: Path, compose_file: Path, env_file: Path) -> None:
        """Store explicit paths so commands work from any current directory."""
        self.project_root = project_root
        self.compose_file = compose_file
        self.env_file = env_file

    @staticmethod
    def docker_executable() -> str | None:
        """Return the Docker CLI path discoverable on this machine."""
        return shutil.which("docker")

    def compose_command(self, arguments: Sequence[str]) -> list[str]:
        """Return a shell-independent Docker Compose v2 argument vector."""
        command = [
            "docker",
            "compose",
            "--project-directory",
            str(self.project_root),
            "--file",
            str(self.compose_file),
        ]
        if self.env_file.exists():
            command.extend(["--env-file", str(self.env_file)])
        return [*command, *arguments]

    def run(self, arguments: Sequence[str], input_text: str | None = None) -> None:
        """Execute a non-interactive Compose command and raise with stderr context."""
        completed = subprocess.run(
            self.compose_command(arguments),
            cwd=self.project_root,
            input=input_text,
            text=True,
            capture_output=input_text is not None,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown Docker error").strip()
            raise CommandFailedError(f"Docker Compose failed: {detail}")

    def output(self, arguments: Sequence[str]) -> str:
        """Return stdout from a successful non-interactive Compose command."""
        completed = subprocess.run(
            self.compose_command(arguments),
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown Docker error").strip()
            raise CommandFailedError(f"Docker Compose failed: {detail}")
        return completed.stdout.strip()

    def interactive(self, arguments: Sequence[str]) -> int:
        """Run a Compose command attached to the current terminal."""
        return subprocess.call(self.compose_command(arguments), cwd=self.project_root)

    def docker_output(self, arguments: Sequence[str]) -> str:
        """Run a Docker CLI command that is not scoped to Compose."""
        completed = subprocess.run(
            ["docker", *arguments],
            cwd=self.project_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "unknown Docker error").strip()
            raise CommandFailedError(f"Docker failed: {detail}")
        return completed.stdout.strip()

    def running_services(self) -> set[str]:
        """Return Compose service names currently running for this project."""
        output = self.output(["ps", "--services", "--status", "running"])
        return {line.strip() for line in output.splitlines() if line.strip()}

    def containers_publishing_port(self, port: int) -> tuple[tuple[str, str], ...]:
        """Return running Docker container IDs and names publishing a host port."""
        output = self.docker_output(
            ["ps", "--filter", f"publish={port}", "--format", "{{.ID}}\t{{.Names}}"]
        )
        containers: list[tuple[str, str]] = []
        for line in output.splitlines():
            container_id, separator, name = line.partition("\t")
            if separator and container_id and name:
                containers.append((container_id, name))
        return tuple(containers)

    def stop_containers(self, container_ids: tuple[str, ...]) -> None:
        """Stop explicitly identified conflicting containers without touching their data."""
        if container_ids:
            self.docker_output(["stop", *container_ids])
