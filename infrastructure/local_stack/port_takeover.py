"""Reclaim the fixed host ports required by local infrastructure.

Architecture:
    LocalInfrastructureManager -> InfrastructurePortReclaimer
                                  |-> DockerComposeClient
                                  `-> PortListenerTerminator

Why this exists
    The stack publishes fixed host ports (see SERVICE_PORTS). Docker reports a
    bind conflict as an opaque failure partway through `up`, leaving some
    containers started and some not. Reclaiming first turns that into one
    explicit, reversible decision made before anything is mutated.

Why it asks first
    Reclaiming is destructive to whatever already owns the port: a container is
    stopped, and a host process is force-terminated with no chance to flush or
    shut down. On a developer machine the occupant is just as likely to be a
    native PostgreSQL install, another project's Redis, or the developer's own
    `uvicorn` on 8000. Every such action is therefore confirmed, naming the
    process and the command line, so the answer is an informed one. `--yes`
    pre-authorizes the whole run for CI.
"""

from __future__ import annotations

import os
import socket
import time
from typing import TYPE_CHECKING

from infrastructure.local_stack import console
from infrastructure.local_stack.constants import SERVICE_PORTS
from infrastructure.local_stack.errors import CommandFailedError

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrastructure.local_stack.docker_compose import DockerComposeClient
    from infrastructure.local_stack.port_listener_terminator import (
        PortListener,
        PortListenerTerminator,
    )

    ConfirmCallback = Callable[[str], bool]


class InfrastructurePortReclaimer:
    """Give this project's services ownership of their configured localhost ports."""

    protected_process_names = frozenset(
        {
            "com.docker.backend",
            "com.docker.backend.exe",
            "docker",
            "docker.exe",
            "dockerd",
            "dockerd.exe",
            "system",
            "system idle process",
        }
    )

    def __init__(
        self,
        compose: DockerComposeClient,
        listener_terminator: PortListenerTerminator,
        confirm: ConfirmCallback = console.confirm,
        release_timeout_seconds: float = 10.0,
    ) -> None:
        """Receive explicit Docker, process, and consent adapters.

        Args:
            compose: Docker access, used to identify this project's own
                containers and to stop conflicting ones.
            listener_terminator: Operating-system adapter that finds and kills
                host processes holding a port.
            confirm: Asks the operator to approve one destructive action.
                Injected so tests can answer without a terminal, and so the
                CLI can bind `--yes`.
            release_timeout_seconds: How long to wait for the OS to release a
                port after its listener is terminated.
        """
        self.compose = compose
        self.listener_terminator = listener_terminator
        self.confirm = confirm
        self.release_timeout_seconds = release_timeout_seconds

    def reclaim_managed_ports(self) -> None:
        """Reclaim every occupied stack port not already owned by this project."""
        running_services = self.compose.running_services()
        conflicts = (
            (service, port)
            for service, port in SERVICE_PORTS.items()
            if service not in running_services and self.port_is_open(port)
        )
        for service, port in conflicts:
            self.reclaim_port(service, port)

    def reclaim_port(self, service: str, port: int) -> None:
        """Stop conflicting containers, then terminate remaining host listeners."""
        console.heading(f"Reclaiming port {port} for {service}")
        self.stop_conflicting_containers(port)
        if self.port_is_open(port):
            self.stop_conflicting_processes(port)
        if self.port_is_open(port):
            raise CommandFailedError(f"Port {port} could not be reclaimed for {service}.")
        console.success(f"Port {port} reclaimed for {service}")

    def stop_conflicting_containers(self, port: int) -> None:
        """Stop other Docker containers publishing the requested port, once approved."""
        containers = self.compose.containers_publishing_port(port)
        if not containers:
            return
        names = ", ".join(name for _, name in containers)
        console.warning(f"Port {port} is published by Docker container(s): {names}")
        if not self.confirm(f"Stop container(s) {names} to free port {port}?"):
            raise CommandFailedError(
                f"Port {port} is in use by container(s) {names} and was left running. "
                f"Stop them yourself (`docker stop {names}`), free the port another way, "
                "or rerun with --yes to approve this automatically."
            )
        self.compose.stop_containers(tuple(container_id for container_id, _ in containers))
        self.wait_until_released(port, maximum_wait_seconds=2.0)

    def stop_conflicting_processes(self, port: int) -> None:
        """Terminate every remaining non-critical process listening on the port."""
        listeners = self.listener_terminator.find_listeners(port)
        if not listeners:
            raise CommandFailedError(
                f"Port {port} is occupied, but its owner is not visible. "
                "Run the command from an elevated terminal."
            )
        for listener in listeners:
            self.stop_process(port, listener)
        self.wait_until_released(port, self.release_timeout_seconds)

    def stop_process(self, port: int, listener: PortListener) -> None:
        """Validate, confirm, and terminate one identified listener.

        Two gates stand in front of the kill. The protected-name check is
        absolute: terminating the Docker daemon, or this process itself, would
        break the very run that is trying to recover. The confirmation is the
        operator's, because a force-terminate gives the target no chance to
        flush state, and the target here is often something the developer is
        deliberately running.
        """
        if (
            listener.process_id == os.getpid()
            or listener.process_name.lower() in self.protected_process_names
        ):
            raise CommandFailedError(
                f"Refusing to terminate protected process {listener.process_name} "
                f"(PID {listener.process_id}) on port {port}."
            )
        console.warning(
            f"Port {port} is held by {listener.process_name} "
            f"(PID {listener.process_id}): {listener.command}"
        )
        if not self.confirm(
            f"Force-terminate {listener.process_name} (PID {listener.process_id}) "
            f"to free port {port}? Unsaved state in that process is lost."
        ):
            raise CommandFailedError(
                f"Port {port} is held by {listener.process_name} "
                f"(PID {listener.process_id}) and was left running. Stop it yourself, "
                "or rerun with --yes to approve termination automatically."
            )
        self.listener_terminator.force_terminate(listener)

    def wait_until_released(self, port: int, maximum_wait_seconds: float) -> None:
        """Wait briefly for the operating system to remove a terminated listener."""
        deadline = time.monotonic() + maximum_wait_seconds
        while time.monotonic() < deadline and self.port_is_open(port):
            time.sleep(0.2)

    @staticmethod
    def port_is_open(port: int) -> bool:
        """Return whether a localhost TCP listener accepts connections on the port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client:
            client.settimeout(0.25)
            return client.connect_ex(("127.0.0.1", port)) == 0
