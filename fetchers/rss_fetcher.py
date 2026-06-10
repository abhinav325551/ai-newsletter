"""RSS fetcher for mainstream news publishers.

Uses a realistic Mozilla User-Agent string because many publishers (Substack,
Cloudflare-fronted sites) now serve a bot-challenge HTML page to unknown UAs.
The previous string ``ai-newsletter/1.0`` caused ~15 feeds to return HTML or
Cloudflare CAPTCHA pages from GitHub Actions runner IPs even when the same
URLs returned proper XML from regular browsers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
from loguru import logger

from .base import FeedItem

# Match a current Firefox UA. Updated periodically — feedparser's default
# ``feedparser/X.Y +http://...`` and any ``*-bot*`` string get blocked or
# served challenge pages by Substack-fronted feeds in CI.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) "
    "Gecko/20100101 Firefox/122.0"
)


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


def fetch_rss_feed(
    name: str,
    url: str,
    weight: float,
    source_type: str = "news",
    since: datetime | None = None,
) -> list[FeedItem]:
    items: list[FeedItem] = []
    try:
        feed = feedparser.parse(
            url,
            request_headers={
                "User-Agent": _USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )
        if feed.bozo and not feed.entries:
            logger.warning(f"[RSS] {name}: malformed feed — {feed.bozo_exception}")
            return items

        for entry in feed.entries:
            pub = _parse_date(entry)
            if since and pub and pub < since:
                continue

            summary = getattr(entry, "summary", "") or ""
            # Strip HTML tags from summary
            import re
            summary = re.sub(r"<[^>]+>", " ", summary).strip()[:500]

            item = FeedItem(
                url=getattr(entry, "link", ""),
                title=getattr(entry, "title", "").strip(),
                source_name=name,
                source_type=source_type,
                summary=summary,
                published_at=pub,
                author=getattr(entry, "author", ""),
                source_weight=weight,
            )
            if item.url and item.title:
                items.append(item)

        logger.info(f"[RSS] {name}: {len(items)} items")
    except Exception as exc:
        logger.error(f"[RSS] {name}: fetch failed — {exc}")
    return items


def fetch_news_rss(config: dict, since: datetime | None = None) -> list[FeedItem]:
    all_items: list[FeedItem] = []
    for feed_cfg in config.get("news_rss", []):
        all_items.extend(
            fetch_rss_feed(
                name=feed_cfg["name"],
                url=feed_cfg["url"],
                weight=feed_cfg.get("weight", 0.6),
                source_type="news",
                since=since,
            )
        )
    return all_items
