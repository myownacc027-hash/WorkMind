from __future__ import annotations

import uuid
from collections.abc import Sequence

from qdrant_client import QdrantClient, models

from packages.common.config import get_settings


def ensure_collection(client: QdrantClient, *, vector_size: int) -> None:
    settings = get_settings()
    if client.collection_exists(settings.qdrant_collection):
        return
    client.create_collection(
        collection_name=settings.qdrant_collection,
        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
    )


def upsert_chunks(
    client: QdrantClient,
    *,
    point_ids: Sequence[uuid.UUID],
    vectors: Sequence[Sequence[float]],
    payloads: Sequence[dict],
) -> None:
    if not vectors:
        return
    ensure_collection(client, vector_size=len(vectors[0]))
    points = [
        models.PointStruct(id=str(point_id), vector=list(vector), payload=payload)
        for point_id, vector, payload in zip(point_ids, vectors, payloads, strict=True)
    ]
    client.upsert(collection_name=get_settings().qdrant_collection, points=points, wait=True)


def delete_points(client: QdrantClient, point_ids: Sequence[uuid.UUID]) -> None:
    if not point_ids:
        return
    settings = get_settings()
    if not client.collection_exists(settings.qdrant_collection):
        return
    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=models.PointIdsList(points=[str(point_id) for point_id in point_ids]),
        wait=True,
    )
