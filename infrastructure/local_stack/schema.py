"""Validate and load the approved PostgreSQL schema execution manifest."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class SchemaManifest:
    """Resolve schema files only from the documented creation order."""

    ENTRY_PATTERN = re.compile(r"^\s*\d+\.\s+`([^`]+\.sql)`")

    def __init__(self, manifest_path: Path, schema_directory: Path) -> None:
        """Bind the manifest and the only directory files may resolve within."""
        self.manifest_path = manifest_path
        self.schema_directory = schema_directory.resolve()

    def ordered_schema_files(self) -> tuple[Path, ...]:
        """Return validated, unique schema paths in manifest order.

        Algorithm:
            1. Extract numbered SQL entries from the Markdown manifest.
            2. Reject an empty manifest or duplicate entries.
            3. Resolve every path and prevent traversal outside postgres_schema.
            4. Reject missing or non-file targets before any SQL is executed.
        """
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Schema manifest not found: {self.manifest_path}")
        names = tuple(
            match.group(1)
            for line in self.manifest_path.read_text(encoding="utf-8").splitlines()
            if (match := self.ENTRY_PATTERN.match(line))
        )
        if not names:
            raise ValueError(f"No numbered SQL files found in {self.manifest_path}")
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Schema manifest contains duplicates: {duplicates}")

        files: list[Path] = []
        for name in names:
            path = (self.schema_directory / name).resolve()
            if not path.is_relative_to(self.schema_directory):
                raise ValueError(f"Schema path escapes postgres_schema: {name}")
            if not path.is_file():
                raise FileNotFoundError(f"Schema manifest entry is missing: {path}")
            files.append(path)
        return tuple(files)
