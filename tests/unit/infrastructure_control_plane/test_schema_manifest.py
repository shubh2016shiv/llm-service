"""Tests for safe PostgreSQL schema manifest resolution."""

from __future__ import annotations

import pytest

from infrastructure.local_stack.schema import SchemaManifest


def test_ordered_schema_files_returns_manifest_order(tmp_path) -> None:
    """Numbered manifest entries are the only source of execution order."""
    schema_directory = tmp_path / "postgres_schema"
    schema_directory.mkdir()
    first = schema_directory / "first.sql"
    second = schema_directory / "second.sql"
    first.write_text("SELECT 1;", encoding="utf-8")
    second.write_text("SELECT 2;", encoding="utf-8")
    manifest = schema_directory / "schema_creation_order.md"
    manifest.write_text("1. `first.sql`\n2. `second.sql`\n", encoding="utf-8")

    files = SchemaManifest(manifest, schema_directory).ordered_schema_files()

    assert files == (first.resolve(), second.resolve())


def test_ordered_schema_files_rejects_duplicate_entry(tmp_path) -> None:
    """A schema cannot be applied twice accidentally through a duplicate manifest row."""
    schema_directory = tmp_path / "postgres_schema"
    schema_directory.mkdir()
    (schema_directory / "one.sql").write_text("SELECT 1;", encoding="utf-8")
    manifest = schema_directory / "schema_creation_order.md"
    manifest.write_text("1. `one.sql`\n2. `one.sql`\n", encoding="utf-8")

    with pytest.raises(ValueError, match="duplicates"):
        SchemaManifest(manifest, schema_directory).ordered_schema_files()


def test_ordered_schema_files_rejects_path_outside_schema_directory(tmp_path) -> None:
    """Manifest traversal cannot feed arbitrary host files into PostgreSQL."""
    schema_directory = tmp_path / "postgres_schema"
    schema_directory.mkdir()
    outside = tmp_path / "outside.sql"
    outside.write_text("SELECT 1;", encoding="utf-8")
    manifest = schema_directory / "schema_creation_order.md"
    manifest.write_text("1. `../outside.sql`\n", encoding="utf-8")

    with pytest.raises(ValueError, match="escapes"):
        SchemaManifest(manifest, schema_directory).ordered_schema_files()
