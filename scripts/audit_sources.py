"""
audit_sources.py — Weekly source health audit + discovery.

Runs every Sunday via GitHub Actions. Does four things:
1. Scores all sources over the last 30 days (via track_sources)
2. Discovers new source candidates from recent newsletter content (via discover_sources)
3. Opens a GitHub PR if config changes are needed (cold sources disabled, new sources added)
4. Opens a GitHub Issue as the weekly source digest (always, unless --no-issue)

Usage:
    python scripts/audit_sources.py
    python scripts/audit_sources.py --dry-run         # print plan, no PR/Issue
    python scripts/audit_sources.py --no-issue        # skip digest issue
    python scripts/audit_sources.py --since 60        # extend scoring window
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from loguru import logger

# Make sure local package imports work when run from repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.track_sources import score_sources
from scripts.discover_sources import discover

CONFIG_PATH  = Path(__file__).parent.parent / "config.yaml"
SOURCES_DIR  = Path(__file__).parent.parent / "sources"

# Thresholds
SILENCE_THRESHOLD   = 30    # days without fetched items → flag cold
COMPOSITE_THRESHOLD = 0.15  # composite score below this → flag poor performer
MIN_ACTIVE_DAYS     = 5     # need at least this many active days before judging performance

# Source sections that can be auto-managed
MANAGED_SECTIONS = ["news_rss", "substacks", "blogs", "podcasts"]


# ── Helpers ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def save_config(config: dict) -> None:
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)


def git_branch_name() -> str:
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    return f"source-audit/{today}"


def section_for_type(source_type: str) -> str:
    mapping = {
        "news_rss": "news_rss",
        "substack": "substacks",
        "blog": "blogs",
        "podcast": "podcasts",
    }
    return mapping.get(source_type, "blogs")


# ── Core audit logic ───────────────────────────────────────────────────────────

def find_cold_sources(scores: dict[str, dict]) -> list[dict]:
    """Sources that haven't delivered anything in >= SILENCE_THRESHOLD days."""
    cold = []
    for name, s in scores.items():
        if s["silent_days"] >= SILENCE_THRESHOLD:
            cold.append({
                "name": name,
                "silent_days": s["silent_days"],
                "composite": s["composite"],
                "source_type": s["source_type"],
                "reason": f"No items fetched for {s['silent_days']} consecutive days",
            })
    return sorted(cold, key=lambda x: x["silent_days"], reverse=True)


def find_poor_performers(scores: dict[str, dict]) -> list[dict]:
    """Active sources with consistently low composite scores."""
    poor = []
    for name, s in scores.items():
        if (
            s["silent_days"] < SILENCE_THRESHOLD          # not already cold
            and s["active_days"] >= MIN_ACTIVE_DAYS        # enough data
            and s["composite"] < COMPOSITE_THRESHOLD
        ):
            poor.append({
                "name": name,
                "composite": s["composite"],
                "hit_rate": s["hit_rate"],
                "freshness": s["freshness"],
                "impact": s["impact"],
                "source_type": s["source_type"],
                "reason": f"Composite score {s['composite']:.2f} over {s['active_days']} active days",
            })
    return sorted(poor, key=lambda x: x["composite"])


def disable_in_config(config: dict, source_names: set[str]) -> tuple[dict, list[str]]:
    """
    Add `enabled: false` to matching sources.
    Returns (updated_config, list_of_actually_disabled_names).
    """
    disabled = []
    for section in MANAGED_SECTIONS:
        for source in config.get(section, []):
            if source.get("name", "") in source_names:
                if source.get("enabled", True):  # only count if actually changing
                    source["enabled"] = False
                    disabled.append(source["name"])
    return config, disabled


def add_candidates_to_config(config: dict, candidates: list[dict]) -> tuple[dict, list[str]]:
    """
    Append new source candidates to the appropriate config section.
    Returns (updated_config, list_of_added_names).
    """
    # Build set of existing URLs (normalised)
    existing_urls = set()
    for section in MANAGED_SECTIONS:
        for s in config.get(section, []):
            existing_urls.add(s.get("url", "").rstrip("/").lower())

    added = []
    for cand in candidates:
        url = cand.get("url", "").rstrip("/").lower()
        if url in existing_urls:
            continue
        source_type = cand.get("type", "blog")
        section = section_for_type(source_type)
        entry = {
            "name": cand["name"],
            "url": cand["url"],
            "weight": cand.get("weight", 0.7),
        }
        config.setdefault(section, []).append(entry)
        existing_urls.add(url)
        added.append(cand["name"])

    return config, added


# ── GitHub interactions ────────────────────────────────────────────────────────

def create_pr(
    cold: list[dict],
    poor: list[dict],
    added: list[dict],
    disabled_names: list[str],
    dry_run: bool,
) -> str | None:
    """
    Commit config changes to a new branch and open a PR.
    Returns PR URL or None.
    """
    branch = git_branch_name()

    if dry_run:
        logger.info(f"[DRY RUN] Would create branch {branch} and open PR")
        return None

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    try:
        # Create and push branch
        _run(f"git checkout -b {branch}")
        _run(f"git add {CONFIG_PATH}")
        _run(f'git commit -m "chore(sources): weekly audit {today}"')
        _run(f"git push origin {branch}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Git operation failed: {e.stderr}")
        return None

    # Build PR body
    lines = [
        "## Weekly Source Audit — Automated PR",
        "",
        f"**Date:** {today}",
        "",
    ]

    if disabled_names:
        lines += [
            "### 🔴 Sources disabled",
            "",
            "| Source | Reason |",
            "|--------|--------|",
        ]
        all_flagged = {d["name"]: d for d in cold + poor}
        for name in disabled_names:
            reason = all_flagged.get(name, {}).get("reason", "—")
            lines.append(f"| {name} | {reason} |")
        lines.append("")

    if added:
        added_map = {c["name"]: c for c in added}
        lines += [
            "### 🟢 New sources added",
            "",
            "| Source | URL | Type | Weight | Why |",
            "|--------|-----|------|--------|-----|",
        ]
        for cand in added:
            lines.append(
                f"| {cand['name']} | {cand['url']} | {cand.get('type','—')} "
                f"| {cand.get('weight', 0.7)} | {cand.get('reason','—')} |"
            )
        lines.append("")

    lines += [
        "---",
        "_Merge to apply. Close to reject. Changes take effect on next newsletter run._",
        "",
        "🤖 Generated by `scripts/audit_sources.py`",
    ]

    body = "\n".join(lines)

    try:
        result = _run(
            f'gh pr create --title "Source audit {today}" '
            f'--body "{body.replace(chr(34), chr(39))}" '
            f'--base main --head {branch}'
        )
        pr_url = result.stdout.strip()
        logger.info(f"PR created: {pr_url}")
        return pr_url
    except subprocess.CalledProcessError as e:
        logger.error(f"gh pr create failed: {e.stderr}")
        return None


def create_digest_issue(
    scores: dict[str, dict],
    cold: list[dict],
    poor: list[dict],
    candidates: list[dict],
    pr_url: str | None,
    dry_run: bool,
) -> str | None:
    """Open a GitHub Issue with the weekly source health digest."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Top 10 performers
    top10 = sorted(scores.items(), key=lambda x: x[1]["composite"], reverse=True)[:10]

    lines = [
        f"# Source Health Digest — {today}",
        "",
        f"**Sources tracked:** {len(scores)}  |  "
        f"**Cold (≥{SILENCE_THRESHOLD}d silence):** {len(cold)}  |  "
        f"**Poor performers:** {len(poor)}  |  "
        f"**New candidates:** {len(candidates)}",
        "",
    ]

    # Top performers table
    lines += [
        "## 🏆 Top 10 Sources (30-day composite)",
        "",
        "| Rank | Source | Type | Composite | Hit Rate | Freshness | Impact | Active Days |",
        "|------|--------|------|-----------|----------|-----------|--------|-------------|",
    ]
    for rank, (name, s) in enumerate(top10, 1):
        lines.append(
            f"| {rank} | {name} | {s['source_type']} | **{s['composite']:.2f}** | "
            f"{s['hit_rate']:.2f} | {s['freshness']:.2f} | {s['impact']:.2f} | {s['active_days']} |"
        )
    lines.append("")

    # Cold sources
    if cold:
        lines += [
            f"## 🔴 Cold Sources (silent ≥ {SILENCE_THRESHOLD} days)",
            "",
            "| Source | Type | Silent Days | Composite |",
            "|--------|------|-------------|-----------|",
        ]
        for s in cold:
            lines.append(f"| {s['name']} | {s['source_type']} | {s['silent_days']} | {s['composite']:.2f} |")
        lines.append("")

    # Poor performers
    if poor:
        lines += [
            "## 🟡 Poor Performers",
            "",
            "| Source | Type | Composite | Hit Rate | Freshness | Impact |",
            "|--------|------|-----------|----------|-----------|--------|",
        ]
        for s in poor:
            lines.append(
                f"| {s['name']} | {s['source_type']} | {s['composite']:.2f} | "
                f"{s['hit_rate']:.2f} | {s['freshness']:.2f} | {s['impact']:.2f} |"
            )
        lines.append("")

    # New candidates
    if candidates:
        lines += [
            "## 🔍 Discovered Candidates",
            "",
            "| Source | URL | Type | Weight | Why |",
            "|--------|-----|------|--------|-----|",
        ]
        for c in candidates:
            lines.append(
                f"| {c['name']} | {c['url']} | {c.get('type','—')} | "
                f"{c.get('weight',0.7)} | {c.get('reason','—')} |"
            )
        lines.append("")

    if pr_url:
        lines += [f"## 🔗 Config Changes PR", "", f"→ {pr_url}", ""]

    lines += [
        "---",
        "_This issue is auto-generated each Sunday. No action needed unless you want to review the PR._",
        "",
        "🤖 `scripts/audit_sources.py`",
    ]

    body = "\n".join(lines)

    if dry_run:
        logger.info("[DRY RUN] Weekly digest issue body:")
        print(body[:2000])
        return None

    # Write body to a temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name

    try:
        result = _run(
            f'gh issue create --title "Source Health Digest — {today}" '
            f'--body-file "{tmp_path}" '
            f'--label "source-audit"'
        )
        issue_url = result.stdout.strip()
        logger.info(f"Digest issue created: {issue_url}")
        return issue_url
    except subprocess.CalledProcessError as e:
        # Label might not exist — try without label
        try:
            result = _run(
                f'gh issue create --title "Source Health Digest — {today}" '
                f'--body-file "{tmp_path}"'
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e2:
            logger.error(f"gh issue create failed: {e2.stderr}")
            return None
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Weekly source audit")
    parser.add_argument("--dry-run", action="store_true", help="Print plan but don't create PR/Issue")
    parser.add_argument("--no-issue", action="store_true", help="Skip creating the digest GitHub Issue")
    parser.add_argument("--since", type=int, default=30, help="Scoring window in days (default 30)")
    parser.add_argument("--silence", type=int, default=SILENCE_THRESHOLD, help="Silent days threshold")
    args = parser.parse_args()

    silence_threshold = args.silence
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    logger.info("=" * 60)
    logger.info(f"Source Audit — {today}")
    logger.info("=" * 60)

    # ── 1. Score ──────────────────────────────────────────────────────────────
    logger.info(f"Scoring sources over last {args.since} days …")
    scores = score_sources(days=args.since)
    logger.info(f"  {len(scores)} sources with history")

    # ── 2. Flag cold / poor ───────────────────────────────────────────────────
    cold = find_cold_sources(scores)
    poor = find_poor_performers(scores)
    logger.info(f"  Cold (≥{silence_threshold}d): {len(cold)}")
    logger.info(f"  Poor performers: {len(poor)}")

    # ── 3. Discover new candidates ────────────────────────────────────────────
    config = load_config()
    logger.info("Discovering new source candidates …")
    candidates = discover(config, dry_run=args.dry_run)
    logger.info(f"  {len(candidates)} candidates found")

    # ── 4. Determine if PR is needed ──────────────────────────────────────────
    # PR threshold: disable cold sources, add new ones.
    # Poor performers are noted in the digest but NOT auto-disabled (needs human review).
    sources_to_disable = {s["name"] for s in cold}

    need_pr = bool(sources_to_disable or candidates)

    pr_url = None
    if need_pr:
        logger.info("Config changes needed — preparing PR …")
        # Apply changes to config
        if sources_to_disable:
            config, disabled_names = disable_in_config(config, sources_to_disable)
            logger.info(f"  Disabled: {disabled_names}")
        else:
            disabled_names = []

        if candidates:
            config, added_names = add_candidates_to_config(config, candidates)
            logger.info(f"  Added: {added_names}")
            # Enrich candidates list with just the ones actually added
            added_candidates = [c for c in candidates if c["name"] in set(added_names)]
        else:
            added_names = []
            added_candidates = []

        if disabled_names or added_names:
            if not args.dry_run:
                save_config(config)
                logger.info(f"config.yaml updated")
            pr_url = create_pr(
                cold=cold,
                poor=poor,
                added=added_candidates,
                disabled_names=disabled_names,
                dry_run=args.dry_run,
            )
        else:
            logger.info("  No net changes after dedup — skipping PR")
    else:
        logger.info("No config changes needed this week")

    # ── 5. Weekly digest issue ────────────────────────────────────────────────
    if not args.no_issue:
        logger.info("Creating weekly digest issue …")
        issue_url = create_digest_issue(
            scores=scores,
            cold=cold,
            poor=poor,
            candidates=candidates,
            pr_url=pr_url,
            dry_run=args.dry_run,
        )

    # ── 6. Summary ────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("=" * 60)
    logger.info("AUDIT COMPLETE")
    logger.info(f"  Sources scored  : {len(scores)}")
    logger.info(f"  Cold flagged    : {len(cold)}")
    logger.info(f"  Poor performers : {len(poor)}")
    logger.info(f"  New candidates  : {len(candidates)}")
    if pr_url:
        logger.info(f"  PR              : {pr_url}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
