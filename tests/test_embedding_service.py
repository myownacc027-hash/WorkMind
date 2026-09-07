from __future__ import annotations

from packages.processing.embeddings import embedding_service_status


def test_embedding_service_status_returns_dict() -> None:
    result = embedding_service_status(timeout_seconds=0.01)
    assert isinstance(result, dict)
    assert "status" in result
