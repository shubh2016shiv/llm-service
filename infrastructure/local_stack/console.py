"""Small cross-platform terminal presentation helpers."""

from __future__ import annotations

import os
import sys


def supports_color() -> bool:
    """Return whether this terminal can safely display ANSI colors."""
    return sys.stdout.isatty() and (os.name != "nt" or bool(os.environ.get("WT_SESSION")))


COLOR_ENABLED = supports_color()


def color(text: str, code: str) -> str:
    """Wrap text in an ANSI color when supported."""
    return f"\033[{code}m{text}\033[0m" if COLOR_ENABLED else text


def bold(text: str) -> str:
    """Return bold terminal text when ANSI presentation is supported."""
    return color(text, "1")


def heading(title: str) -> None:
    """Print a readable section heading."""
    print(f"\n{title}\n{'-' * len(title)}")


def success(message: str) -> None:
    """Print a successful operation message."""
    print(f"{color('[PASS]', '32')} {message}")


def warning(message: str) -> None:
    """Print a non-blocking warning."""
    print(f"{color('[WARN]', '33')} {message}")


def failure(message: str) -> None:
    """Print a blocking failure message."""
    print(f"{color('[FAIL]', '31')} {message}")


def step(message: str) -> None:
    """Print a neutral progress line that is neither success nor failure."""
    print(f"[ .. ] {message}")


def confirm(question: str, assume_yes: bool = False) -> bool:
    """Ask for explicit consent before an irreversible or disruptive action.

    Answering is deliberately opt-in: an empty answer means "no". A
    non-interactive run (CI, a piped stdin) cannot answer, so it is treated as
    "no" rather than silently proceeding — `--yes` is the way to pre-authorize
    those runs.
    """
    if assume_yes:
        print(f"{question} [y/N] y  (--yes supplied)")
        return True
    if not sys.stdin.isatty():
        print(f"{question} [y/N] n  (not a terminal; rerun with --yes to pre-authorize)")
        return False
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def table(headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
    """Print an aligned ASCII table that renders consistently in every shell."""
    widths = tuple(
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    )
    separator = "+-" + "-+-".join("-" * width for width in widths) + "-+"

    def render_row(row: tuple[str, ...]) -> str:
        return "| " + " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(row)
        ) + " |"

    print(separator)
    print(render_row(headers))
    print(separator)
    for row in rows:
        print(render_row(row))
    print(separator)


# Compatibility names used by the long-form diagnostic modules.
header = heading
ok = success
warn = warning
fail = failure
