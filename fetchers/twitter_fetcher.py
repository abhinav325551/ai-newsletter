"""Twitter / X fetcher.

Gracefully optional — returns [] if TWITTER_BEARER_TOKEN is not set.
Uses the X API v2 user timeline endpoint (works on free tier).
Falls back silently if the account tier doesn't support the endpoint.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

import httpx
from loguru import logger

from .base import FeedItem

TWITTER_USER_URL = "https://api.twitter.com/2/users/by/username/{username}"
TWITTER_TIMELINE_URL = "https://api.twitter.com/2/users/{user_id}/tweets"


def _bearer_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def fetch_twitter(config: dict, since: datetime | None = None) -> list[FeedItem]:
    token = os.getenv("TWITTER_BEARER_TOKEN")
    if not token:
        logger.info("[Twitter] TWITTER_BEARER_TOKEN not set — skipping")
        return []

    twitter_cfg = config.get("twitter", {})
    handles = twitter_cfg.get("handles", [])
    max_per_handle = twitter_cfg.get("max_tweets_per_handle", 5)

    all_items: list[FeedItem] = []

    with httpx.Client(timeout=15) as client:
        for handle in handles:
            try:
                # Step 1: resolve handle → user_id
                user_resp = client.get(
                    TWITTER_USER_URL.format(username=handle),
                    headers=_bearer_headers(token),
                )
                if user_resp.status_code == 402:
                    logger.warning("[Twitter] Free tier does not support this endpoint — skipping Twitter entirely")
                    return []
                if user_resp.status_code == 429:
                    logger.warning("[Twitter] Rate limited — skipping remaining handles")
                    break
                if user_resp.status_code != 200:
                    logger.debug(f"[Twitter] @{handle}: user lookup HTTP {user_resp.status_code}")
                    continue

                user_id = user_resp.json().get("data", {}).get("id")
                if not user_id:
                    continue

                # Step 2: fetch timeline
                params: dict = {
                    "max_results": max(5, max_per_handle),
                    "tweet.fields": "created_at,public_metrics",
                    "exclude": "retweets,replies",
                }
                if since:
                    params["start_time"] = since.strftime("%Y-%m-%dT%H:%M:%SZ")

                tl_resp = client.get(
                    TWITTER_TIMELINE_URL.format(user_id=user_id),
                    params=params,
                    headers=_bearer_headers(token),
                )
                if tl_resp.status_code == 402:
                    logger.warning("[Twitter] Free tier does not support timeline endpoint — skipping Twitter")
                    return []
                if tl_resp.status_code != 200:
                    logger.debug(f"[Twitter] @{handle} timeline: HTTP {tl_resp.status_code}")
                    continue

                tweets = tl_resp.json().get("data", [])
                for tweet in tweets[:max_per_handle]:
                    tweet_id = tweet["id"]
                    text = tweet.get("text", "")
                    metrics = tweet.get("public_metrics", {})
                    created = tweet.get("created_at", "")

                    pub: datetime | None = None
                    if created:
                        try:
                            pub = datetime.fromisoformat(created.replace("Z", "+00:00"))
                        except Exception:
                            pass

                    all_items.append(
                        FeedItem(
                            url=f"https://twitter.com/{handle}/status/{tweet_id}",
                            title=text[:120] + ("…" if len(text) > 120 else ""),
                            source_name=f"@{handle}",
                            source_type="twitter",
                            summary=text,
                            published_at=pub,
                            score=metrics.get("like_count", 0) + metrics.get("retweet_count", 0) * 3,
                            comments=metrics.get("reply_count", 0),
                            author=handle,
                            source_weight=0.75,
                        )
                    )

                time.sleep(0.5)

            except Exception as exc:
                logger.error(f"[Twitter] @{handle}: {exc}")

    logger.info(f"[Twitter] {len(all_items)} tweets")
    return all_items
