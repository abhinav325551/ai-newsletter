"""Reddit fetcher using PRAW."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import praw
from loguru import logger

from .base import FeedItem


def fetch_reddit(config: dict, since: datetime | None = None) -> list[FeedItem]:
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    user_agent = os.getenv("REDDIT_USER_AGENT", "ai-newsletter-bot/1.0")

    if not client_id or not client_secret:
        logger.warning("[Reddit] REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set — skipping")
        return []

    reddit_cfg = config.get("reddit", {})
    subreddits = reddit_cfg.get("subreddits", [])
    time_filters = reddit_cfg.get("time_filters", ["day"])
    post_limit = reddit_cfg.get("post_limit", 25)
    min_score = config.get("newsletter", {}).get("min_reddit_score", 20)
    ai_keywords = [k.lower() for k in config.get("ai_keywords", [])]

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent=user_agent,
            read_only=True,
        )
    except Exception as exc:
        logger.error(f"[Reddit] PRAW init failed: {exc}")
        return []

    all_items: list[FeedItem] = []
    seen_ids: set[str] = set()

    for sub_cfg in subreddits:
        sub_name = sub_cfg["name"]
        weight = sub_cfg.get("weight", 0.7)

        for time_filter in time_filters:
            try:
                subreddit = reddit.subreddit(sub_name)
                posts = subreddit.top(time_filter=time_filter, limit=post_limit)

                for post in posts:
                    if post.id in seen_ids:
                        continue
                    seen_ids.add(post.id)

                    if post.score < min_score:
                        continue

                    title_lower = post.title.lower()
                    # For non-AI subreddits, apply keyword filter
                    if sub_name.lower() not in ("machinelearning", "localllama", "openai", "claudeai", "chatgpt"):
                        if not any(kw in title_lower for kw in ai_keywords):
                            continue

                    pub = datetime.fromtimestamp(post.created_utc, tz=timezone.utc)
                    if since and pub < since:
                        continue

                    # Prefer external URL, fall back to Reddit post
                    url = post.url if not post.is_self else f"https://reddit.com{post.permalink}"

                    all_items.append(
                        FeedItem(
                            url=url,
                            title=post.title,
                            source_name=f"r/{sub_name}",
                            source_type="reddit",
                            summary=(post.selftext or "")[:500],
                            published_at=pub,
                            score=post.score,
                            comments=post.num_comments,
                            author=str(post.author) if post.author else "",
                            source_weight=weight,
                            tags=[f"https://reddit.com{post.permalink}"],
                        )
                    )

            except Exception as exc:
                logger.error(f"[Reddit] r/{sub_name} ({time_filter}): {exc}")

    logger.info(f"[Reddit] {len(all_items)} items")
    return all_items
