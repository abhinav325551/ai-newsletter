"""HTML/JSON scrapers for LLM labs that don't expose RSS.

Two strategies:

1. **HTML scraping** (`scrape_lab_blog`) — fetch the lab's news/blog index
   page and parse with BeautifulSoup using per-site selectors. Used for
   Anthropic and Allen Institute (AI2), which gate or have no RSS but
   serve their blog index as crawlable HTML.

2. **HuggingFace org API** (`fetch_hf_org_releases`) — for labs that
   don't have any public blog index but DO publish their model releases
   on HuggingFace (xAI, DeepSeek, Perplexity, Stability, 01.AI). The
   `/api/models?author=ORG&sort=lastModified` JSON endpoint is the
   cleanest signal: every new model card is a release event.

Both return ``list[FeedItem]`` so they slot into the existing pipeline.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from .base import FeedItem

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:122.0) "
    "Gecko/20100101 Firefox/122.0"
)
_TIMEOUT = 15.0


# ── HTML blog scrapers ───────────────────────────────────────────────────────

def _http_get(url: str) -> str | None:
    """Fetch a URL with the realistic UA and return text, or None on failure."""
    try:
        with httpx.Client(
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            timeout=_TIMEOUT,
        ) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.text
    except Exception as exc:
        logger.warning(f"[LabScraper] GET {url}: {exc}")
        return None


# Months → 2-digit number for date parsing (Anthropic / AI2 use English)
_MONTHS = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], start=1
)}


def _parse_loose_date(text: str) -> datetime | None:
    """Parse ad-hoc date strings like 'Jun 9, 2026' or 'May 2026'."""
    text = text.strip()
    # "Jun 9, 2026" or "Jun 9 2026"
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", text, re.I)
    if m:
        mo, d, y = m.group(1).lower()[:3], int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, _MONTHS[mo], d, tzinfo=timezone.utc)
        except ValueError:
            pass
    # "May 2026" — fallback to mid-month
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{4})", text, re.I)
    if m:
        mo, y = m.group(1).lower()[:3], int(m.group(2))
        try:
            return datetime(y, _MONTHS[mo], 15, tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def scrape_anthropic(name: str, url: str, weight: float, since: datetime | None) -> list[FeedItem]:
    """Anthropic news: anchors like <a href="/news/SLUG">...nested h2/h4 title..."""
    html = _http_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items: list[FeedItem] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=re.compile(r"^/news/[a-z0-9-]+/?$")):
        href = a.get("href")
        if href in seen:
            continue
        seen.add(href)
        # Title: prefer nested heading; fall back to slug-titleize
        h = a.find(["h1", "h2", "h3", "h4"])
        title = h.get_text(strip=True) if h else ""
        if not title:
            slug = href.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").title()
        # Date: look for "Jun 9, 2026"-style text inside the anchor
        pub = _parse_loose_date(a.get_text(separator=" "))
        if since and pub and pub < since:
            continue
        items.append(FeedItem(
            url=f"https://www.anthropic.com{href}",
            title=title,
            source_name=name,
            source_type="blog",
            summary="",
            published_at=pub,
            source_weight=weight,
        ))
    logger.info(f"[LabScraper] {name}: {len(items)} items")
    return items


def scrape_ai2(name: str, url: str, weight: float, since: datetime | None) -> list[FeedItem]:
    """AI2 (allenai.org/news): /blog/SLUG anchors; titles live in parent <h2>
    with format 'Month Year-Title'."""
    html = _http_get(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items: list[FeedItem] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=re.compile(r"^/blog/[a-z0-9-]+/?$")):
        href = a.get("href")
        if href in seen:
            continue
        seen.add(href)
        # Walk up looking for an h2/h3 in the card container
        parent = a
        title, pub = "", None
        for _ in range(6):
            parent = parent.parent
            if parent is None:
                break
            h = parent.find(["h1", "h2", "h3"]) if hasattr(parent, "find") else None
            if h:
                raw = h.get_text(strip=True)
                # Format observed: "May 2026-Title here"
                if "-" in raw:
                    date_part, _, title_part = raw.partition("-")
                    pub = _parse_loose_date(date_part)
                    title = title_part.strip()
                else:
                    title = raw
                break
        if not title:
            slug = href.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").capitalize()
        if since and pub and pub < since:
            continue
        items.append(FeedItem(
            url=f"https://allenai.org{href}",
            title=title,
            source_name=name,
            source_type="blog",
            summary="",
            published_at=pub,
            source_weight=weight,
        ))
    logger.info(f"[LabScraper] {name}: {len(items)} items")
    return items


_BLOG_SCRAPERS: dict[str, Callable[[str, str, float, datetime | None], list[FeedItem]]] = {
    "anthropic": scrape_anthropic,
    "ai2":       scrape_ai2,
}


def fetch_lab_scrapers(config: dict, since: datetime | None = None) -> list[FeedItem]:
    """Run all configured HTML lab scrapers."""
    all_items: list[FeedItem] = []
    for cfg in config.get("lab_scrapers", []) or []:
        scraper_type = cfg.get("type", "")
        fn = _BLOG_SCRAPERS.get(scraper_type)
        if fn is None:
            logger.warning(f"[LabScraper] Unknown type '{scraper_type}' for {cfg.get('name','?')}")
            continue
        try:
            items = fn(cfg["name"], cfg["url"], cfg.get("weight", 0.85), since)
            all_items.extend(items)
        except Exception as exc:
            logger.error(f"[LabScraper] {cfg.get('name','?')} failed: {exc}")
    return all_items


# ── HuggingFace org model-release API ────────────────────────────────────────

def fetch_hf_org_releases(
    name: str, org: str, weight: float, since: datetime | None, limit: int = 12
) -> list[FeedItem]:
    """Fetch recent model releases from a HuggingFace org as FeedItems.

    Uses HF's public JSON API. No auth needed for public models.
    """
    url = f"https://huggingface.co/api/models?author={org}&sort=lastModified&limit={limit}"
    try:
        with httpx.Client(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_TIMEOUT,
        ) as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as exc:
        logger.warning(f"[LabScraper/HF] {name}: {exc}")
        return []

    items: list[FeedItem] = []
    for m in data:
        model_id = m.get("modelId") or m.get("id") or ""
        if not model_id:
            continue
        # Parse lastModified — ISO 8601 like "2026-06-08T14:21:33.000Z"
        raw_date = m.get("lastModified") or m.get("createdAt") or ""
        try:
            pub = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            pub = None
        if since and pub and pub < since:
            continue

        # Title: "<Lab> releases <model>" framing — makes it look like news
        model_short = model_id.split("/", 1)[-1]
        title = f"{name} releases {model_short}"

        # Build a short summary from pipeline tag + downloads
        pipeline = m.get("pipeline_tag", "")
        downloads = m.get("downloads", 0)
        likes = m.get("likes", 0)
        bits = []
        if pipeline:
            bits.append(f"pipeline: {pipeline}")
        if downloads:
            bits.append(f"{downloads:,} downloads")
        if likes:
            bits.append(f"{likes:,} likes")
        summary = " · ".join(bits) if bits else "New model release"

        items.append(FeedItem(
            url=f"https://huggingface.co/{model_id}",
            title=title,
            source_name=name,
            source_type="blog",
            summary=summary,
            published_at=pub,
            source_weight=weight,
        ))
    logger.info(f"[LabScraper/HF] {name} ({org}): {len(items)} releases")
    return items


def fetch_hf_orgs(config: dict, since: datetime | None = None) -> list[FeedItem]:
    """Run all configured HuggingFace org fetchers."""
    all_items: list[FeedItem] = []
    for cfg in config.get("hf_orgs", []) or []:
        try:
            items = fetch_hf_org_releases(
                name=cfg["name"],
                org=cfg["org"],
                weight=cfg.get("weight", 0.85),
                since=since,
            )
            all_items.extend(items)
        except Exception as exc:
            logger.error(f"[LabScraper/HF] {cfg.get('name','?')} failed: {exc}")
    return all_items
