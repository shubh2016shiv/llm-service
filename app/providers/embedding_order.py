"""Keep OpenAI-compatible embedding vectors aligned with their input texts."""

from __future__ import annotations

from typing import Any


def ordered_embeddings(data: dict[str, Any], expected_count: int) -> list[list[float]]:
    """Reject missing, duplicate, or out-of-range indexes before returning vectors."""
    entries = data["data"]
    if not isinstance(entries, list) or len(entries) != expected_count:
        raise ValueError("Provider returned the wrong number of embeddings")
    vectors: list[list[float] | None] = [None] * expected_count
    for entry in entries:
        index = entry["index"]
        if type(index) is not int or not 0 <= index < expected_count or vectors[index] is not None:
            raise ValueError("Provider returned invalid embedding indexes")
        vectors[index] = entry["embedding"]
    return [vector for vector in vectors if vector is not None]
