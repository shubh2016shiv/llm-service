"""Find and terminate processes listening on managed infrastructure ports.

Architecture:
    InfrastructurePortReclaimer -> PortListenerTerminator -> native OS commands
"""

from __future__ import annotations

import csv
import os
import platform
import re
import signal
import subprocess
from dataclasses import dataclass

from infrastructure.local_stack.errors import CommandFailedError


@dataclass(frozen=True)
class PortListener:
    """Human-readable identity of a process listening on a TCP port."""

    process_id: int
    process_name: str
    command: str


class PortListenerTerminator:
    """Discover and force-terminate port listeners on Windows, Linux, and macOS."""

    def find_listeners(self, port: int) -> tuple[PortListener, ...]:
        """Return the processes listening on one TCP port."""
        if platform.system() == "Windows":
            process_ids = self.find_windows_process_ids(port)
        else:
            process_ids = self.find_unix_process_ids(port)
        return tuple(self.describe_process(process_id) for process_id in process_ids)

    def find_windows_process_ids(self, port: int) -> tuple[int, ...]:
        """Parse listener PIDs from the built-in Windows netstat command."""
        output = self.command_output(["netstat", "-ano", "-p", "tcp"])
        process_ids: set[int] = set()
        for line in output.splitlines():
            columns = line.split()
            if (
                len(columns) >= 5
                and columns[3].upper() == "LISTENING"
                and columns[1].rsplit(":", maxsplit=1)[-1] == str(port)
            ):
                process_ids.add(int(columns[4]))
        return tuple(sorted(process_ids))

    def find_unix_process_ids(self, port: int) -> tuple[int, ...]:
        """Use Linux ss when present and otherwise use macOS/Linux lsof."""
        if platform.system() == "Linux":
            try:
                output = self.command_output(["ss", "-H", "-ltnp", f"sport = :{port}"])
            except FileNotFoundError:
                pass
            else:
                process_ids = {int(value) for value in re.findall(r"pid=(\d+)", output)}
                if process_ids or not output.strip():
                    return tuple(sorted(process_ids))
        try:
            output = self.command_output(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                accepted_exit_codes=(0, 1),
            )
        except FileNotFoundError as error:
            raise CommandFailedError(
                f"Cannot identify the owner of port {port}; install `ss` or `lsof`."
            ) from error
        return tuple(sorted({int(line) for line in output.splitlines() if line.isdigit()}))

    def describe_process(self, process_id: int) -> PortListener:
        """Resolve the process name and command displayed before termination."""
        if platform.system() == "Windows":
            output = self.command_output(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                accepted_exit_codes=(0, 1),
            )
            rows = tuple(csv.reader(line for line in output.splitlines() if line.startswith('"')))
            process_name = rows[0][0] if rows else "unknown"
            return PortListener(process_id, process_name, process_name)
        output = self.command_output(
            ["ps", "-p", str(process_id), "-o", "comm=", "-o", "args="],
            accepted_exit_codes=(0, 1),
        ).strip()
        process_name, _, command = output.partition(" ")
        return PortListener(
            process_id,
            process_name or "unknown",
            command or output or "unavailable",
        )

    def force_terminate(self, listener: PortListener) -> None:
        """Immediately terminate the listener and its child processes."""
        try:
            if platform.system() == "Windows":
                self.run_command(
                    ["taskkill", "/PID", str(listener.process_id), "/T", "/F"],
                    accepted_exit_codes=(0, 128),
                )
            else:
                # SIGKILL is absent from the Windows `signal` module, so it is
                # resolved dynamically: mypy analyses this file on Windows too,
                # where the attribute does not exist at type-check time.
                os.kill(listener.process_id, getattr(signal, "SIGKILL", signal.SIGTERM))
        except PermissionError as error:
            raise CommandFailedError(
                f"Permission denied terminating {listener.process_name} "
                f"(PID {listener.process_id}). Run from an elevated terminal."
            ) from error
        except ProcessLookupError:
            return

    def command_output(
        self,
        command: list[str],
        accepted_exit_codes: tuple[int, ...] = (0,),
    ) -> str:
        """Run a native inspection command and return its output."""
        return self.run_command(command, accepted_exit_codes).stdout.strip()

    @staticmethod
    def run_command(
        command: list[str],
        accepted_exit_codes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        """Run a native command without shell interpolation."""
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode not in accepted_exit_codes:
            detail = (completed.stderr or completed.stdout or "unknown error").strip()
            raise CommandFailedError(f"Command {' '.join(command)} failed: {detail}")
        return completed
