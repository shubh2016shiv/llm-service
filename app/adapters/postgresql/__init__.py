"""
PostgreSQL connectivity — the database doorway
===============================================

What this package is for
------------------------
The app's data lives in PostgreSQL. Every database touch needs a
"connection" — an open phone line to the server. Opening a fresh line for
every query is slow, so we keep a small pool of ready-made lines and hand
them out one at a time. This package owns that pool: building it at
startup, handing out short-lived "sessions" (a line plus the work done on
it) during normal operation, and closing everything at shutdown.

What this package deliberately does NOT own
-------------------------------------------
The actual SQL queries live in ``app.database``. This package manages only
the plumbing; the query code borrows sessions from here.

One file, one idea
------------------
    session_provider.py — the pool keeper: engine, pool, sessions, health
                          check, and shutdown.

If any line in these files still reads like jargon, it is a bug in the
comments — not in you. Fix it right there.

Author: Shubham Singh
"""

# Make the pool keeper available at the package level, so callers can
# write ``from app.adapters.postgresql import PostgresSessionProvider``.
from app.adapters.postgresql.session_provider import PostgresSessionProvider

# Keep the package's public API explicit for documentation and static tools.
__all__ = ["PostgresSessionProvider"]
