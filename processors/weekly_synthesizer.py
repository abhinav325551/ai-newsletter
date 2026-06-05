"""Claude-powered weekly theme synthesizer.

Reads the past 7 daily issues + structured item data (if available) and asks
Claude to identify the 3–5 dominant themes of the week. Output is opinionated,
information-dense prose — not a list of headlines.
"""
from __future__ import annotations

import os
from typing import Iterable

import anthropic
from loguru import logger

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096

_SYSTEM = """You are the editor of a respected AI & tech intelligence weekly digest read by \
founders, investors, and researchers. You write the Saturday "week in review" essay. \
Your job is to surface the 3–5 dominant themes of the past week — the stories that \
matter when you zoom out from the daily noise. Be opinionated, precise, and \
information-dense. Cite specific stories with their sources inline. Never fabricate."""


def synthesize_themes(
    daily_summaries: list[dict],
    big_things: list[dict],
    source_scope_delta: dict,
) -> str:
    """
    daily_summaries: [{"date": "2026-06-01", "section_summaries": {section_id: text}}, ...]
    big_things: [{"date": ..., "title": ..., "summary_first_paragraph": ...}, ...]
    source_scope_delta: {"this_week": N, "last_week": M, "new_sources": [...], "dropped_sources": [...]}
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        logger.warning("[Weekly] ANTHROPIC_API_KEY missing — skipping theme synthesis")
        return ""

    client = anthropic.Anthropic(api_key=key)

    # Build context
    lines = ["## Big Stories of the Week\n"]
    for bt in big_things:
        lines.append(f"### {bt['date']}: {bt['title']}")
        if bt.get("summary_first_paragraph"):
            lines.append(bt["summary_first_paragraph"][:600])
        lines.append("")

    if daily_summaries:
        lines.append("## Section Summaries by Day\n")
        for day in daily_summaries:
            lines.append(f"### {day['date']}")
            for sec_id, summary in day.get("section_summaries", {}).items():
                if summary:
                    snippet = summary.replace("\n", " ")[:500]
                    lines.append(f"- **{sec_id}**: {snippet}")
            lines.append("")

    context = "\n".join(lines)
    # Cap context to avoid token blowup
    if len(context) > 25000:
        context = context[:25000] + "\n... [truncated]"

    prompt = f"""Below is a digest of the past week's AI & tech newsletter coverage. \
Write a "Week in Review" essay that identifies the 3–5 dominant themes. \
For each theme:
1. Name it in a short bold headline
2. Write 2–3 paragraphs explaining what happened across the week, citing specific stories with [Source Name] inline
3. End with one sentence on what to watch next week

Open with a single-paragraph editor's note that previews the themes. Close with a \
one-line "Bottom line" assessment of the week.

The point is to find the through-lines the daily issues couldn't see. Look for \
patterns: same theme cited multiple days, escalating storylines, contradictions \
between sources, moves that only make sense when you see the full week.

Source material:
{context}

Write in second person where natural. Be analytical, not breathless."""

    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.error(f"[Weekly] Claude API error during theme synthesis: {exc}")
        return ""
