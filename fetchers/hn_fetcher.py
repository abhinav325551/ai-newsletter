"""Hacker News fetcher using the official Firebase API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from .base import FeedItem

HN_API = "https://hacker-news.firebaseio.com/v0"


async def _get_json(client: httpx.AsyncClient, url: str) -> Any:
    r = await client.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


async def _fetch_story(client: httpx.AsyncClient, story_id: int) -> dict | None:
    try:
        return await _get_json(client, f"{HN_API}/item/{story_id}.json")
    except Exception:
        return None


async def _fetch_stories_async(
    story_types: list[str],
    max_items: int,
    min_score: int,
    ai_keywords: list[str],
    since: datetime | None,
) -> list[FeedItem]:
    items: list[FeedItem] = []
    seen_ids: set[int] = set()
    kw_lower = [k.lower() for k in ai_keywords]

    async with httpx.AsyncClient() as client:
        for story_type in story_types:
            try:
                ids = await _get_json(client, f"{HN_API}/{story_type}.json")
            except Exception as exc:
                logger.error(f"[HN] Failed to fetch {story_type}: {exc}")
                continue

            # Fetch in batches of 20
            batch_size = 20
            fetched = 0
            for i in range(0, min(len(ids), max_items * 3), batch_size):
                if fetched >= max_items:
                    break
                batch = [sid for sid in ids[i : i + batch_size] if sid not in seen_ids]
                stories = await asyncio.gather(*[_fetch_story(client, sid) for sid in batch])

                for story in stories:
                    if not story or story.get("type") != "story":
                        continue
                    sid = story.get("id")
                    if sid in seen_ids:
                        continue
                    seen_ids.add(sid)

                    score = story.get("score", 0)
                    title = (story.get("title") or "").strip()
                    url = story.get("url") or f"https://news.ycombinator.com/item?id={sid}"

                    if score < min_score:
                        continue

                    title_lower = title.lower()
                    if not any(kw in title_lower for kw in kw_lower):
                        continue

                    pub_ts = story.get("time")
                    pub = datetime.fromtimestamp(pub_ts, tz=timezone.utc) if pub_ts else None
                    if since and pub and pub < since:
                        continue

                    items.append(
                        FeedItem(
                            url=url,
                            title=title,
                            source_name="Hacker News",
                            source_type="hn",
                            summary=story.get("text") or "",
                            published_at=pub,
                            score=score,
                            comments=story.get("descendants", 0),
                            author=story.get("by", ""),
                            source_weight=0.85,
                            tags=[f"https://news.ycombinator.com/item?id={sid}"],
                        )
                    )
                    fetched += 1

    logger.info(f"[HN] {len(items)} AI-relevant items (min score {min_score})")
    return items


def fetch_hacker_news(config: dict, since: datetime | None = None) -> list[FeedItem]:
    hn_cfg = config.get("hacker_news", {})
    ai_keywords = config.get("ai_keywords", [])
    return asyncio.run(
        _fetch_stories_async(
            story_types=hn_cfg.get("story_types", ["topstories"]),
            max_items=hn_cfg.get("max_items", 60),
            min_score=hn_cfg.get("min_score", 50),
            ai_keywords=ai_keywords,
            since=since,
        )
    )
