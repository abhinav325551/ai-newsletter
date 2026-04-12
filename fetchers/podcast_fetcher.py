"""Podcast fetcher — pulls episode titles and descriptions from RSS.

Full audio transcription is not performed by default. To enable it,
set WHISPER_ENABLED=true in .env and install openai-whisper separately.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
from loguru import logger

from .base import FeedItem


def _parse_date(entry: Any) -> datetime | None:
    for attr in ("published", "updated"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    return None


def fetch_podcasts(config: dict, since: datetime | None = None) -> list[FeedItem]:
    all_items: list[FeedItem] = []

    for feed_cfg in config.get("podcasts", []):
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        weight = feed_cfg.get("weight", 0.7)

        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "ai-newsletter/1.0"})
            if feed.bozo and not feed.entries:
                logger.warning(f"[Podcast] {name}: malformed feed")
                continue

            for entry in feed.entries:
                pub = _parse_date(entry)
                if since and pub and pub < since:
                    continue

                # Prefer episode page link over enclosure URL
                link = getattr(entry, "link", "")
                enclosures = getattr(entry, "enclosures", [])
                if not link and enclosures:
                    link = enclosures[0].get("href", "")

                summary = getattr(entry, "summary", "") or ""
                summary = re.sub(r"<[^>]+>", " ", summary).strip()[:600]

                audio_url = ""
                if enclosures:
                    audio_url = enclosures[0].get("href", "")

                item = FeedItem(
                    url=link,
                    title=getattr(entry, "title", "").strip(),
                    source_name=name,
                    source_type="podcast",
                    summary=summary,
                    published_at=pub,
                    source_weight=weight,
                    tags=[audio_url] if audio_url else [],  # store audio URL in tags for Whisper hook
                )
                if item.url and item.title:
                    all_items.append(item)

            logger.info(f"[Podcast] {name}: {sum(1 for i in all_items if i.source_name == name)} items")

        except Exception as exc:
            logger.error(f"[Podcast] {name}: fetch failed — {exc}")

    logger.info(f"[Podcast] Total: {len(all_items)} items")
    return all_items
