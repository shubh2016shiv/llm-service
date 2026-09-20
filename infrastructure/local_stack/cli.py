"""Command-line interface for the cross-platform local infrastructure control plane."""

from __future__ import annotations

import argparse
import sys
from functools import partial
from typing import TYPE_CHECKING

from infrastructure.local_stack import console
from infrastructure.local_stack.constants import (
    COMPOSE_FILE,
    ENV_FILE,
    PROJECT_ROOT,
    SCHEMA_DIRECTORY,
    SCHEMA_MANIFEST,
)
from infrastructure.local_stack.docker_compose import DockerComposeClient
from infrastructure.local_stack.environment import LocalEnvironmentFile
from infrastructure.local_stack.errors import InfrastructureError
from infrastructure.local_stack.manager import LocalInfrastructureManager
from infrastructure.local_stack.port_listener_terminator import PortListenerTerminator
from infrastructure.local_stack.port_takeover import InfrastructurePortReclaimer
from infrastructure.local_stack.preflight import InfrastructurePreflight
from infrastructure.local_stack.schema import SchemaManifest
from infrastructure.local_stack.token_manager import TokenManagerStack

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    """Define one consistent command surface for Windows, Linux, and macOS."""
    parser = argparse.ArgumentParser(
        prog="python -m infrastructure",
        description="Manage local PostgreSQL, Redis, and Vault safely through Docker Compose.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_integer,
        default=90,
        help="Readiness timeout in seconds (default: 90).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Approve port reclaim without prompting. Required for non-interactive "
            "runs, which otherwise decline every destructive action."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Run read-only host and project prevalidation.")

    init_parser = subparsers.add_parser("init", help="Generate only missing local .env values.")
    add_secret_hiding_option(init_parser)
    start_parser = subparsers.add_parser(
        "start",
        help="Prevalidate, start services, bootstrap Vault, apply schema, start token manager.",
    )
    add_secret_hiding_option(start_parser)
    restart_parser = subparsers.add_parser("restart", help="Stop then run the full start flow.")
    add_secret_hiding_option(restart_parser)
    status_parser = subparsers.add_parser("status", help="Show service and connection status.")
    add_secret_hiding_option(status_parser)
    connection_info_parser = subparsers.add_parser(
        "connection-info",
        help="Print ports, usernames, passwords, tokens, and connection URLs.",
    )
    add_secret_hiding_option(connection_info_parser)

    subparsers.add_parser("verify", help="Run native health checks against running services.")
    subparsers.add_parser("stop", help="Stop services while preserving local data.")
    subparsers.add_parser("schema", help="Validate and apply the PostgreSQL schema manifest.")

    connect_parser = subparsers.add_parser("connect", help="Open a service-native shell.")
    connect_parser.add_argument("service", choices=("postgres", "redis"))

    reset_parser = subparsers.add_parser("reset", help="Delete containers and named volumes.")
    reset_parser.add_argument(
        "--confirm-delete-volumes",
        action="store_true",
        help="Required acknowledgement that all local infrastructure data will be deleted.",
    )
    return parser


def add_secret_hiding_option(parser: argparse.ArgumentParser) -> None:
    """Allow credentials to be masked when terminal output will be shared."""
    parser.add_argument(
        "--hide-secrets",
        action="store_true",
        help="Mask passwords and tokens in the connection table.",
    )


def positive_integer(value: str) -> int:
    """Parse an integer CLI value and reject zero or negative timeouts."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_manager(
    readiness_timeout_seconds: int = 90,
    assume_yes: bool = False,
) -> LocalInfrastructureManager:
    """Assemble production collaborators from absolute repository paths."""
    environment_file = LocalEnvironmentFile(ENV_FILE)
    compose = DockerComposeClient(PROJECT_ROOT, COMPOSE_FILE, ENV_FILE)
    schema_manifest = SchemaManifest(SCHEMA_MANIFEST, SCHEMA_DIRECTORY)
    preflight = InfrastructurePreflight(
        compose=compose,
        environment_file=environment_file,
        schema_manifest=schema_manifest,
        required_paths=(
            COMPOSE_FILE,
            SCHEMA_MANIFEST,
            PROJECT_ROOT / "vault" / "scripts" / "init.sh",
        ),
    )
    return LocalInfrastructureManager(
        compose=compose,
        environment_file=environment_file,
        schema_manifest=schema_manifest,
        preflight=preflight,
        port_reclaimer=InfrastructurePortReclaimer(
            compose,
            PortListenerTerminator(),
            confirm=partial(console.confirm, assume_yes=assume_yes),
        ),
        token_manager=TokenManagerStack(),
        readiness_timeout_seconds=readiness_timeout_seconds,
    )


def dispatch(manager: LocalInfrastructureManager, arguments: argparse.Namespace) -> int:
    """Execute exactly one parsed command and return its process exit code."""
    command = arguments.command
    if command == "check":
        return 0 if manager.check().ready else 1
    if command == "init":
        manager.initialize(show_secrets=not arguments.hide_secrets)
    elif command == "start":
        manager.start(show_secrets=not arguments.hide_secrets)
    elif command == "restart":
        manager.restart(show_secrets=not arguments.hide_secrets)
    elif command == "status":
        manager.status(show_secrets=not arguments.hide_secrets)
    elif command == "connection-info":
        manager.connection_info(show_secrets=not arguments.hide_secrets)
    elif command == "verify":
        manager.verify()
    elif command == "stop":
        manager.stop()
    elif command == "schema":
        manager.apply_schema()
    elif command == "connect":
        return manager.connect(arguments.service)
    elif command == "reset":
        manager.reset(confirm_delete_volumes=arguments.confirm_delete_volumes)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments and convert expected operational failures to exit code one."""
    arguments = build_parser().parse_args(argv)
    try:
        return dispatch(build_manager(arguments.timeout, arguments.yes), arguments)
    except (InfrastructureError, FileNotFoundError, OSError, TimeoutError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
