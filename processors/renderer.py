"""Render newsletter sections to Markdown and HTML using Jinja2 templates."""
from __future__ import annotations

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

    return md_path, html_path
