"""Regenerate past daily issues from the raw item snapshots in data/items/.

main.py always fetches live and stamps today's date, so it can't rebuild an
old issue. This replays steps 2-8 of the daily pipeline (dedup → score →
full text → cluster → summarize → render) over a saved snapshot, with the
clock pinned to that day's run time so recency scoring matches the original.

It does NOT touch data/items/ or sources/performance.jsonl, and never emails.

Usage:
    python backfill.py 2026-09-17 2026-09-18
    python backfill.py --empty            # every issue in docs/ that came out blank
    python backfill.py --items-dir data/items_reconstructed --reconstructed --empty
                                          # approximate buckets from reconstruct.py
"""
from __future__ import annotations

import dataclasses
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import copy

import click
from loguru import logger

from fetchers.base import FeedItem
from main import load_config

ITEMS_DIR = Path("data/items")
DOCS = Path("docs")
# A healthy issue is ~20 KB; the blank ones (all Claude calls failed) are ~500 B.
EMPTY_ISSUE_BYTES = 2000
# Daily cron is 03:33 UTC but GitHub starts it ~08:30 UTC in practice.
RUN_HOUR_UTC = 8


RECONSTRUCTED_NOTE = (
    " — Reconstructed after the fact from what source feeds still held; "
    "coverage is partial."
)


def load_snapshot(day: str, items_dir: Path = ITEMS_DIR) -> list[FeedItem]:
    fields = {f.name for f in dataclasses.fields(FeedItem)}
    items = []
    with (items_dir / f"{day}.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            rec = {k: v for k, v in json.loads(line).items() if k in fields}
            if rec.get("published_at"):
                rec["published_at"] = datetime.fromisoformat(rec["published_at"])
            rec["cluster_id"] = None
            rec["section"] = None
            items.append(FeedItem(**rec))
    return items


def empty_days(items_dir: Path = ITEMS_DIR) -> list[str]:
    days = []
    for md in sorted(DOCS.glob("20??-??-??.md")):
        if md.stat().st_size < EMPTY_ISSUE_BYTES and (items_dir / f"{md.stem}.jsonl").exists():
            days.append(md.stem)
    return days


def pin_scorer_clock(run_time: datetime) -> None:
    """Make the scorer's recency boost see `run_time` instead of the real now."""
    import processors.scorer as scorer

    class _Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return run_time

    scorer.datetime = _Pinned


def rebuild(day: str, config: dict, items_dir: Path = ITEMS_DIR, reconstructed: bool = False) -> None:
    from processors import (
        cluster_items, deduplicate, enrich_full_text,
        score_and_classify, summarize_newsletter,
    )
    from processors.renderer import render

    newsletter_cfg = config.get("newsletter", {})
    run_time = datetime.fromisoformat(day).replace(hour=RUN_HOUR_UTC, tzinfo=timezone.utc)
    pin_scorer_clock(run_time)

    items = load_snapshot(day, items_dir)
    if reconstructed:
        # Label the issue via the subtitle both templates already render.
        config = copy.deepcopy(config)
        nl = config.setdefault("newsletter", {})
        nl["subtitle"] = (nl.get("subtitle") or "") + RECONSTRUCTED_NOTE
    logger.info("=" * 60)
    logger.info(f"BACKFILL {day} — {len(items)} snapshot items")
    logger.info("=" * 60)

    items = deduplicate(items, title_threshold=newsletter_cfg.get("dedup_title_threshold", 88))
    items = score_and_classify(items)
    items = enrich_full_text(items)
    items = cluster_items(
        items,
        threshold=newsletter_cfg.get("cluster_similarity_threshold", 0.82),
        backend=newsletter_cfg.get("embedding_backend", "local"),
        model_name=newsletter_cfg.get("local_embedding_model", "all-MiniLM-L6-v2"),
    )

    # Same section grouping as main.py step 6.
    max_per_section = newsletter_cfg.get("max_items_per_section", 8)
    items_by_section: dict[str, list] = defaultdict(list)
    core = [i for i in items if i.section in
            ("infrastructure", "models_research", "tooling_agents", "saas_disruption")]
    fallback = [i for i in items if i.section == "applications"]
    top_item = core[0] if core else (fallback[0] if fallback else None)
    skip_uid = None
    if top_item is not None:
        top_item.section = "big_thing"
        items_by_section["big_thing"] = [top_item]
        skip_uid = top_item.uid
    for item in items:
        if skip_uid and item.uid == skip_uid:
            continue
        section = item.section or "applications"
        if len(items_by_section[section]) < max_per_section * 2:
            items_by_section[section].append(item)

    sections = summarize_newsletter(dict(items_by_section), config)

    # The summarizer swallows API errors and returns blanks — that is how these
    # issues came out empty in the first place. Refuse to overwrite with another blank.
    had_items = [s for s in sections.values() if s.items]
    written = [s for s in had_items if (s.intro or s.summary)]
    if had_items and not written:
        raise SystemExit(f"{day}: every Claude call failed — not writing a blank issue")

    render(sections, config, DOCS, date=run_time)
    logger.info(f"{day}: {len(written)}/{len(had_items)} sections written")


@click.command()
@click.argument("days", nargs=-1)
@click.option("--empty", is_flag=True, help="Rebuild every blank issue that has a snapshot")
@click.option("--items-dir", default=str(ITEMS_DIR), help="Snapshot directory to replay from")
@click.option("--reconstructed", is_flag=True, help="Label issues as reconstructed (partial coverage)")
@click.option("--config", "config_path", default="config.yaml")
def cli(days: tuple[str, ...], empty: bool, items_dir: str, reconstructed: bool, config_path: str):
    items_path = Path(items_dir)
    targets = list(days) + (empty_days(items_path) if empty else [])
    if not targets:
        logger.error("Nothing to backfill")
        sys.exit(1)
    config = load_config(config_path)
    logger.info(f"Backfilling {len(targets)} issue(s): {', '.join(targets)}")
    for day in targets:
        rebuild(day, config, items_path, reconstructed)


if __name__ == "__main__":
    cli()
