"""AI & Tech Newsletter Generator — main entry point.

Usage:
    python main.py                         # run with defaults from config.yaml
    python main.py --since 3               # look back 3 days
    python main.py --dry-run               # skip Claude API calls
    python main.py --no-email              # generate only, don't send
    python main.py --output-dir ./output   # custom output directory
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
import yaml
from dotenv import load_dotenv
from loguru import logger

# Load .env from the project root (same dir as this file)
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

# ── Logger setup ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}", level="INFO")
logger.add("logs/newsletter_{time:YYYY-MM-DD}.log", rotation="1 day", retention="30 days", level="DEBUG")


def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


@click.command()
@click.option("--since", default=None, type=int, help="Lookback window in days (overrides config)")
@click.option("--dry-run", is_flag=True, help="Fetch sources but skip Claude API calls")
@click.option("--no-email", is_flag=True, help="Generate newsletter but do not send via Gmail")
@click.option("--config", "config_path", default="config.yaml", help="Path to config.yaml")
@click.option("--output-dir", default="output", help="Base output directory")
@click.option("--no-full-text", is_flag=True, help="Skip full article text fetching (faster)")
@click.option("--sections-only", default=None, help="Comma-separated section IDs to generate (debug)")
def cli(
    since: int | None,
    dry_run: bool,
    no_email: bool,
    config_path: str,
    output_dir: str,
    no_full_text: bool,
    sections_only: str | None,
):
    """Generate the AI & Tech Intelligence newsletter."""
    config = load_config(config_path)
    newsletter_cfg = config.get("newsletter", {})

    # Resolve lookback window
    since_days = since if since is not None else newsletter_cfg.get("since_days", 1)
    since_dt = datetime.now(tz=timezone.utc) - timedelta(days=since_days)
    logger.info(f"Looking back {since_days} day(s) — since {since_dt.strftime('%Y-%m-%d %H:%M UTC')}")

    if dry_run:
        logger.info("DRY RUN mode — Claude API calls will be skipped")

    # ── 1. Fetch ──────────────────────────────────────────────────────────────
    from fetchers import (
        fetch_blogs,
        fetch_hacker_news,
        fetch_news_rss,
        fetch_podcasts,
        fetch_reddit,
        fetch_research,
        fetch_substacks,
        fetch_twitter,
    )

    logger.info("=" * 60)
    logger.info("STEP 1/5 — Fetching sources")
    logger.info("=" * 60)

    all_items = []
    fetchers = [
        ("News RSS",    lambda: fetch_news_rss(config, since_dt)),
        ("Substacks",   lambda: fetch_substacks(config, since_dt)),
        ("Blogs",       lambda: fetch_blogs(config, since_dt)),
        ("Podcasts",    lambda: fetch_podcasts(config, since_dt)),
        ("Hacker News", lambda: fetch_hacker_news(config, since_dt)),
        ("Reddit",      lambda: fetch_reddit(config, since_dt)),
        ("Twitter/X",   lambda: fetch_twitter(config, since_dt)),
        ("Research",    lambda: fetch_research(config, since_dt)),
    ]

    for name, fn in fetchers:
        try:
            items = fn()
            logger.info(f"  ✓ {name}: {len(items)} items")
            all_items.extend(items)
        except Exception as exc:
            logger.error(f"  ✗ {name}: FAILED — {exc}")

    logger.info(f"Total fetched: {len(all_items)} items")

    if not all_items:
        logger.error("No items fetched — aborting")
        raise SystemExit(1)

    # ── 2. Deduplicate ────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 2/5 — Deduplicating")
    logger.info("=" * 60)

    pre_dedup_items = list(all_items)

    from processors import deduplicate
    dedup_threshold = newsletter_cfg.get("dedup_title_threshold", 88)
    all_items = deduplicate(all_items, title_threshold=dedup_threshold)

    # ── 3. Score + classify ───────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 3/5 — Scoring & classifying")
    logger.info("=" * 60)

    from processors import score_and_classify
    all_items = score_and_classify(all_items)

    # ── 4. Enrich full text ───────────────────────────────────────────────────
    if not no_full_text:
        logger.info("=" * 60)
        logger.info("STEP 3b — Fetching full article text")
        logger.info("=" * 60)
        from processors import enrich_full_text
        all_items = enrich_full_text(all_items)

    # ── 5. Cluster ────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 4/5 — Clustering stories")
    logger.info("=" * 60)

    from processors import cluster_items
    all_items = cluster_items(
        all_items,
        threshold=newsletter_cfg.get("cluster_similarity_threshold", 0.82),
        backend=newsletter_cfg.get("embedding_backend", "local"),
        model_name=newsletter_cfg.get("local_embedding_model", "all-MiniLM-L6-v2"),
    )

    # ── 5b. Track source performance ─────────────────────────────────────────
    try:
        from scripts.track_sources import track as track_sources
        track_sources(pre_dedup_items, all_items)
        logger.info("  ✓ Source performance logged")
    except Exception as exc:
        logger.warning(f"  ✗ Source tracking failed (non-fatal): {exc}")

    # ── 6. Group by section ───────────────────────────────────────────────────
    max_per_section = newsletter_cfg.get("max_items_per_section", 8)
    items_by_section: dict[str, list] = defaultdict(list)

    # For big_thing: prefer the highest-scoring item from an AI-core section.
    # We exclude signals/papers/worth_reading/podcast (those have their own
    # treatment) AND off_topic (non-AI items that would cause Claude to refuse
    # the Big Thing summary, shipping a refusal as the lead story).
    BIG_THING_CORE_SECTIONS = (
        "infrastructure", "models_research", "tooling_agents", "saas_disruption"
    )
    BIG_THING_EXCLUDED = ("signals", "papers", "worth_reading", "podcast", "off_topic")

    core_candidates = [i for i in all_items if i.section in BIG_THING_CORE_SECTIONS]
    # Fall back to applications (which now requires an AI anchor to be assigned)
    # if no core section produced anything; never fall back to off_topic.
    fallback_candidates = [i for i in all_items if i.section == "applications"]

    top_item = None
    if core_candidates:
        top_item = core_candidates[0]
    elif fallback_candidates:
        top_item = fallback_candidates[0]

    if top_item is not None:
        top_item.section = "big_thing"
        items_by_section["big_thing"] = [top_item]
        skip_uid = top_item.uid
        logger.info(f"  Big Thing pick: [{top_item.source_name}] {top_item.title[:90]}")
    else:
        skip_uid = None
        logger.warning("  No AI-relevant item available for Big Thing — section will be skipped")

    for item in all_items:
        if skip_uid and item.uid == skip_uid:
            continue
        section = item.section or "applications"
        if sections_only:
            allowed = [s.strip() for s in sections_only.split(",")]
            if section not in allowed:
                continue
        if len(items_by_section[section]) < max_per_section * 2:
            items_by_section[section].append(item)

    # ── 7. Summarize ──────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("STEP 5/5 — LLM summarization")
    logger.info("=" * 60)

    from processors import summarize_newsletter
    sections = summarize_newsletter(dict(items_by_section), config, dry_run=dry_run)

    # ── 8. Render ─────────────────────────────────────────────────────────────
    from processors.renderer import render
    today = datetime.now(tz=timezone.utc)
    # When output_dir is "docs", write files flat into docs/ (for GitHub Pages)
    # Otherwise write into output/YYYY-MM-DD/ (local default)
    out = Path(output_dir)
    if out.name == "docs":
        date_dir = out
    else:
        date_dir = out / today.strftime("%Y-%m-%d")
    md_path, html_path = render(sections, config, date_dir, date=today)

    logger.info(f"\nOutput:")
    logger.info(f"  Markdown : {md_path}")
    logger.info(f"  HTML     : {html_path}")

    # ── 9. Email ──────────────────────────────────────────────────────────────
    if not no_email and not dry_run:
        from processors.emailer import send_newsletter
        subject = f"{newsletter_cfg.get('title', 'AI Newsletter')} — {today.strftime('%B %d, %Y')}"
        send_newsletter(
            subject=subject,
            html_content=html_path.read_text(encoding="utf-8"),
            md_content=md_path.read_text(encoding="utf-8"),
        )

    logger.info("\nDone.")


if __name__ == "__main__":
    cli()
