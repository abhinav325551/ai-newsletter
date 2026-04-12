"""Substack newsletter fetcher (uses standard RSS feeds)."""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from .base import FeedItem
from .rss_fetcher import fetch_rss_feed


def fetch_substacks(config: dict, since: datetime | None = None) -> list[FeedItem]:
    all_items: list[FeedItem] = []
    for feed_cfg in config.get("substacks", []):
        items = fetch_rss_feed(
            name=feed_cfg["name"],
            url=feed_cfg["url"],
            weight=feed_cfg.get("weight", 0.7),
            source_type="substack",
            since=since,
        )
        all_items.extend(items)
    logger.info(f"[Substack] Total: {len(all_items)} items")
    return all_items
