"""
track_sources.py — runs after every newsletter generation.

Appends one JSON line per source to sources/performance.jsonl:
{
  "date": "2026-04-13",
  "source_name": "TechCrunch",
  "source_type": "news",
  "fetched": 13,
  "passed_filter": 8,
  "section_placements": {"applications": 2, "infrastructure": 1},
  "top_placement": true,     # made it to big_thing / infrastructure / models
  "big_thing": false         # was the top story
}
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PERF_LOG = Path(__file__).parent.parent / "sources" / "performance.jsonl"
TOP_SECTIONS = {"big_thing", "infrastructure", "models_research"}


def track(all_items: list, scored_items: list) -> None:
    """
    all_items    — raw FeedItems before dedup/scoring
    scored_items — FeedItems after scoring + section assignment
    """
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Count raw fetched per source
    fetched: dict[str, int] = defaultdict(int)
    source_types: dict[str, str] = {}
    for item in all_items:
        fetched[item.source_name] += 1
        source_types[item.source_name] = item.source_type

    # Count scored (passed filter) per source
    passed: dict[str, int] = defaultdict(int)
    placements: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    top_placed: dict[str, bool] = defaultdict(bool)
    big_thing: dict[str, bool] = defaultdict(bool)

    for item in scored_items:
        passed[item.source_name] += 1
        if item.section:
            placements[item.source_name][item.section] += 1
        if item.section in TOP_SECTIONS:
            top_placed[item.source_name] = True
        if item.section == "big_thing":
            big_thing[item.source_name] = True

    # Write one line per source
    PERF_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PERF_LOG.open("a") as f:
        all_sources = set(fetched.keys()) | set(passed.keys())
        for source in sorted(all_sources):
            record = {
                "date": today,
                "source_name": source,
                "source_type": source_types.get(source, "unknown"),
                "fetched": fetched.get(source, 0),
                "passed_filter": passed.get(source, 0),
                "section_placements": dict(placements.get(source, {})),
                "top_placement": top_placed.get(source, False),
                "big_thing": big_thing.get(source, False),
            }
            f.write(json.dumps(record) + "\n")


def load_history(days: int = 30) -> list[dict]:
    """Load performance records for the last N days."""
    if not PERF_LOG.exists():
        return []
    from datetime import timedelta
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    records = []
    with PERF_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("date", "") >= cutoff:
                    records.append(rec)
            except json.JSONDecodeError:
                pass
    return records


def score_sources(days: int = 30) -> dict[str, dict]:
    """
    Returns a dict of source_name → score breakdown for the last N days.

    Metrics:
      hit_rate      = avg(passed_filter / fetched) across days with fetches
      freshness     = days_with_items / total_active_days
      impact        = top_placement_rate (% of active days with top section)
      composite     = 0.4*hit_rate + 0.35*freshness + 0.25*impact
    """
    records = load_history(days)
    if not records:
        return {}

    from collections import defaultdict
    by_source: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_source[rec["source_name"]].append(rec)

    total_days = len({rec["date"] for rec in records})
    scores = {}

    for source, recs in by_source.items():
        days_with_fetches = [r for r in recs if r["fetched"] > 0]
        days_with_items   = [r for r in recs if r["passed_filter"] > 0]
        days_top          = [r for r in recs if r.get("top_placement")]

        hit_rate  = (
            sum(r["passed_filter"] / r["fetched"] for r in days_with_fetches) / len(days_with_fetches)
            if days_with_fetches else 0.0
        )
        freshness = len(days_with_items) / total_days if total_days else 0.0
        impact    = len(days_top) / total_days if total_days else 0.0
        composite = 0.4 * hit_rate + 0.35 * freshness + 0.25 * impact

        # Silence = consecutive days with 0 fetched (most recent first)
        sorted_recs = sorted(recs, key=lambda r: r["date"], reverse=True)
        silent_days = 0
        for r in sorted_recs:
            if r["fetched"] == 0:
                silent_days += 1
            else:
                break

        scores[source] = {
            "source_type": recs[0].get("source_type", "unknown"),
            "hit_rate": round(hit_rate, 3),
            "freshness": round(freshness, 3),
            "impact": round(impact, 3),
            "composite": round(composite, 3),
            "silent_days": silent_days,
            "total_fetched": sum(r["fetched"] for r in recs),
            "total_placed": sum(r["passed_filter"] for r in recs),
            "big_thing_count": sum(1 for r in recs if r.get("big_thing")),
            "active_days": len(days_with_items),
        }

    return scores
