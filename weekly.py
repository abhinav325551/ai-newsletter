"""AI & Tech Newsletter — Weekly Saturday Digest.

Runs every Saturday morning. Replaces the prior Sunday source-audit issue.

Pipeline:
  1. Gather the past 7 days of daily issues from docs/ (JSON sidecars when
     available, falling back to parsing the .md for older days).
  2. Score sources over 30 days (reuses scripts/track_sources.score_sources).
  3. Compute week-over-week deltas — what's new, what stopped delivering.
  4. Run Claude theme synthesis to produce a "This Week in AI" essay.
  5. Discover new source candidates (reuses scripts/discover_sources).
  6. Open a config PR if cold sources need disabling or candidates can be added.
  7. Render docs/{date}-weekly.{md,html,json} and email it.

Usage:
    python weekly.py                # full run
    python weekly.py --dry-run      # skip Claude + email + PR creation
    python weekly.py --no-email     # render only
    python weekly.py --no-pr        # render + email but don't auto-PR config
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader
from loguru import logger

# Project root
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
load_dotenv(dotenv_path=ROOT / ".env", override=True)

# Logger setup (mirrors main.py)
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    level="INFO",
)
logger.add("logs/weekly_{time:YYYY-MM-DD}.log", rotation="1 day", retention="90 days", level="DEBUG")

DOCS = ROOT / "docs"
PERF_LOG = ROOT / "sources" / "performance.jsonl"


# ── Data gathering ────────────────────────────────────────────────────────────

def _date_range(end_date: datetime, days: int) -> list[str]:
    """Return YYYY-MM-DD strings for the `days` days ending at `end_date` (inclusive)."""
    return [(end_date - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]


def load_week_sidecars(end_date: datetime, days: int = 7) -> list[dict]:
    """Load .json sidecars for each day in the window. Missing days are skipped silently."""
    out = []
    for d in _date_range(end_date, days):
        p = DOCS / f"{d}.json"
        if p.exists():
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as exc:
                logger.warning(f"Failed to parse {p}: {exc}")
    return out


# ── Markdown fallback parser ─────────────────────────────────────────────────
# For days before JSON sidecars existed, parse what we can from the .md file.

_BIG_THING_RE = re.compile(r"^##\s+The Big Thing:\s+(.+)$", re.MULTILINE | re.IGNORECASE)
_BIG_THING_SECTION_RE = re.compile(
    r"^##\s+1\.\s+The Big Thing\s*$(.+?)(?=^##\s+2\.)",
    re.MULTILINE | re.IGNORECASE | re.DOTALL,
)
_LINK_RE = re.compile(r"\*\*\[([^\]]+)\]\*\*\s+\[([^\]]+)\]\(([^)]+)\)")


def parse_md_fallback(date_str: str) -> dict | None:
    """Best-effort parse of a daily .md when no sidecar exists."""
    md_path = DOCS / f"{date_str}.md"
    if not md_path.exists():
        return None
    text = md_path.read_text(encoding="utf-8")

    # Big Thing headline — try colon-form first, then fall back to first bolded
    # phrase inside the Big Thing section, then first non-empty prose line.
    big_thing_title = None
    bt_match = _BIG_THING_RE.search(text)
    if bt_match:
        big_thing_title = bt_match.group(1).strip()
    else:
        sect_match = _BIG_THING_SECTION_RE.search(text)
        if sect_match:
            body = sect_match.group(1)
            # Strip the secondary `## ...` heading if present
            body = re.sub(r"^##.*?$", "", body, count=1, flags=re.MULTILINE).strip()
            # Prefer first bolded phrase
            bold = re.search(r"\*\*([^*]{8,140})\*\*", body)
            if bold:
                big_thing_title = bold.group(1).strip()
            else:
                # Fall back to first sentence (up to 140 chars)
                first_line = next((l.strip() for l in body.split("\n") if l.strip()), "")
                if first_line:
                    big_thing_title = first_line[:140].rstrip(".") + ("…" if len(first_line) > 140 else "")

    # Extract `**[Source]** [Title](url)` style links (signals/worth_reading/papers items)
    items = [
        {"source_name": src, "title": title, "url": url, "source_type": "", "score": 0}
        for src, title, url in _LINK_RE.findall(text)
    ]

    return {
        "date": date_str,
        "big_thing_title": big_thing_title,
        "items": items,
        "fallback": True,
    }


def extract_big_things(sidecars: list[dict], window: list[str]) -> list[dict]:
    """Pull each day's Big Thing — from sidecar where possible, fallback to .md parse."""
    by_date = {sc["date"]: sc for sc in sidecars}
    out = []
    for d in window:
        sc = by_date.get(d)
        if sc:
            bt_sec = next((s for s in sc.get("sections", []) if s.get("id") == "big_thing"), None)
            if bt_sec and bt_sec.get("items"):
                top = bt_sec["items"][0]
                # Strip "The Big Thing: " prefix if present in title
                title = top.get("title", "").strip()
                # Sometimes the Claude-generated summary headline is in section intro; use item title
                out.append({"date": d, "title": title or "(untitled)", "url": top.get("url", "")})
                continue
            # Sidecar without explicit big_thing item — fall back to parsing the .md
        fallback = parse_md_fallback(d)
        if fallback and fallback.get("big_thing_title"):
            out.append({"date": d, "title": fallback["big_thing_title"], "url": ""})
    return out


def build_story_index(sidecars: list[dict], window: list[str]) -> list[dict]:
    """Per-day, per-section list of items. Returns [] if no sidecars exist."""
    by_date = {sc["date"]: sc for sc in sidecars}
    index = []
    SKIP_SECTIONS = {"big_thing"}  # big_thing already shown separately
    for d in window:
        sc = by_date.get(d)
        if not sc:
            continue
        day_sections = []
        for sec in sc.get("sections", []):
            if sec.get("id") in SKIP_SECTIONS:
                continue
            if not sec.get("items"):
                continue
            day_sections.append({"title": sec.get("title", sec.get("id", "")), "items": sec["items"]})
        if day_sections:
            index.append({"date": d, "sections": day_sections})
    return index


def daily_summaries_for_synthesis(sidecars: list[dict]) -> list[dict]:
    """Compact form of each day's section summaries for the Claude theme prompt."""
    out = []
    for sc in sidecars:
        out.append({
            "date": sc["date"],
            "section_summaries": {
                sec.get("id", ""): sec.get("summary") or sec.get("intro") or ""
                for sec in sc.get("sections", [])
            },
        })
    return out


# ── Source scope ─────────────────────────────────────────────────────────────

def load_perf_window(start: datetime, end: datetime) -> list[dict]:
    """Load performance.jsonl rows in [start, end] inclusive."""
    if not PERF_LOG.exists():
        return []
    s = start.strftime("%Y-%m-%d")
    e = end.strftime("%Y-%m-%d")
    out = []
    with PERF_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if s <= r.get("date", "") <= e:
                    out.append(r)
            except json.JSONDecodeError:
                continue
    return out


def sources_active_in(rows: list[dict]) -> set[str]:
    """Source names that delivered at least one fetched item in the window."""
    return {r["source_name"] for r in rows if r.get("fetched", 0) > 0}


# ── Markdown → HTML for the theme essay ──────────────────────────────────────

def _markdown_to_html(text: str) -> str:
    """Tiny markdown converter for Claude's essay output. Avoids a new dependency."""
    if not text:
        return ""
    html_lines = []
    in_para = False
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line:
            if in_para:
                html_lines.append("</p>")
                in_para = False
            continue
        # Headings
        if line.startswith("### "):
            if in_para:
                html_lines.append("</p>")
                in_para = False
            html_lines.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            if in_para:
                html_lines.append("</p>")
                in_para = False
            html_lines.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            if in_para:
                html_lines.append("</p>")
                in_para = False
            html_lines.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith(("- ", "* ")):
            if in_para:
                html_lines.append("</p>")
                in_para = False
            html_lines.append(f"<ul><li>{_inline(line[2:])}</li></ul>")
        else:
            if not in_para:
                html_lines.append("<p>")
                in_para = True
            html_lines.append(_inline(line))
    if in_para:
        html_lines.append("</p>")
    return "\n".join(html_lines)


def _inline(s: str) -> str:
    # Bold
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    # Italic
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", s)
    # Links
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    return s


# ── Render ───────────────────────────────────────────────────────────────────

def render_weekly(ctx: dict, out_dir: Path, date_str: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=False)

    md_tmpl = env.get_template("weekly.md.j2")
    md_path = out_dir / f"{date_str}-weekly.md"
    md_path.write_text(md_tmpl.render(**ctx), encoding="utf-8")
    logger.info(f"[Weekly] Markdown → {md_path}")

    html_tmpl = env.get_template("weekly.html.j2")
    html_path = out_dir / f"{date_str}-weekly.html"
    html_path.write_text(html_tmpl.render(**ctx), encoding="utf-8")
    logger.info(f"[Weekly] HTML → {html_path}")

    return md_path, html_path


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly Saturday digest")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude + email + PR")
    parser.add_argument("--no-email", action="store_true", help="Render only, don't email")
    parser.add_argument("--no-pr", action="store_true", help="Don't open a config PR for cold/discovered sources")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output-dir", default="docs")
    args = parser.parse_args()

    today = datetime.now(tz=timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    week_end = today
    week_start = today - timedelta(days=6)

    logger.info("=" * 60)
    logger.info(f"WEEKLY DIGEST — {date_str}")
    logger.info(f"Window: {week_start.strftime('%Y-%m-%d')} → {week_end.strftime('%Y-%m-%d')}")
    logger.info("=" * 60)

    # ── 1. Gather daily sidecars (and parse .md fallbacks for older days) ────
    window = _date_range(week_end, 7)
    sidecars = load_week_sidecars(week_end, days=7)
    logger.info(f"Found {len(sidecars)} structured sidecars (of 7 days)")

    big_things = extract_big_things(sidecars, window)
    logger.info(f"Big Things parsed: {len(big_things)}")

    story_index = build_story_index(sidecars, window)
    daily_summaries = daily_summaries_for_synthesis(sidecars)

    # ── 2. Source scope: this-week vs prior-week active sets ────────────────
    this_week_rows = load_perf_window(week_start, week_end)
    prior_start = week_start - timedelta(days=7)
    prior_end = week_start - timedelta(days=1)
    prior_week_rows = load_perf_window(prior_start, prior_end)

    active_this_week = sources_active_in(this_week_rows)
    active_prior_week = sources_active_in(prior_week_rows)
    new_sources = sorted(active_this_week - active_prior_week)
    dropped_sources = sorted(active_prior_week - active_this_week)

    logger.info(f"Active this week: {len(active_this_week)}  | prior: {len(active_prior_week)}")
    logger.info(f"New: {len(new_sources)}  | Stopped: {len(dropped_sources)}")

    # ── 3. 30-day source scoring + audit (reuse existing modules) ───────────
    from scripts.track_sources import score_sources
    from scripts.audit_sources import (
        find_cold_sources, find_poor_performers,
        disable_in_config, add_candidates_to_config,
        load_config, save_config, create_pr, SILENCE_THRESHOLD,
    )
    from scripts.discover_sources import discover

    scores = score_sources(days=30)
    logger.info(f"Scored {len(scores)} sources over 30d")

    cold = find_cold_sources(scores)
    poor = find_poor_performers(scores)
    logger.info(f"Cold: {len(cold)}  | Poor: {len(poor)}")

    top10 = sorted(scores.items(), key=lambda x: x[1]["composite"], reverse=True)[:10]
    top_performers = [
        {
            "name": name,
            "type": s["source_type"],
            "composite": s["composite"],
            "hit_rate": s["hit_rate"],
            "freshness": s["freshness"],
            "impact": s["impact"],
            "active_days": s["active_days"],
        }
        for name, s in top10
    ]

    # ── 4. Discover candidates ──────────────────────────────────────────────
    config = load_config()
    if args.dry_run:
        candidates = []
        logger.info("[dry-run] Skipping discovery")
    else:
        try:
            candidates = discover(config, dry_run=False) or []
        except Exception as exc:
            logger.warning(f"Discovery failed (non-fatal): {exc}")
            candidates = []
    logger.info(f"Discovered candidates: {len(candidates)}")

    # ── 5. Apply config changes + open PR ───────────────────────────────────
    config_pr_url = None
    if not args.dry_run and not args.no_pr:
        sources_to_disable = {s["name"] for s in cold}
        disabled_names: list[str] = []
        added_names: list[str] = []
        if sources_to_disable:
            config, disabled_names = disable_in_config(config, sources_to_disable)
        if candidates:
            config, added_names = add_candidates_to_config(config, candidates)
        if disabled_names or added_names:
            save_config(config)
            added_candidates = [c for c in candidates if c["name"] in set(added_names)]
            try:
                config_pr_url = create_pr(
                    cold=cold, poor=poor,
                    added=added_candidates,
                    disabled_names=disabled_names,
                    dry_run=False,
                )
                logger.info(f"Config PR: {config_pr_url}")
            except Exception as exc:
                logger.warning(f"PR creation failed (non-fatal): {exc}")

    # ── 6. Theme synthesis via Claude ───────────────────────────────────────
    themes_essay = ""
    if not args.dry_run:
        from processors.weekly_synthesizer import synthesize_themes
        themes_essay = synthesize_themes(
            daily_summaries=daily_summaries,
            big_things=big_things,
            source_scope_delta={
                "this_week": len(active_this_week),
                "last_week": len(active_prior_week),
                "new_sources": new_sources,
                "dropped_sources": dropped_sources,
            },
        )
        logger.info(f"Themes essay length: {len(themes_essay)} chars")

    # ── 7. Build template context ───────────────────────────────────────────
    delta_count = len(active_this_week) - len(active_prior_week)
    ctx = {
        "date": date_str,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": week_end.strftime("%Y-%m-%d"),
        "days_covered": len(sidecars) + sum(
            1 for d in window
            if not any(sc["date"] == d for sc in sidecars) and (DOCS / f"{d}.md").exists()
        ),
        "total_sources_active": len(active_this_week),
        "total_sources_prior": len(active_prior_week),
        "delta_count": abs(delta_count),
        "delta_sign": "+" if delta_count >= 0 else "−",
        "new_sources": new_sources,
        "dropped_sources": dropped_sources,
        "cold_count": len(cold),
        "cold_sources": [c["name"] for c in cold],
        "top_performers": top_performers,
        "discovered_candidates": [
            {
                "name": c["name"],
                "url": c.get("url", ""),
                "type": c.get("type", "—"),
                "reason": c.get("reason", "—"),
            }
            for c in candidates
        ],
        "config_pr_url": config_pr_url,
        "big_things": big_things,
        "story_index": story_index,
        "themes_essay": themes_essay,
        "themes_essay_html": _markdown_to_html(themes_essay),
        "model": "claude-sonnet-4-6",
    }

    # ── 8. Render ───────────────────────────────────────────────────────────
    out_dir = Path(args.output_dir)
    md_path, html_path = render_weekly(ctx, out_dir, date_str)

    # Also write the structured weekly summary JSON (handy for index page / future use)
    json_path = out_dir / f"{date_str}-weekly.json"
    json_path.write_text(json.dumps({
        "date": date_str,
        "week_start": ctx["week_start"],
        "week_end": ctx["week_end"],
        "sources_active": len(active_this_week),
        "new_sources": new_sources,
        "dropped_sources": dropped_sources,
        "cold_count": len(cold),
        "top_performers": top_performers,
        "candidates": ctx["discovered_candidates"],
        "config_pr_url": config_pr_url,
    }, indent=2), encoding="utf-8")
    logger.info(f"[Weekly] Summary JSON → {json_path}")

    # ── 9. Email ────────────────────────────────────────────────────────────
    if not args.no_email and not args.dry_run:
        from processors.emailer import send_newsletter
        subject = f"AI & Tech Weekly Review — Week of {ctx['week_start']}"
        send_newsletter(
            subject=subject,
            html_content=html_path.read_text(encoding="utf-8"),
            md_content=md_path.read_text(encoding="utf-8"),
        )

    logger.info("=" * 60)
    logger.info("WEEKLY DIGEST COMPLETE")
    logger.info(f"  Markdown : {md_path}")
    logger.info(f"  HTML     : {html_path}")
    logger.info(f"  Summary  : {json_path}")
    if config_pr_url:
        logger.info(f"  Config PR: {config_pr_url}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
