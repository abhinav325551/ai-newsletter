"""Approximate item snapshots for blank issues that predate data/items/.

Snapshotting began 2026-09-04, so the blank issues before that can't be
replayed faithfully. This does one live fetch with a long lookback and buckets
whatever the feeds still hold by publish date: an issue dated D gets items
published in the 24h before its ~08:00 UTC run (72h on Mondays, matching the
weekday-only cron).

Coverage is partial by nature — RSS feeds keep only their latest N entries and
HN/Reddit rankings from back then are gone. Buckets are written to
data/items_reconstructed/ (NOT data/items/, which the narrative ledger ingests
as ground truth). Rebuild from them with:

    python backfill.py --items-dir data/items_reconstructed --reconstructed DAY...

Usage:
    python reconstruct.py            # every blank issue in docs/ with no snapshot
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from backfill import DOCS, EMPTY_ISSUE_BYTES, ITEMS_DIR, RUN_HOUR_UTC
from main import load_config
from persistence import persist_items

OUT_DIR = Path("data/items_reconstructed")
# Below this an issue isn't worth generating.
MIN_ITEMS = 10


def missing_days() -> list[str]:
    return [
        md.stem for md in sorted(DOCS.glob("20??-??-??.md"))
        if md.stat().st_size < EMPTY_ISSUE_BYTES and not (ITEMS_DIR / f"{md.stem}.jsonl").exists()
    ]


def main() -> None:
    from fetchers import (
        fetch_blogs, fetch_hacker_news, fetch_hf_orgs, fetch_lab_scrapers,
        fetch_news_rss, fetch_podcasts, fetch_research, fetch_substacks,
    )

    days = [d for d in missing_days() if d >= "2026-08-01"]
    if not days:
        logger.info("No blank issues without snapshots")
        return
    config = load_config()
    since_dt = datetime.fromisoformat(days[0]).replace(tzinfo=timezone.utc) - timedelta(days=4)
    logger.info(f"Reconstructing {len(days)} day(s), fetching since {since_dt:%Y-%m-%d}")

    # Reddit/Twitter are skipped: their APIs only return current rankings.
    all_items = []
    for name, fn in [
        ("News RSS", fetch_news_rss), ("Substacks", fetch_substacks), ("Blogs", fetch_blogs),
        ("Lab scrapers", fetch_lab_scrapers), ("HF orgs", fetch_hf_orgs),
        ("Podcasts", fetch_podcasts), ("Hacker News", fetch_hacker_news),
        ("Research", fetch_research),
    ]:
        try:
            items = fn(config, since_dt)
            logger.info(f"  ✓ {name}: {len(items)} items")
            all_items.extend(items)
        except Exception as exc:
            logger.error(f"  ✗ {name}: FAILED — {exc}")

    for day in days:
        end = datetime.fromisoformat(day).replace(hour=RUN_HOUR_UTC, tzinfo=timezone.utc)
        start = end - timedelta(days=3 if end.weekday() == 0 else 1)
        bucket = [i for i in all_items if i.published_at and start <= i.published_at < end]
        sources = len({i.source_name for i in bucket})
        if len(bucket) < MIN_ITEMS:
            logger.warning(f"{day}: only {len(bucket)} items from {sources} sources — skipped")
            continue
        persist_items(bucket, OUT_DIR, day)
        logger.info(f"{day}: {len(bucket)} items from {sources} sources")


if __name__ == "__main__":
    main()
