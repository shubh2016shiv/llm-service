"""
Terminal colour helpers for local diagnostic scripts.

Enables Windows VT processing when available; falls back to plain text
automatically when stdout is not a TTY or the terminal does not support
ANSI escape sequences.
"""

from __future__ import annotations

import sys


def _ansi_supported() -> bool:
    """Return True when the current stdout can render ANSI escape sequences."""
    if not sys.stdout.isatty():
        return False
    if sys.platform != "win32":
        return True
    # On Windows, attempt to enable Virtual Terminal Processing via the
    # Win32 console API.  If the call fails the terminal is too old.
    try:
        import ctypes
        import ctypes.wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
        )
    except Exception:
        return False


_USE_COLOUR = _ansi_supported()

_BOLD   = "\033[1m"  if _USE_COLOUR else ""
_GREEN  = "\033[32m" if _USE_COLOUR else ""
_YELLOW = "\033[33m" if _USE_COLOUR else ""
_RED    = "\033[31m" if _USE_COLOUR else ""
_RESET  = "\033[0m"  if _USE_COLOUR else ""


def header(title: str) -> None:
    print(f"\n{_BOLD}{title}{_RESET}")
    print("-" * len(title))


def ok(msg: str) -> None:
    print(f"  {_GREEN}[OK]{_RESET}   {msg}")


def warn(msg: str) -> None:
    print(f"  {_YELLOW}[WARN]{_RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {_RED}[FAIL]{_RESET} {msg}")


def bold(text: str) -> str:
    return f"{_BOLD}{text}{_RESET}"
