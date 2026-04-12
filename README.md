# AI & Tech Intelligence Newsletter

A personal AI newsletter generator covering the full stack — from silicon to SaaS disruption.

## Setup

### 1. Install dependencies

```bash
# With uv (recommended)
pip install uv
uv pip install -e .

# Or with pip
pip install -e .
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

### 3. Run

```bash
# Default: last 24 hours, send via Gmail
python main.py

# Last 3 days, no email
python main.py --since 3 --no-email

# Dry run (no Claude API calls)
python main.py --dry-run

# Skip full-text fetching (faster for testing)
python main.py --no-full-text --dry-run
```

## Required Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | Powers Claude summarization |
| `REDDIT_CLIENT_ID` | Optional | Reddit PRAW access |
| `REDDIT_CLIENT_SECRET` | Optional | Reddit PRAW access |
| `REDDIT_USER_AGENT` | Optional | Defaults to `ai-newsletter-bot/1.0` |
| `TWITTER_BEARER_TOKEN` | Optional | X API v2 — Twitter fetcher is skipped if absent |
| `GMAIL_CREDENTIALS_PATH` | Optional | Path to OAuth credentials JSON for Gmail |
| `GMAIL_TOKEN_PATH` | Optional | Path to store Gmail OAuth token |
| `RECIPIENT_EMAIL` | Optional | Where to send the newsletter |

## Gmail Setup (optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** of type *Desktop app*
3. Download the JSON → save as `credentials.json` (or set `GMAIL_CREDENTIALS_PATH`)
4. On first run with email enabled, a browser window opens for OAuth authorization
5. The token is cached in `token.json` for subsequent runs

## Project Structure

```
ai-newsletter/
├── main.py                  # CLI entry point
├── config.yaml              # All source lists & tuning parameters
├── .env.example             # API key template
├── pyproject.toml
├── fetchers/
│   ├── base.py              # FeedItem dataclass
│   ├── rss_fetcher.py       # News RSS (TechCrunch, Bloomberg, etc.)
│   ├── substack_fetcher.py  # Substack newsletters
│   ├── blog_fetcher.py      # Lab & VC blogs
│   ├── podcast_fetcher.py   # Podcast episode titles/descriptions
│   ├── hn_fetcher.py        # Hacker News Firebase API
│   ├── reddit_fetcher.py    # Reddit via PRAW
│   ├── twitter_fetcher.py   # Twitter/X API v2 (optional)
│   └── research_fetcher.py  # arXiv + Papers With Code
├── processors/
│   ├── deduplicator.py      # URL + fuzzy title dedup
│   ├── article_fetcher.py   # Full text via trafilatura
│   ├── scorer.py            # Relevance scoring + section assignment
│   ├── clusterer.py         # Story clustering via embeddings
│   ├── summarizer.py        # Claude summarization
│   ├── renderer.py          # Jinja2 → Markdown + HTML
│   └── emailer.py           # Gmail API delivery
├── templates/
│   ├── newsletter.md.j2
│   └── newsletter.html.j2
├── output/                  # Generated issues (YYYY-MM-DD/)
└── .github/workflows/
    └── newsletter.yml       # GitHub Actions (runs weekdays 06:00 UTC)
```

## Adding / Removing Sources

All sources live in `config.yaml`. No code changes needed:

```yaml
news_rss:
  - name: "My New Source"
    url: "https://example.com/feed.rss"
    weight: 0.8   # 0.0–1.0, higher = more authoritative

substacks:
  - name: "New Substack"
    url: "https://mysubstack.substack.com/feed"
    weight: 0.75
```

## Scheduling (cron)

```bash
# Add to crontab: every weekday at 7 AM local time
0 7 * * 1-5 cd /path/to/ai-newsletter && python main.py >> logs/cron.log 2>&1
```

Or use the included GitHub Actions workflow (`.github/workflows/newsletter.yml`).

## Newsletter Sections

1. **The Big Thing** — single most important story, 3–4 paragraphs
2. **Infrastructure & Silicon** — chips, data centers, training/inference
3. **Models & Research** — releases, open-source, papers, benchmarks
4. **Tooling & Agents** — frameworks, vector DBs, orchestration, eval
5. **Applications & Consumer AI** — product launches, enterprise AI
6. **SaaS Disruption Watch** — ≥3 incumbent categories/companies pressured by AI
7. **Signals from the Feed** — Twitter/Reddit/HN bullets
8. **Worth Reading** — long-reads, essays, podcasts
9. **Papers of the Week** — top arXiv + Papers With Code

## Whisper Hook (future)

To enable podcast transcription, set `WHISPER_ENABLED=true` in `.env` and install:

```bash
pip install openai-whisper
```

Audio URLs are stored in each podcast `FeedItem.tags[0]` and ready to be passed to Whisper.
