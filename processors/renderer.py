"""Render newsletter sections to Markdown and HTML using Jinja2 templates.

Also writes a `.json` sidecar with the structured per-section item data so the
Saturday weekly digest can aggregate the week's stories without re-fetching.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from loguru import logger

from .summarizer import NewsletterSection

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


def render(
    sections: dict[str, NewsletterSection],
    config: dict,
    output_dir: Path,
    date: datetime | None = None,
) -> tuple[Path, Path]:
    """Write Markdown + HTML to output_dir. Returns (md_path, html_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)

    date = date or datetime.utcnow()
    date_str = date.strftime("%Y-%m-%d")

    all_items = [i for sec in sections.values() for i in sec.items]
    source_names = sorted({i.source_name for i in all_items})
    source_list = ", ".join(source_names[:20])
    if len(source_names) > 20:
        source_list += f" + {len(source_names) - 20} more"

    sections_cfg = config.get("sections", [])
    ordered_sections = [
        sections[sec["id"]]
        for sec in sections_cfg
        if sec["id"] in sections
    ]

    ctx = {
        "newsletter": config.get("newsletter", {}),
        "date": date_str,
        "total_items": len(all_items),
        "sections": ordered_sections,
        "model": config.get("newsletter", {}).get("summarization_model", "claude-sonnet-4-6"),
        "source_list": source_list,
    }

    env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)), autoescape=False)

    # Markdown
    md_tmpl = env.get_template("newsletter.md.j2")
    md_path = output_dir / f"{date_str}.md"
    md_path.write_text(md_tmpl.render(**ctx), encoding="utf-8")
    logger.info(f"[Renderer] Markdown → {md_path}")

    # HTML
    html_tmpl = env.get_template("newsletter.html.j2")
    html_path = output_dir / f"{date_str}.html"
    html_path.write_text(html_tmpl.render(**ctx), encoding="utf-8")
    logger.info(f"[Renderer] HTML → {html_path}")

    # JSON sidecar — structured data for the weekly digest aggregator
    sidecar = {
        "date": date_str,
        "total_items": len(all_items),
        "source_names": source_names,
        "sections": [
            {
                "id": sec.id,
                "title": sec.title,
                "intro": sec.intro or "",
                "summary": sec.summary or "",
                "items": [
                    {
                        "title": getattr(it, "title", "") or "",
                        "url": getattr(it, "url", "") or "",
                        "source_name": getattr(it, "source_name", "") or "",
                        "source_type": getattr(it, "source_type", "") or "",
                        "score": getattr(it, "score", 0) or 0,
                    }
                    for it in sec.items
                ],
            }
            for sec in ordered_sections
        ],
    }
    sidecar_path = output_dir / f"{date_str}.json"
    sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"[Renderer] JSON sidecar → {sidecar_path}")

    return md_path, html_path
