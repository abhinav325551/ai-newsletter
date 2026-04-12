"""Research fetcher: arXiv and Papers With Code."""
from __future__ import annotations

from datetime import datetime, timezone

import arxiv
import httpx
from loguru import logger

from .base import FeedItem

PWC_API = "https://paperswithcode.com/api/v1/papers"


def fetch_arxiv(config: dict, since: datetime | None = None) -> list[FeedItem]:
    arxiv_cfg = config.get("arxiv", {})
    categories = arxiv_cfg.get("categories", ["cs.AI", "cs.CL", "cs.LG"])
    max_papers = arxiv_cfg.get("max_papers", 30)

    # arXiv papers always look back at least 7 days — papers are submitted
    # on weekdays and may take 1-2 days to appear in the API
    from datetime import timedelta
    arxiv_since = since
    if arxiv_since:
        min_arxiv_lookback = datetime.now(tz=timezone.utc) - timedelta(days=7)
        if arxiv_since > min_arxiv_lookback:
            arxiv_since = min_arxiv_lookback

    items: list[FeedItem] = []

    query = " OR ".join(f"cat:{cat}" for cat in categories)
    sort = arxiv.SortCriterion.SubmittedDate

    try:
        search = arxiv.Search(query=query, max_results=max_papers, sort_by=sort)
        client = arxiv.Client()

        for result in client.results(search):
            pub = result.published
            if pub and pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if arxiv_since and pub and pub < arxiv_since:
                continue

            authors = ", ".join(str(a) for a in result.authors[:3])
            if len(result.authors) > 3:
                authors += " et al."

            items.append(
                FeedItem(
                    url=result.entry_id,
                    title=result.title.strip(),
                    source_name="arXiv",
                    source_type="arxiv",
                    summary=result.summary[:600],
                    published_at=pub,
                    author=authors,
                    source_weight=0.9,
                    tags=[c for c in result.categories],
                )
            )

        logger.info(f"[arXiv] {len(items)} papers")
    except Exception as exc:
        logger.error(f"[arXiv] fetch failed: {exc}")

    return items


def fetch_papers_with_code(since: datetime | None = None) -> list[FeedItem]:
    items: list[FeedItem] = []
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                PWC_API,
                params={"ordering": "-github_link_count", "items_per_page": 20},
                headers={"User-Agent": "ai-newsletter/1.0"},
            )
            if resp.status_code != 200:
                logger.warning(f"[PapersWithCode] HTTP {resp.status_code}")
                return items

            data = resp.json().get("results", [])
            for paper in data:
                pub_str = paper.get("published") or paper.get("date", "")
                pub: datetime | None = None
                if pub_str:
                    try:
                        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                    except Exception:
                        pass
                if since and pub and pub < since:
                    continue

                url = paper.get("url_pdf") or paper.get("url_abs") or ""
                arxiv_id = paper.get("arxiv_id", "")
                if arxiv_id and not url:
                    url = f"https://arxiv.org/abs/{arxiv_id}"

                items.append(
                    FeedItem(
                        url=url,
                        title=(paper.get("title") or "").strip(),
                        source_name="Papers With Code",
                        source_type="arxiv",
                        summary=(paper.get("abstract") or "")[:500],
                        published_at=pub,
                        score=paper.get("github_link_count", 0),
                        source_weight=0.85,
                    )
                )

        logger.info(f"[PapersWithCode] {len(items)} papers")
    except Exception as exc:
        logger.error(f"[PapersWithCode] fetch failed: {exc}")

    return items


def fetch_research(config: dict, since: datetime | None = None) -> list[FeedItem]:
    items = fetch_arxiv(config, since)
    items += fetch_papers_with_code(since)
    return items
