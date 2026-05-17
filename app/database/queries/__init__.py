"""
app/database/queries
--------------------
Named SQL constant modules — one module per entity.

Keeping SQL in dedicated modules rather than inline strings achieves three things:
  1. Persistence classes stay readable; SQL is not buried in method bodies.
  2. SQL strings are trivially grep-able and diff-able.
  3. IDE SQL plugins and linters can operate on the raw strings without parsing
     Python method bodies.

Each module exports SCREAMING_SNAKE_CASE constants. Dynamic SQL (pagination,
partial-update SET clauses) is assembled in the persistence layer where the
runtime values are available.
"""
