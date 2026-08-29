"""Persist the raw FeedItems the pipeline already holds in memory.

The rendered digest strips attribution; these JSONL snapshots are what makes
item-level, author-attributed layer-2 ingestion possible downstream in the
narrative ledger. One file per day; re-running a day overwrites it, so the
snapshot is idempotent like the digest itself.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path


def persist_items(items, out_dir: Path, day: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            record = dataclasses.asdict(item)
            if not record.get("url"):
                continue
            # full_text can be hundreds of KB per article and the ledger only
            # needs attribution + summary; drop it from the snapshot.
            record.pop("full_text", None)
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    return path
