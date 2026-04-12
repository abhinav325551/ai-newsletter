"""Shared data model and helpers for all fetchers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class FeedItem:
    """Canonical representation of a single piece of content from any source."""

    # Identity
    url: str
    title: str
    source_name: str
    source_type: str          # news | substack | blog | podcast | hn | reddit | twitter | arxiv

    # Content
    summary: str = ""         # original excerpt / abstract
    full_text: str = ""       # fetched full article text (filled by processor)
    published_at: Optional[datetime] = None

    # Engagement signals (used for importance scoring)
    score: int = 0            # HN points / Reddit upvotes / Twitter likes
    comments: int = 0
    author: str = ""

    # Metadata
    source_weight: float = 0.6
    tags: list[str] = field(default_factory=list)
    is_rumor: bool = False
    cluster_id: Optional[int] = None   # assigned by clusterer
    section: Optional[str] = None      # assigned by scorer

    @property
    def uid(self) -> str:
        """Stable ID derived from canonical URL."""
        return hashlib.sha1(self.canonical_url.encode()).hexdigest()[:12]

    @property
    def canonical_url(self) -> str:
        """Strip UTM params and trailing slashes for deduplication."""
        import re
        url = self.url.split("?")[0].rstrip("/")
        url = re.sub(r"#.*$", "", url)
        return url.lower()
