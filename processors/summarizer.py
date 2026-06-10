"""LLM summarization: uses Claude (claude-sonnet-4-6) to write the newsletter."""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass

import anthropic
from loguru import logger

from fetchers.base import FeedItem

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 4096


@dataclass
class NewsletterSection:
    id: str
    title: str
    intro: str
    items: list[FeedItem]
    summary: str = ""


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def _items_to_context(items: list[FeedItem], max_items: int = 8) -> str:
    lines = []
    for item in items[:max_items]:
        src = f"[{item.source_name}]"
        text = item.full_text or item.summary or ""
        snippet = text[:400].replace("\n", " ")
        lines.append(f"- {src} **{item.title}**\n  {snippet}\n  URL: {item.url}")
    return "\n\n".join(lines)


def _call_claude(client: anthropic.Anthropic, system: str, prompt: str) -> str:
    try:
        msg = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        logger.error(f"[Summarizer] Claude API error: {exc}")
        return ""


_SYSTEM = """You are the editor of a highly respected AI & tech intelligence newsletter read by \
founders, investors, and researchers. Your writing is precise, opinionated, and information-dense. \
Avoid fluff, marketing language, and generic statements. Every sentence must add new information. \
Always attribute claims to specific sources. Flag unconfirmed reports with "(unconfirmed)" or \
"(rumored)". Never fabricate URLs or statistics."""


# Phrases Claude tends to emit when it correctly refuses to write a Big Thing
# about non-AI source material. If any of these show up in the first 600 chars
# of the response, we treat the output as a refusal and drop the section
# rather than shipping the refusal text as the lead story.
_REFUSAL_MARKERS = (
    "i don't have",
    "i'm not able",
    "i cannot",
    "i can't write",
    "what i'd recommend",
    "what you should do",
    "what i can do instead",
    "source material",  # appears in phrases like "the source material doesn't contain"
    "doesn't contain",
    "no meaningful ai angle",
    "no ai angle",
    "fabricating",
    "no legitimate",
    "hold this slot",
)


def _looks_like_refusal(text: str) -> bool:
    if not text:
        return True
    head = text[:600].lower()
    hits = sum(1 for m in _REFUSAL_MARKERS if m in head)
    # Two or more markers in the opening = high-confidence refusal.
    # A very short response (< 400 chars) with even one marker is also suspect.
    if hits >= 2:
        return True
    if hits >= 1 and len(text.strip()) < 400:
        return True
    return False


def _summarize_big_thing(client: anthropic.Anthropic, top_item: FeedItem, related: list[FeedItem]) -> str:
    context = _items_to_context([top_item] + related[:3])
    prompt = f"""Write "The Big Thing" section for today's newsletter.

This is the single most important AI story right now. Write 3–4 substantial paragraphs that:
1. Explain what happened and why it matters
2. Provide relevant context (what led here, who's involved)
3. Analyze implications for the broader AI landscape
4. Note what to watch next

Source material:
{context}

Write in second person ("you") where appropriate. Be direct and analytical."""
    result = _call_claude(client, _SYSTEM, prompt)
    if _looks_like_refusal(result):
        logger.warning(
            f"[Summarizer] Big Thing output looks like a refusal — dropping. "
            f"Top item was [{top_item.source_name}] {top_item.title[:80]}"
        )
        return ""
    return result


def _summarize_section(
    client: anthropic.Anthropic,
    section_id: str,
    section_title: str,
    items: list[FeedItem],
    max_items: int = 8,
) -> tuple[str, str]:
    """Returns (intro_paragraph, formatted_bullets)."""
    context = _items_to_context(items, max_items)

    section_desc = {
        "infrastructure": "chips, data centers, training infrastructure, and inference optimization",
        "models_research": "foundation model releases, open-source model drops, benchmarks, and research papers",
        "tooling_agents": "AI frameworks, agent infrastructure, vector databases, orchestration, and dev tools",
        "applications": "consumer AI products, enterprise AI deployments, and notable product launches",
        "saas_disruption": (
            "how AI is disrupting, augmenting, or displacing existing SaaS businesses. "
            "Identify at least 3 specific companies or software categories being pressured this week. "
            "Be concrete: name the incumbents, name the AI challengers, and explain the mechanism of disruption."
        ),
        "signals": "notable takes, debates, and discussions from Twitter, Reddit, and HN",
        "worth_reading": "long-reads, in-depth essays, podcast episodes worth your time",
        "papers": "the most impactful academic papers published this week",
    }.get(section_id, section_title)

    prompt = f"""Write the **{section_title}** section of the newsletter, covering {section_desc}.

Source material ({len(items)} items):
{context}

Format:
1. A 2-3 sentence opinionated intro paragraph that synthesizes the week's theme in this area.
2. Then 3–6 bullet points, each in this exact format:
   • **[Source Name] Headline or key claim** — one sentence of analysis or context. [Link]({'{url}'})

Rules:
- Prefer original sources over aggregators
- Flag rumors/unconfirmed with "(unconfirmed)"
- Every bullet must have an attribution and a URL
- Be specific: cite numbers, model names, company names where available"""

    result = _call_claude(client, _SYSTEM, prompt)
    # Split intro from bullets if possible
    lines = result.strip().split("\n")
    intro_lines = []
    bullet_lines = []
    in_bullets = False
    for line in lines:
        if line.strip().startswith("•") or line.strip().startswith("-"):
            in_bullets = True
        if in_bullets:
            bullet_lines.append(line)
        else:
            intro_lines.append(line)

    intro = "\n".join(intro_lines).strip()
    bullets = "\n".join(bullet_lines).strip()
    return intro, bullets


def _summarize_saas_disruption(client: anthropic.Anthropic, all_items: list[FeedItem]) -> tuple[str, str]:
    """SaaS disruption section always gets a dedicated pass over all sections."""
    relevant = [
        i for i in all_items
        if i.section in ("saas_disruption", "applications", "tooling_agents")
    ][:20]
    return _summarize_section(client, "saas_disruption", "SaaS Disruption Watch", relevant)


def summarize_newsletter(
    items_by_section: dict[str, list[FeedItem]],
    config: dict,
    dry_run: bool = False,
) -> dict[str, NewsletterSection]:
    """Generate all newsletter sections. Returns dict keyed by section_id."""

    if dry_run:
        logger.info("[Summarizer] Dry run — skipping Claude API calls")
        result: dict[str, NewsletterSection] = {}
        for sec in config.get("sections", []):
            sid = sec["id"]
            sec_items = items_by_section.get(sid, [])
            result[sid] = NewsletterSection(
                id=sid,
                title=sec["title"],
                intro=f"[DRY RUN — {len(sec_items)} items would be summarized here]",
                items=sec_items,
                summary="",
            )
        return result

    client = _client()
    all_items = [i for items in items_by_section.values() for i in items]

    result: dict[str, NewsletterSection] = {}
    sections_cfg = config.get("sections", [])

    for sec in sections_cfg:
        sid = sec["id"]
        sec_items = items_by_section.get(sid, [])
        max_items = config.get("newsletter", {}).get("max_items_per_section", 8)

        logger.info(f"[Summarizer] Writing section: {sec['title']} ({len(sec_items)} items)")

        if not sec_items:
            result[sid] = NewsletterSection(
                id=sid, title=sec["title"], intro="", items=[], summary=""
            )
            continue

        if sid == "big_thing":
            top = sec_items[0]
            body = _summarize_big_thing(client, top, sec_items[1:])
            result[sid] = NewsletterSection(
                id=sid, title=sec["title"], intro=body, items=sec_items[:1], summary=body
            )
        elif sid == "saas_disruption":
            intro, bullets = _summarize_saas_disruption(client, all_items)
            result[sid] = NewsletterSection(
                id=sid, title=sec["title"], intro=intro, items=sec_items, summary=bullets
            )
        else:
            intro, bullets = _summarize_section(client, sid, sec["title"], sec_items, max_items)
            result[sid] = NewsletterSection(
                id=sid, title=sec["title"], intro=intro, items=sec_items, summary=bullets
            )

    return result
