"""
discover_sources.py — Claude-powered source discovery.

Reads recent newsletter content and asks Claude to identify:
1. Outlets / Substacks / blogs being cited that we don't have direct feeds for
2. Their likely RSS URLs
3. Why they're worth adding

Returns a list of candidate dicts ready to paste into config.yaml.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import anthropic
from loguru import logger

DOCS_DIR = Path(__file__).parent.parent / "docs"
CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _existing_sources(config: dict) -> set[str]:
    """Collect all source names + URLs already in config."""
    names = set()
    for section in ["news_rss", "substacks", "blogs", "podcasts"]:
        for s in config.get(section, []):
            names.add(s.get("name", "").lower())
            url = s.get("url", "")
            # extract domain
            m = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if m:
                names.add(m.group(1).lower())
    return names


def _recent_content(max_chars: int = 12000) -> str:
    """Pull text from the most recent newsletter markdown file."""
    mds = sorted(DOCS_DIR.glob("*.md"), reverse=True)
    content = ""
    for md in mds[:3]:
        content += md.read_text(encoding="utf-8", errors="ignore") + "\n\n"
        if len(content) > max_chars:
            break
    return content[:max_chars]


def discover(config: dict, dry_run: bool = False) -> list[dict]:
    """
    Returns a list of candidate source dicts:
    [
      {
        "name": "Example Newsletter",
        "url": "https://example.substack.com/feed",
        "type": "substack",   # news_rss | substack | blog | podcast
        "weight": 0.8,
        "reason": "Cited 4 times in last 3 issues; covers AI inference research"
      },
      ...
    ]
    """
    if dry_run:
        logger.info("[Discover] Dry run — skipping Claude API call")
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("[Discover] ANTHROPIC_API_KEY not set — skipping discovery")
        return []

    existing = _existing_sources(config)
    content = _recent_content()

    if not content.strip():
        logger.warning("[Discover] No recent newsletter content found")
        return []

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""You are a research assistant helping curate an AI & tech newsletter.

Below is content from recent newsletter issues. Your job is to identify sources that are:
1. Cited, quoted, or linked to repeatedly in the content
2. NOT already in our feed (I'll give you the existing list)
3. Likely to have a public RSS feed we can subscribe to
4. Highly relevant to AI/ML/tech infrastructure/SaaS

Existing sources (already subscribed — do NOT suggest these):
{json.dumps(sorted(existing), indent=2)}

Recent newsletter content:
---
{content}
---

Return ONLY a JSON array of up to 8 candidate sources. Each entry:
{{
  "name": "Human-readable name",
  "url": "Best guess at RSS feed URL (e.g. https://example.substack.com/feed)",
  "type": "substack|news_rss|blog|podcast",
  "weight": 0.7,
  "reason": "1-2 sentences: why this source, how often cited, what it covers"
}}

Rules:
- Only include sources with high confidence of having an RSS feed
- Weight range: 0.65 (general) to 0.95 (must-read primary source)
- If unsure of RSS URL format, use standard patterns:
    Substack: https://[name].substack.com/feed
    WordPress: https://[domain]/feed
    Ghost: https://[domain]/rss
- Return [] if no strong candidates found
- Return ONLY the JSON array, no other text"""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        # Extract JSON array from response
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            logger.warning("[Discover] Claude returned no JSON array")
            return []
        candidates = json.loads(match.group())
        logger.info(f"[Discover] Claude proposed {len(candidates)} new source candidates")
        return candidates
    except Exception as exc:
        logger.error(f"[Discover] Claude discovery failed: {exc}")
        return []
