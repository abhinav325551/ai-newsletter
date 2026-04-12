"""Deduplication: URL canonicalization + title fuzzy matching via rapidfuzz."""
from __future__ import annotations

from loguru import logger
from rapidfuzz import fuzz

from fetchers.base import FeedItem


def deduplicate(items: list[FeedItem], title_threshold: int = 88) -> list[FeedItem]:
    """Remove near-duplicate items.

    Two items are duplicates if:
    - Their canonical URLs match exactly, OR
    - Their titles have a rapidfuzz token_sort_ratio >= title_threshold
    """
    seen_urls: set[str] = set()
    unique: list[FeedItem] = []

    # Sort: higher source weight first so we keep the most authoritative copy
    sorted_items = sorted(items, key=lambda x: x.source_weight, reverse=True)

    for item in sorted_items:
        if not item.url or not item.title:
            continue

        # URL dedup
        canon = item.canonical_url
        if canon in seen_urls:
            continue

        # Title fuzzy dedup
        is_dup = False
        for kept in unique:
            ratio = fuzz.token_sort_ratio(item.title.lower(), kept.title.lower())
            if ratio >= title_threshold:
                is_dup = True
                break

        if not is_dup:
            seen_urls.add(canon)
            unique.append(item)

    removed = len(items) - len(unique)
    logger.info(f"[Dedup] {len(items)} → {len(unique)} items ({removed} duplicates removed)")
    return unique
