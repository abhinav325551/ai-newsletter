"""Score items for relevance + importance and assign them to newsletter sections."""
from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from loguru import logger

from fetchers.base import FeedItem

# Section keyword patterns (order matters — first match wins except for big_thing)
_SECTION_PATTERNS: list[tuple[str, list[str]]] = [
    ("infrastructure", [
        r"\b(chip|gpu|tpu|npu|h100|h200|b200|gb200|blackwell|hopper|nvidia|amd|intel|cerebras|groq|tenstorrent)\b",
        r"\b(data center|datacenter|power plant|energy|cooling|rack|cluster|hpc)\b",
        r"\b(training run|pre.?training|fine.?tun|inference|throughput|latency|token.s)\b",
        r"\b(foundry|fab|tsmc|samsung|intel foundry|semiconductor)\b",
    ]),
    ("models_research", [
        r"\b(release|launch|announc|open.sourc|weights|checkpoint|model card)\b.{0,60}\b(llm|model|gpt|claude|gemini|llama|mistral|deepseek|grok|qwen)\b",
        r"\b(paper|research|arxiv|benchmark|mmlu|helm|swe.bench|humaneval|reasoning)\b",
        r"\b(foundation model|base model|multimodal|vision language|vl[m]?)\b",
    ]),
    ("tooling_agents", [
        r"\b(agent|agentic|multi.?agent|autonomous|workflow)\b",
        r"\b(langchain|llamaindex|langgraph|dspy|crewai|autogen|openai swarm)\b",
        r"\b(vector (db|database)|pinecone|weaviate|chroma|qdrant|milvus)\b",
        r"\b(rag|retrieval.?augmented|embedding|rerank)\b",
        r"\b(eval|evals|evaluation|red.?team|safety test|benchmark framework)\b",
        r"\b(sdk|api|framework|library|toolkit|middleware|orchestrat)\b",
    ]),
    ("saas_disruption", [
        r"\b(replac|disrupt|disintermediat|kill|dead|obsolet|compet)\b.{0,80}\b(saas|software|startup|company|product)\b",
        r"\b(saas|crm|erp|hris|cms|analytics platform)\b.{0,60}\b(ai|llm|automat)\b",
        r"\b(salesforce|hubspot|zendesk|servicenow|workday|sap|oracle|microsoft)\b.{0,60}\b(ai|compet|threat)\b",
        r"\bai.?native\b",
        r"\b(cursor|devin|copilot|codegen|vibe.?cod)\b",
    ]),
    ("applications", [
        r"\b(app|product|consumer|user|launch|ship|feature|update|v\d[\.\d]*)\b",
        r"\b(chatgpt|claude\.ai|perplexity|character\.ai|midjourney|runway|sora|pika)\b",
        r"\b(enterprise|b2b|workflow|automat|productivity|assistant|copilot)\b",
        r"\b(startup|seed|series [ab]|funding|raises?\b)\b",
    ]),
]

_INFRA_KEYWORDS = re.compile(
    "|".join(_SECTION_PATTERNS[0][1]), re.IGNORECASE
)


def _compute_recency_boost(pub: datetime | None) -> float:
    if not pub:
        return 0.7
    now = datetime.now(tz=timezone.utc)
    hours_old = (now - pub).total_seconds() / 3600
    if hours_old < 6:
        return 1.0
    if hours_old < 24:
        return 0.9
    if hours_old < 72:
        return 0.75
    return 0.55


def _engagement_score(item: FeedItem) -> float:
    """Normalised 0-1 engagement signal."""
    if item.source_type == "hn":
        return min(1.0, math.log1p(item.score) / math.log1p(2000))
    if item.source_type == "reddit":
        return min(1.0, math.log1p(item.score) / math.log1p(5000))
    if item.source_type == "twitter":
        return min(1.0, math.log1p(item.score) / math.log1p(10000))
    return 0.5  # RSS / blog / arxiv — no engagement signal


def _detect_section(item: FeedItem) -> str:
    text = f"{item.title} {item.summary}".lower()
    for section_id, patterns in _SECTION_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                return section_id
    # Fallback by source type
    if item.source_type == "arxiv":
        return "papers"
    if item.source_type == "podcast":
        return "worth_reading"
    if item.source_type in ("twitter", "reddit", "hn"):
        return "signals"
    return "applications"


def score_and_classify(items: list[FeedItem]) -> list[FeedItem]:
    """Compute an importance score and assign a section to each item."""
    for item in items:
        recency = _compute_recency_boost(item.published_at)
        engagement = _engagement_score(item)
        authority = item.source_weight

        # Weighted composite score
        item.score = int(
            (0.35 * authority + 0.35 * recency + 0.30 * engagement) * 100
        )

        if not item.section:
            item.section = _detect_section(item)

    # Sort by score descending within each section
    items.sort(key=lambda x: x.score, reverse=True)

    section_counts = {}
    for item in items:
        section_counts[item.section] = section_counts.get(item.section, 0) + 1

    logger.info(f"[Scorer] Section distribution: {section_counts}")
    return items
