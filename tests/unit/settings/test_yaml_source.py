"""Tests for strict YAML document loading and overlay merging."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core.settings.yaml_source import load_yaml_mapping, merge_mappings

if TYPE_CHECKING:
    from pathlib import Path


def test_load_yaml_mapping_rejects_list_document(tmp_path: Path) -> None:
    """A syntactically valid but structurally wrong YAML file must fail loudly."""
    path = tmp_path / "broken.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must contain a mapping"):
        load_yaml_mapping(path)


def test_load_yaml_mapping_rejects_non_text_key(tmp_path: Path) -> None:
    """Configuration keys must remain addressable by stable text names."""
    path = tmp_path / "broken.yaml"
    path.write_text("1: value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must be text"):
        load_yaml_mapping(path)


def test_merge_mappings_recurses_without_mutating_inputs() -> None:
    """An overlay replaces leaves while retaining unrelated nested defaults."""
    base = {"http": {"timeout": 10, "pool": 20}, "level": "INFO"}
    override = {"http": {"timeout": 3}}

    merged = merge_mappings(base, override)

    assert merged == {"http": {"timeout": 3, "pool": 20}, "level": "INFO"}
    assert base["http"] == {"timeout": 10, "pool": 20}
