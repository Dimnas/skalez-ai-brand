# skalez.ai — Autonomous AI News Pipeline

Fully automated content engine that monitors 19 AI news sources, scores stories by community signal, and generates Instagram-ready carousels. Runs on cron with zero human intervention.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     LAYER 1 — MONITOR                    │
│                 (ai_news_monitor.py)                     │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │ RSS/Atom │ │  Reddit  │ │HackerNews│ │  GitHub     │ │
│  │ 13 feeds │ │ 5 subs   │ │ Algolia  │ │  Search API │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
│       │            │            │              │         │
│  ┌────┴─────┐ ┌────┴─────┐ ┌────┴─────┐ ┌─────┴──────┐ │
│  │Insight   │ │Community │ │Community│ │ Community   │ │
│  │scoring   │ │upvotes   │ │ points  │ │ stars(log)  │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬──────┘ │
│       └─────────────┴────────────┴─────────────┘        │
│                         │                               │
│                    pending_items.json                   │
└─────────────────────────┬───────────────────────────────┘
                          │
┌─────────────────────────┴───────────────────────────────┐
│                   LAYER 2 — CAROUSEL                     │
│                                                         │
│  ┌────────────────┐    ┌──────────────────┐             │
│  │ carousel_prep  │───▶│  LLM copywriter  │             │
│  │ score → pick   │    │  writes slides    │             │
│  └────────────────┘    └────────┬─────────┘             │
│                                 │                       │
│  ┌──────────────────────────────┴──────────┐            │
│  │        render_carousel.py               │            │
│  │   ffmpeg + PIL → MP4 slides             │            │
│  │   1080×1350, video backgrounds           │            │
│  └──────────────────────────────────────────┘            │
│                         │                               │
│                    Discord #carousel-results             │
└─────────────────────────────────────────────────────────┘
```

## Sources (all free, no API keys required)

| Category | Sources | Method |
|----------|---------|--------|
| **Insight RSS** | Import AI, Stratechery, Simon Willison, TLDR AI, The Sequence, Latent Space, No Priors, AI Snake Oil, One Useful Thing, Marginal Revolution, OpenAI Research, Google AI Blog, DeepMind Blog | RSS/Atom polling |
| **Community** | HackerNews (6 keyword queries), Reddit (5 subreddits), GitHub (4 topic searches) | Algolia / JSON / Search APIs |
| **Scrapers** | Anthropic blog, The Decoder, HN | Custom scraper |

## Scoring System

Stories compete on **community signal**, not description length.

| Source | Signal | Range |
|--------|--------|-------|
| HackerNews | Raw points | 0–5,000 |
| Reddit | Raw upvotes | 0–5,000 |
| GitHub | log₁₀(stars) × 100 | 169 (50★) → 527 (190K★) |
| Insight RSS | Title depth + description length | 100–800 |
| Scraper | Description length × 2 | 0–1,000 |

Age-based FIFO fairness boost (capped at 200) prevents new items from starving.

## Red-Team Hardening

- Lockfiles prevent overlapping cron runs
- Atomic writes (tmp + rename) on all state files
- Deferred state mutation — pending file committed only after read-all succeeds
- GitHub rate-limit backoff (403/429 → 1h skip)
- DeepMind first-run cap (10 URLs, prevents 334-fetch runaway)
- URL validation, markdown escaping, TTL pruning (30-day state entries)

## Quick Start

```bash
# Install
git clone https://github.com/Dimnas/skalez-ai-brand
cd skalez-ai-brand
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Install blogwatcher-cli (for Google AI RSS)
curl -fsSL https://raw.githubusercontent.com/nicholasgriffintn/blogwatcher-cli/main/install.sh | bash

# Run Layer 1 — collects news from all sources
python3 scripts/ai_news_monitor.py

# Run Layer 2 — picks best story, writes carousel, renders
python3 scripts/carousel_prep.py  # selects item
# (LLM processes .current_item.json → writes slide JSON)
python3 scripts/render_carousel.py  # renders MP4s
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_NEWS_DATA_DIR` | `./data/` | State/pending/lock files |
| `BLOGWATCHER_BIN` | `blogwatcher-cli` | Path to blogwatcher binary |
| `BLOGWATCHER_DB` | `./data/blogwatcher.db` | Path to blogwatcher database |
| `VIDEO_DIR` | `./assets/pinterest-visuals/` | Video background assets |

## Output

Carousels render as individual MP4 files (one per slide) at 1080×1350 (4:5 portrait). Each slide gets a video background (rotated round-robin across 10 variants) with static text overlay.

## License

MIT
