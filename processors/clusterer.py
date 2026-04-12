"""Story clustering using sentence-transformers embeddings + cosine similarity.

Falls back to a simpler title-only TF-IDF approach if sentence-transformers
is unavailable or ANTHROPIC_API_KEY is set and embedding_backend='anthropic'.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np
from loguru import logger
from sklearn.metrics.pairwise import cosine_similarity

from fetchers.base import FeedItem

if TYPE_CHECKING:
    pass


def _embed_local(texts: list[str], model_name: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(model_name)
    return model.encode(texts, show_progress_bar=False, batch_size=64)


def _embed_anthropic(texts: list[str]) -> np.ndarray:
    import anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    vectors = []
    # Anthropic doesn't yet expose a public embeddings endpoint in the
    # standard SDK — fall through to local.
    raise NotImplementedError("Anthropic embeddings not yet available; using local")


def _embed(texts: list[str], backend: str, model_name: str) -> np.ndarray:
    if backend == "anthropic":
        try:
            return _embed_anthropic(texts)
        except Exception as exc:
            logger.warning(f"[Cluster] Anthropic embeddings failed ({exc}), falling back to local")

    return _embed_local(texts, model_name)


def cluster_items(
    items: list[FeedItem],
    threshold: float = 0.82,
    backend: str = "local",
    model_name: str = "all-MiniLM-L6-v2",
) -> list[FeedItem]:
    """Assign cluster_id to items that cover the same story."""
    if not items:
        return items

    texts = [f"{item.title}. {item.summary[:200]}" for item in items]

    try:
        embeddings = _embed(texts, backend, model_name)
    except Exception as exc:
        logger.error(f"[Cluster] Embedding failed: {exc} — skipping clustering")
        for i, item in enumerate(items):
            item.cluster_id = i
        return items

    sim_matrix = cosine_similarity(embeddings)

    cluster_map: dict[int, int] = {}  # item_idx -> cluster_id
    next_cluster = 0

    for i in range(len(items)):
        if i in cluster_map:
            continue
        cluster_map[i] = next_cluster
        for j in range(i + 1, len(items)):
            if j in cluster_map:
                continue
            if sim_matrix[i, j] >= threshold:
                cluster_map[j] = next_cluster
        next_cluster += 1

    for idx, item in enumerate(items):
        item.cluster_id = cluster_map.get(idx, idx)

    # Count multi-source clusters
    from collections import Counter
    counts = Counter(item.cluster_id for item in items)
    multi = sum(1 for c in counts.values() if c > 1)
    logger.info(f"[Cluster] {next_cluster} clusters ({multi} multi-source stories)")

    return items
