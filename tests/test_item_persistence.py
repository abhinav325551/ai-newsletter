from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fetchers.base import FeedItem  # noqa: E402
from persistence import persist_items  # noqa: E402


def _item(url="https://example.com/post", **kw):
    base = dict(url=url, title="A post", source_name="Stratechery",
                source_type="substack", summary="dek", author="Ben Thompson",
                published_at=datetime(2026, 8, 28, 10, 0, 0))
    base.update(kw)
    return FeedItem(**base)


def test_persists_attributed_records_one_per_line(tmp_path):
    path = persist_items([_item(), _item(url="https://example.com/two")],
                         tmp_path / "items", "2026-08-28")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert path.name == "2026-08-28.jsonl" and len(lines) == 2
    record = json.loads(lines[0])
    for field in ("url", "title", "summary", "author", "published_at", "source_name"):
        assert record.get(field), field
    assert "full_text" not in record  # dropped: ledger needs attribution, not bodies


def test_rerun_overwrites_and_empty_urls_skipped(tmp_path):
    out = tmp_path / "items"
    persist_items([_item(), _item(url="")], out, "2026-08-28")
    path = persist_items([_item()], out, "2026-08-28")
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
