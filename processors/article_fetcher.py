"""Full article text enrichment using trafilatura."""
from __future__ import annotations

import concurrent.futures

import trafilatura
from loguru import logger
from tqdm import tqdm

from fetchers.base import FeedItem

_SKIP_TYPES = {"twitter", "reddit", "hn", "arxiv", "podcast"}
_MAX_WORKERS = 8


def _fetch_one(item: FeedItem) -> FeedItem:
    if item.source_type in _SKIP_TYPES:
        return item
    if not item.url or item.url.startswith("https://news.ycombinator.com"):
        return item
    try:
        downloaded = trafilatura.fetch_url(item.url)
        if downloaded:
            text = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                no_fallback=False,
            )
            if text:
                item.full_text = text[:3000]
    except Exception as exc:
        logger.debug(f"[ArticleFetch] {item.url}: {exc}")
    return item


def enrich_full_text(items: list[FeedItem]) -> list[FeedItem]:
    """Fetch full article text for news / blog / substack items in parallel."""
    fetchable = [i for i in items if i.source_type not in _SKIP_TYPES]
    skip = [i for i in items if i.source_type in _SKIP_TYPES]

    logger.info(f"[ArticleFetch] Fetching full text for {len(fetchable)} items...")

    enriched: list[FeedItem] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_one, item): item for item in fetchable}
        for future in tqdm(
            concurrent.futures.as_completed(futures),
            total=len(futures),
            desc="Full text",
            unit="article",
        ):
            try:
                enriched.append(future.result())
            except Exception as exc:
                logger.debug(f"[ArticleFetch] Worker error: {exc}")
                enriched.append(futures[future])

    result = enriched + skip
    fetched_count = sum(1 for i in enriched if i.full_text)
    logger.info(f"[ArticleFetch] Full text fetched for {fetched_count}/{len(fetchable)} items")
    return result
