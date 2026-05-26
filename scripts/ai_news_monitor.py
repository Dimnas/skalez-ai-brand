#!/usr/bin/env python3
"""
AI News Monitor — Layer 1 (Free)

Sources:
  - Google AI Blog: RSS via blogwatcher-cli
  - Google DeepMind: sitemap polling (no RSS available)
  - OpenAI: via existing Anthropic scraper (The Decoder / HN fallback)
  - Anthropic/Claude: existing scrape.py
  - DeepMind Blog RSS, Google AI Blog RSS, and insight newsletters
  - Hacker News: Algolia API (6 keyword queries, free)
  - Reddit: JSON API (5 AI subreddits, free)
  - GitHub: Search API (4 topic queries, 2h cache)
  - Insight RSS: 13 curated newsletters/research blogs (free)

Runs every 15 minutes. Outputs new items only (delta since last run).
Silent when nothing new — cron delivers only when stdout is non-empty.

Future: Apify integration slot (just add another source function).

State file: ~/.hermes/scraper/.monitor_state.json

Red-team hardening (2026-05-25):
  - Lockfile prevents overlapping cron runs
  - State pruning: seen_urls TTL = 30 days — prevents unbounded growth
  - Deferred state mutation: source functions return items; main() commits after
    pending file write succeeds — no premature seen_urls corruption
  - URL validation: scheme check, empty/bogus URL filtering
  - Markdown escaping in format_item prevents Telegram formatting corruption
  - DeepMind: rate-limited per-URL fetches (0.5s delay) + logged warnings
  - read-all failure = don't save state (prevents blogwatcher/monitor drift)
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ─── Config ────────────────────────────────────────────────────────────────
# Override DATA_DIR to change where state/pending files live.
# Default: ./data/ (project-relative) — set env var AI_NEWS_DATA_DIR to override.

import os as _os
_DATA_DIR = _os.environ.get("AI_NEWS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
SCRAPER_DIR = Path(_DATA_DIR)
SCRAPER_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = SCRAPER_DIR / ".monitor_state.json"
PENDING_FILE = SCRAPER_DIR / ".pending_items.json"
LOCK_FILE = SCRAPER_DIR / ".monitor.lock"

GOOGLE_AI_BLOG_NAME = "Google AI Blog"
GOOGLE_AI_RSS = "https://blog.google/innovation-and-ai/technology/ai/rss/"
DEEPMIND_SITEMAP = "https://deepmind.google/sitemap.xml"
ANTHROPIC_SCRAPER = Path(__file__).resolve().parent / "scrape.py"
BLOGWATCHER_BIN = os.environ.get("BLOGWATCHER_BIN", "blogwatcher-cli")
BLOGWATCHER_DB = os.environ.get("BLOGWATCHER_DB", str(Path(_DATA_DIR) / "blogwatcher.db"))

# Prune seen_urls entries older than this (prevents unbounded state growth)
SEEN_URL_TTL_DAYS = 30


# ─── Locking ───────────────────────────────────────────────────────────────

def acquire_lock():
    """Try to acquire an exclusive file lock. Returns fd on success, None if busy."""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        return None


def release_lock(fd):
    """Release and remove lock file."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


# ─── State ─────────────────────────────────────────────────────────────────

def load_state():
    """Load state file with corruption guard and TTL pruning."""
    state = {"seen_urls": {}, "last_run": None}
    if not STATE_FILE.exists():
        return state

    try:
        raw = STATE_FILE.read_text().strip()
        if not raw:
            return state
        state = json.loads(raw)
    except json.JSONDecodeError:
        print("[WARN] Corrupt monitor state — starting fresh", file=sys.stderr)
        return {"seen_urls": {}, "last_run": None}

    # Prune seen_urls older than TTL to prevent unbounded growth
    if "seen_urls" in state and isinstance(state["seen_urls"], dict):
        cutoff = datetime.now(timezone.utc) - timedelta(days=SEEN_URL_TTL_DAYS)
        to_delete = []
        kept = 0
        for url, meta in state["seen_urls"].items():
            if not isinstance(meta, dict):
                to_delete.append(url)
                continue
            first_seen = meta.get("first_seen", "")
            try:
                if first_seen:
                    dt = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                    if dt < cutoff:
                        to_delete.append(url)
                        continue
            except (ValueError, TypeError):
                pass  # keep entries with unparseable dates
            kept += 1
        for url in to_delete:
            del state["seen_urls"][url]
        if to_delete:
            print(f"[INFO] Pruned {len(to_delete)} stale seen_urls (>{SEEN_URL_TTL_DAYS}d)", file=sys.stderr)

    return state


def save_state(state):
    """Atomic state write (tmp + rename)."""
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2))
        tmp.replace(STATE_FILE)
    except Exception as e:
        print(f"[WARN] Failed to save state: {e}", file=sys.stderr)


# ─── URL & Markdown utilities ──────────────────────────────────────────────

def normalize_url(url):
    """Strip fragments, validate scheme, normalize trailing slash for DeepMind."""
    url = (url or "").strip()
    if not url:
        return ""

    # Block empty/scheme-only/bogus URLs
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https"):
        return ""
    if not p.netloc:
        return ""
    # Treat bare "https://" or "http://" as bogus
    if len(url) <= len(p.scheme) + 3:  # "https://"
        return ""

    p = p._replace(fragment="")
    url = urllib.parse.urlunparse(p)

    # Force trailing slash consistency on DeepMind blog URLs
    if url.startswith("https://deepmind.google/blog/") and not url.endswith("/"):
        url += "/"

    return url


_MD_ESCAPE = str.maketrans({
    "*": "\\*", "_": "\\_", "`": "\\`", "[": "\\[", "]": "\\]",
    "(": "\\(", ")": "\\)", "~": "\\~", ">": "\\>", "#": "\\#",
    "+": "\\+", "-": "\\-", "=": "\\=", "|": "\\|", "{": "\\{",
    "}": "\\}", ".": "\\.", "!": "\\!",
})


def esc_md(text):
    """Escape Telegram MarkdownV2 special characters in text."""
    if not text:
        return text
    return str(text).translate(_MD_ESCAPE)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_item(entry):
    """Format a single news item for output. Escapes markdown in titles."""
    source = esc_md(entry.get("source", "Unknown"))
    title = esc_md(entry.get("title", "Untitled"))
    url = entry.get("url", "")
    date_str = entry.get("date", "")
    emoji = {"Google AI": "🤖", "DeepMind": "🧠", "Anthropic": "🟠", "OpenAI": "⚡", "Claude": "🟣",
             "HackerNews": "🔶", "Reddit/singularity": "🔴", "Reddit/LocalLLaMA": "🟤",
             "Reddit/MachineLearning": "🔵", "Reddit/ChatGPT": "🟢", "Reddit/artificial": "⚪",
             "GitHub": "🐙",
             "Import AI": "📡", "OpenAI Research": "🔬", "Simon Willison": "🦙",
             "TLDR AI": "📋", "The Sequence": "🧬", "Stratechery": "🎯",
             "No Priors Podcast": "🎙️", "Google AI Blog RSS": "🤖", "DeepMind Blog RSS": "🧠",
             "Latent Space": "🚀", "AI Snake Oil": "🐍", "One Useful Thing": "💡",
             "Marginal Revolution": "📊"}
    prefix = emoji.get(entry.get("source", "Unknown"), "📰")
    lines = [f"{prefix} **{title}**"]
    if date_str:
        lines.append(f"   *{source}* · {date_str}")
    else:
        lines.append(f"   *{source}*")
    lines.append(f"   {url}")
    return "\n".join(lines)


# ─── Source: Google AI Blog (blogwatcher-cli RSS) ───────────────────────────
# Returns: (items_list, new_seen_dict) — caller merges new_seen into state AFTER
# pending file write succeeds. No premature mutation.

def google_ai_blog_new(seen_urls, last_run):
    """Run blogwatcher-cli scan for Google AI Blog, return new items.
    Does NOT mutate state — returns new_seen entries for caller to merge."""
    env = {**os.environ, "BLOGWATCHER_DB": BLOGWATCHER_DB}

    # Ensure blog is registered
    result = subprocess.run(
        [BLOGWATCHER_BIN, "blogs"],
        capture_output=True, text=True, timeout=30,
        env=env
    )
    if GOOGLE_AI_BLOG_NAME not in result.stdout:
        add_result = subprocess.run(
            [BLOGWATCHER_BIN, "add", GOOGLE_AI_BLOG_NAME,
             "https://blog.google", "--feed-url", GOOGLE_AI_RSS],
            capture_output=True, text=True, timeout=30,
            env=env
        )
        if add_result.returncode != 0:
            print(f"[WARN] blogwatcher add failed: {add_result.stderr}", file=sys.stderr)

    # Scan
    scan_result = subprocess.run(
        [BLOGWATCHER_BIN, "scan", GOOGLE_AI_BLOG_NAME],
        capture_output=True, text=True, timeout=60,
        env=env
    )
    if scan_result.returncode != 0:
        print(f"[WARN] blogwatcher scan failed: {scan_result.stderr}", file=sys.stderr)

    # Get unread articles
    result = subprocess.run(
        [BLOGWATCHER_BIN, "articles", "--blog", GOOGLE_AI_BLOG_NAME, "--all", "--since",
         (last_run or "2024-01-01")[:10]],
        capture_output=True, text=True, timeout=30,
        env=env
    )
    if result.returncode != 0:
        print(f"[WARN] blogwatcher articles failed: {result.stderr}", file=sys.stderr)
        return [], {}

    items = []
    new_seen = {}
    now = now_iso()

    blocks = re.split(r'\n\s+(?=\[\d+\])', result.stdout)
    for block in blocks:
        lines = block.strip().split('\n')
        if not lines:
            continue

        title_match = re.search(r'\]\s*(?:\[(?:new|read)\]\s*)?(.+)', lines[0])
        if not title_match:
            continue
        title = title_match.group(1).strip()

        url = ""
        date_str = ""
        for line in lines[1:]:
            if line.strip().startswith("URL:"):
                url = line.split("URL:", 1)[1].strip()
            elif line.strip().startswith("Published:"):
                date_str = line.split("Published:", 1)[1].strip()

        norm_url = normalize_url(url)
        if not norm_url:
            continue  # bogus URL — skip
        if norm_url in seen_urls:
            continue

        entry = {
            "title": title,
            "url": norm_url,
            "source": "Google AI",
            "date": date_str,
            "description": "",
            "image_url": "",
            "category": "",
            "_score": len(title) * 2,  # RSS items get modest score from title depth
            "_source_type": "rss",
            "_metrics": {"rss_feed": GOOGLE_AI_BLOG_NAME},
        }
        new_seen[norm_url] = {
            "title": title,
            "source": "Google AI",
            "date": date_str,
            "first_seen": now,
        }
        items.append(entry)

    return items, new_seen


# ─── Source: DeepMind (sitemap polling) ──────────────────────────────────────

def deepmind_new(seen_urls):
    """Fetch DeepMind sitemap, extract blog URLs, return new ones.
    Rate-limited: 0.5s between per-URL curl fetches. Logs warnings on failures.
    Does NOT mutate state — returns new_seen entries for caller."""
    try:
        result = subprocess.run(
            ["curl", "-sL", "--fail", DEEPMIND_SITEMAP],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            print(f"[WARN] DeepMind sitemap fetch failed (rc={result.returncode})", file=sys.stderr)
            return [], {}
        sitemap_xml = result.stdout
    except Exception as e:
        print(f"[WARN] DeepMind sitemap exception: {e}", file=sys.stderr)
        return [], {}

    # Extract all loc + optional lastmod pairs
    entries = re.findall(
        r'<loc>(https://deepmind\.google/blog/[^<]+)</loc>\s*(?:<lastmod>([^<]*)</lastmod>)?',
        sitemap_xml
    )

    # Filter out the bare /blog/ index page
    entries = [(url, lm) for url, lm in entries if url.rstrip('/') != 'https://deepmind.google/blog']

    # Cap to prevent runaway first-run (no seen_urls → could be 100+ fetches)
    MAX_FIRST_FETCH = 10
    total_found = len(entries)
    if len(entries) > MAX_FIRST_FETCH:
        # Sort by lastmod descending (newest first), take the cap
        entries.sort(key=lambda x: x[1] or "", reverse=True)
        entries = entries[:MAX_FIRST_FETCH]
        print(f"[INFO] DeepMind: capping first-run fetch to {MAX_FIRST_FETCH} of {total_found} items", file=sys.stderr)

    items = []
    new_seen = {}
    now = now_iso()
    fetch_count = 0

    for url, lastmod in entries:
        norm_url = normalize_url(url)
        if not norm_url:
            continue
        if norm_url in seen_urls:
            continue

        # Rate limit: small delay between per-URL fetches (skip first)
        if fetch_count > 0:
            time.sleep(0.5)
        fetch_count += 1

        # Attempt to fetch real title from page (first 8KB only)
        real_title = ""
        try:
            head = subprocess.run(
                ["curl", "-sL", "-r", "0-8192", "--fail", norm_url],
                capture_output=True, text=True, timeout=15
            )
            if head.returncode == 0:
                # Try og:title first
                og_title = re.search(
                    r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)',
                    head.stdout
                )
                if og_title:
                    real_title = og_title.group(1).strip()
                else:
                    t = re.search(r'<title>(.*?)</title>', head.stdout, re.IGNORECASE | re.DOTALL)
                    if t:
                        real_title = t.group(1).strip()
            else:
                print(f"[WARN] DeepMind title fetch failed (rc={head.returncode}): {norm_url}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[WARN] DeepMind title fetch timed out: {norm_url}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] DeepMind title fetch error: {e} — {norm_url}", file=sys.stderr)

        if not real_title:
            slug = norm_url.rstrip('/').split('/')[-1].replace('-', ' ').title()

        entry = {
            "title": real_title,
            "url": norm_url,
            "source": "DeepMind",
            "date": lastmod or "",
            "description": "",
            "image_url": "",
            "category": "",
            "_score": len(real_title) * 3,  # DeepMind blog posts typically dense
            "_source_type": "sitemap",
            "_metrics": {"deepmind_lastmod": lastmod or ""},
        }

        new_seen[norm_url] = {
            "title": real_title,
            "source": "DeepMind",
            "date": lastmod or "",
            "first_seen": now,
        }
        items.append(entry)

    return items, new_seen


# ─── Source: Anthropic/Claude + OpenAI (existing scraper) ────────────────────

def anthropic_openai_new(seen_urls):
    """Check for new items from Anthropic/OpenAI. Uses cached feed if recent,
    runs scraper only if stale (with tight timeout).
    Does NOT mutate state — returns new_seen entries for caller."""

    if not ANTHROPIC_SCRAPER.exists():
        return [], {}

    feed_file = SCRAPER_DIR / "unified_feed.json"

    # If cached feed is recent (<2h), skip the scraper
    if feed_file.exists():
        mtime = feed_file.stat().st_mtime
        age_seconds = time.time() - mtime
        if age_seconds >= 7200:
            try:
                subprocess.run(
                    ["python3", str(ANTHROPIC_SCRAPER)],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(ANTHROPIC_SCRAPER.parent)
                )
            except subprocess.TimeoutExpired:
                print("[WARN] Anthropic/OpenAI scraper timed out — using cached feed", file=sys.stderr)
            except Exception as e:
                print(f"[WARN] Anthropic/OpenAI scraper error: {e}", file=sys.stderr)
    else:
        try:
            subprocess.run(
                ["python3", str(ANTHROPIC_SCRAPER)],
                capture_output=True, text=True, timeout=30,
                cwd=str(ANTHROPIC_SCRAPER.parent)
            )
        except subprocess.TimeoutExpired:
            print("[WARN] Anthropic/OpenAI scraper timed out on first run", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Anthropic/OpenAI scraper error: {e}", file=sys.stderr)

    if not feed_file.exists():
        return [], {}

    try:
        feed = json.loads(feed_file.read_text())
    except json.JSONDecodeError:
        return [], {}

    items = []
    new_seen = {}
    now = now_iso()

    for entry in feed.get("items", []):
        if not isinstance(entry, dict):
            continue
        url = entry.get("url", "")
        norm_url = normalize_url(url)
        if not norm_url or norm_url in seen_urls:
            continue

        source = entry.get("source", "Unknown")
        title = entry.get("title", "Untitled")
        date_str = entry.get("date", "")

        # Normalize source name
        if source == "Anthropic" and entry.get("via") == "Claude Blog":
            source = "Claude"

        clean_entry = {
            "title": title,
            "url": norm_url,
            "source": source,
            "date": date_str,
            "description": entry.get("description", ""),
            "image_url": entry.get("image_url", ""),
            "category": entry.get("category", ""),
            "_score": len(entry.get("description", "")) * 2,  # Scraped content depth
            "_source_type": "scraper",
            "_metrics": {"scraper_source": source},
        }

        new_seen[norm_url] = {
            "title": title,
            "source": source,
            "date": date_str,
            "first_seen": now,
        }
        items.append(clean_entry)

    return items, new_seen


# ─── Source: Hacker News (Algolia API — free, no auth) ──────────────────────
# Searches keywords via HN Algolia, returns stories from last 7 days.
# Six targeted queries. Each hit gets community _score = points.

HN_QUERIES = [
    "Claude", "OpenAI", "MCP",
    "AI agent", "LLM", "artificial intelligence"
]
HN_API = "https://hn.algolia.com/api/v1/search"
HN_QUERY_LIMIT = 30  # hits per query
HN_MAX_AGE_SECONDS = 7 * 86400  # 7 days


def hackernews_new(seen_urls):
    """Search HN Algolia for AI topics, return new items with point scores.
    Returns (items, new_seen)."""
    items = []
    new_seen = {}
    now = now_iso()

    for query in HN_QUERIES:
        try:
            params = urllib.parse.urlencode({
                "query": query,
                "tags": "story",
                "hitsPerPage": HN_QUERY_LIMIT,
            })
            result = subprocess.run(
                ["curl", "-sL", "--fail", f"{HN_API}?{params}"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                print(f"[WARN] HN query '{query}' failed (rc={result.returncode})", file=sys.stderr)
                continue

            data = json.loads(result.stdout)
            hits = data.get("hits", [])
            if not isinstance(hits, list):
                continue

            for hit in hits:
                if not isinstance(hit, dict):
                    continue

                title = (hit.get("title") or "").strip()
                if not title:
                    continue

                article_url = (hit.get("url") or "").strip()
                hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

                # Use article URL for dedup, fall back to HN discussion
                dedup_url = normalize_url(article_url) or normalize_url(hn_url)
                if not dedup_url or dedup_url in seen_urls:
                    continue

                points = hit.get("points", 0) or 0
                created = hit.get("created_at", "")
                date_str = ""
                if created:
                    try:
                        dt = __import__('datetime').datetime.fromisoformat(
                            created.replace("Z", "+00:00")
                        )
                        date_str = dt.strftime("%Y-%m-%d")
                        # Guard: skip stories older than 7 days
                        age = (__import__('datetime').datetime.now(__import__('datetime').timezone.utc) - dt).total_seconds()
                        if age > HN_MAX_AGE_SECONDS:
                            continue
                    except Exception:
                        pass

                num_comments = hit.get("num_comments", 0) or 0

                entry = {
                    "title": title,
                    "url": article_url or hn_url,
                    "source": "HackerNews",
                    "date": date_str,
                    "description": f"{points} points, {num_comments} comments — {title}",
                    "image_url": "",
                    "category": f"hn:{query}",
                    "_score": points,
                    "_source_type": "community",
                    "_metrics": {"hn_points": points, "hn_comments": num_comments},
                }

                new_seen[dedup_url] = {
                    "title": title,
                    "source": "HackerNews",
                    "date": date_str,
                    "first_seen": now,
                }
                items.append(entry)

            # Small delay between queries to be gentle on Algolia
            time.sleep(0.3)

        except json.JSONDecodeError as e:
            print(f"[WARN] HN query '{query}' bad JSON: {e}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[WARN] HN query '{query}' timed out", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] HN query '{query}' error: {e}", file=sys.stderr)

    return items, new_seen


# ─── Source: Reddit (JSON API — free, User-Agent required) ───────────────────
# Fetches hot posts from 5 AI subreddits. Each post gets community _score = upvotes.

REDDIT_SUBS = [
    "singularity", "LocalLLaMA", "MachineLearning",
    "ChatGPT", "artificial"
]
REDDIT_LIMIT = 25  # posts per subreddit
REDDIT_MAX_AGE_SECONDS = 7 * 86400


def reddit_new(seen_urls):
    """Fetch hot posts from AI subreddits via Reddit JSON API.
    Returns (items, new_seen)."""
    items = []
    new_seen = {}
    now = now_iso()

    for sub in REDDIT_SUBS:
        try:
            url = f"https://www.reddit.com/r/{sub}/hot.json?limit={REDDIT_LIMIT}&raw_json=1"
            result = subprocess.run(
                ["curl", "-sL", "--fail",
                 "-H", "User-Agent: HermesNewsBot/1.0 (news monitor; linux)",
                 url],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                print(f"[WARN] Reddit r/{sub} fetch failed (rc={result.returncode})", file=sys.stderr)
                continue

            data = json.loads(result.stdout)
            children = data.get("data", {}).get("children", [])
            if not isinstance(children, list):
                continue

            for child in children:
                if not isinstance(child, dict):
                    continue
                post_data = child.get("data", {})
                if not isinstance(post_data, dict):
                    continue

                title = (post_data.get("title") or "").strip()
                if not title:
                    continue

                # Skip stickied posts (announcements, sub rules)
                if post_data.get("stickied"):
                    continue

                upvotes = post_data.get("score", 0) or 0
                created_utc = post_data.get("created_utc", 0) or 0
                date_str = ""
                if created_utc:
                    try:
                        dt = __import__('datetime').datetime.fromtimestamp(
                            created_utc, tz=__import__('datetime').timezone.utc
                        )
                        date_str = dt.strftime("%Y-%m-%d")
                        age = (__import__('datetime').datetime.now(__import__('datetime').timezone.utc) - dt).total_seconds()
                        if age > REDDIT_MAX_AGE_SECONDS:
                            continue
                    except Exception:
                        pass

                post_url = (post_data.get("url") or "").strip()
                permalink_raw = (post_data.get("permalink") or "").strip()
                if not permalink_raw:
                    continue  # Can't dedup without a URL — skip
                permalink = "https://www.reddit.com" + permalink_raw

                # Use external URL for dedup if present and not a reddit domain
                if post_url and not post_url.startswith("https://www.reddit.com"):
                    dedup_url = normalize_url(post_url)
                else:
                    dedup_url = normalize_url(permalink)

                if not dedup_url or dedup_url in seen_urls:
                    continue

                selftext = (post_data.get("selftext") or "")[:500]
                num_comments = post_data.get("num_comments", 0) or 0

                desc = f"↑{upvotes} | {sub} | 💬{num_comments}"
                if selftext:
                    desc += f" — {selftext[:200]}"

                entry = {
                    "title": title,
                    "url": post_url if post_url else permalink,
                    "source": f"Reddit/{sub}",
                    "date": date_str,
                    "description": desc,
                    "image_url": "",
                    "category": f"reddit:{sub}",
                    "_score": upvotes,
                    "_source_type": "community",
                    "_metrics": {
                        "reddit_upvotes": upvotes,
                        "reddit_comments": num_comments,
                        "reddit_sub": sub,
                    },
                }

                new_seen[dedup_url] = {
                    "title": title,
                    "source": f"Reddit/{sub}",
                    "date": date_str,
                    "first_seen": now,
                }
                items.append(entry)

            time.sleep(0.5)  # Be gentle on Reddit's API

        except json.JSONDecodeError as e:
            print(f"[WARN] Reddit r/{sub} bad JSON: {e}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[WARN] Reddit r/{sub} timed out", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Reddit r/{sub} error: {e}", file=sys.stderr)

    return items, new_seen


# ─── Source: GitHub Trending (Search API — free, 10 req/min, 2h cache) ───────
# Searches GitHub for repos matching AI topics. Scoring: stars × 10.

GITHUB_QUERIES = [
    "MCP server",
    "AI agent framework",
    "LLM tool",
    "AI workflow",
]
GITHUB_API = "https://api.github.com/search/repositories"
GITHUB_PER_QUERY = 20
GITHUB_CACHE_FILE = SCRAPER_DIR / ".github_cache.json"
GITHUB_CACHE_TTL = 7200  # 2 hours
GITHUB_MIN_STARS = 50  # Only repos with significant traction
GITHUB_MAX_AGE_DAYS = 30  # Don't pick up ancient repos
GITHUB_RATELIMIT_CACHE = SCRAPER_DIR / ".github_ratelimit.json"


def github_trending_new(seen_urls):
    """Search GitHub for trending AI repos. Uses 2h cache to stay under rate limit.
    Returns (items, new_seen). Stars are log-scaled for cross-source comparability."""
    # Check rate-limit backoff first (from previous 403/429 responses)
    if GITHUB_RATELIMIT_CACHE.exists():
        try:
            rl = json.loads(GITHUB_RATELIMIT_CACHE.read_text())
            retry_after = rl.get("retry_after", 0)
            if time.time() < retry_after:
                print(f"[WARN] GitHub rate-limited — skipping until {datetime.fromtimestamp(retry_after).strftime('%H:%M:%S')}", file=sys.stderr)
                return [], {}
        except Exception:
            pass

    # Check cache first
    if GITHUB_CACHE_FILE.exists():
        try:
            cache = json.loads(GITHUB_CACHE_FILE.read_text())
            cache_age = time.time() - cache.get("fetched_at", 0)
            if cache_age < GITHUB_CACHE_TTL and "repos" in cache:
                # Use cached data — filter against seen_urls
                return _github_from_cache(cache["repos"], seen_urls)
        except (json.JSONDecodeError, KeyError):
            pass  # Corrupt cache — re-fetch

    # Fresh fetch
    repos = []
    for query in GITHUB_QUERIES:
        try:
            q = urllib.parse.quote(f"{query} stars:>={GITHUB_MIN_STARS}")
            url = f"{GITHUB_API}?q={q}&sort=stars&order=desc&per_page={GITHUB_PER_QUERY}"
            result = subprocess.run(
                ["curl", "-sL", "--fail",
                 "-H", "Accept: application/vnd.github.v3+json",
                 "-H", "User-Agent: HermesNewsBot/1.0",
                 url],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                print(f"[WARN] GitHub query '{query}' failed (rc={result.returncode})", file=sys.stderr)
                # Check for rate-limit response (403 = rate limit, 429 = too many)
                if result.returncode in (403, 429):
                    retry_after = time.time() + 3600  # Default: 1 hour backoff
                    try:
                        body = json.loads(result.stdout or "{}")
                        msg = body.get("message", "").lower()
                        if "api rate limit exceeded" in msg or "secondary rate limit" in msg:
                            retry_after = time.time() + 3600
                    except Exception:
                        pass
                    # Cache the backoff
                    try:
                        atomic_write(GITHUB_RATELIMIT_CACHE, json.dumps({
                            "retry_after": retry_after,
                            "reason": f"HTTP {result.returncode}",
                            "at": now_iso(),
                        }))
                    except Exception:
                        pass
                    print(f"[WARN] GitHub rate-limited — backing off for 1h", file=sys.stderr)
                    break  # Stop all GitHub queries — rate-limited
                continue

            data = json.loads(result.stdout)
            gh_items = data.get("items", [])
            if not isinstance(gh_items, list):
                continue

            for repo in gh_items:
                if not isinstance(repo, dict):
                    continue
                full_name = repo.get("full_name", "")
                stars = repo.get("stargazers_count", 0) or 0
                desc = (repo.get("description") or "").strip()
                html_url = repo.get("html_url", "")
                created_at = repo.get("created_at", "")
                pushed_at = repo.get("pushed_at", "")
                language = repo.get("language") or ""
                topics = repo.get("topics", []) or []

                if not full_name or not html_url:
                    continue

                # Guard: skip repos without recent activity (stale repos)
                if pushed_at:
                    try:
                        pushed_dt = __import__('datetime').datetime.fromisoformat(
                            pushed_at.replace("Z", "+00:00")
                        )
                        age_days = (__import__('datetime').datetime.now(__import__('datetime').timezone.utc) - pushed_dt).days
                    except Exception:
                        age_days = 0
                else:
                    age_days = 999  # no push date → assume very stale

                repos.append({
                    "full_name": full_name,
                    "stars": stars,
                    "description": desc,
                    "html_url": html_url,
                    "created_at": created_at,
                    "pushed_at": pushed_at,
                    "language": language,
                    "topics": topics,
                    "query": query,
                    "age_days": age_days,
                })

            time.sleep(7.0)  # GitHub unauthenticated: 10 req/min → 7s between calls

        except json.JSONDecodeError as e:
            print(f"[WARN] GitHub query '{query}' bad JSON: {e}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"[WARN] GitHub query '{query}' timed out", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] GitHub query '{query}' error: {e}", file=sys.stderr)

    # Deduplicate by full_name across queries
    seen_repos = {}
    for repo in repos:
        name = repo["full_name"]
        if name not in seen_repos or repo["stars"] > seen_repos[name]["stars"]:
            seen_repos[name] = repo
    repos = list(seen_repos.values())

    # Sort by stars (most relevant first) and cache
    repos.sort(key=lambda r: r["stars"], reverse=True)
    try:
        atomic_write(GITHUB_CACHE_FILE, json.dumps({
            "fetched_at": time.time(),
            "fetched_at_iso": now_iso(),
            "repos": repos,
        }, indent=2))
    except Exception as e:
        print(f"[WARN] Failed to write GitHub cache: {e}", file=sys.stderr)

    return _github_from_cache(repos, seen_urls)


def _github_from_cache(repos, seen_urls):
    """Filter cached repos against seen_urls, return (items, new_seen)."""
    items = []
    new_seen = {}
    now = now_iso()

    for repo in repos:
        url = repo.get("html_url", "")
        norm_url = normalize_url(url)
        if not norm_url or norm_url in seen_urls:
            continue

        # Skip repos with no recent activity (stale, abandoned)
        age_days = repo.get("age_days", 0)
        if age_days > GITHUB_MAX_AGE_DAYS:
            continue

        stars = repo["stars"]
        desc = repo.get("description", "")
        full_name = repo["full_name"]
        language = repo.get("language", "")
        topics = ", ".join(repo.get("topics", [])[:5])
        pushed = repo.get("pushed_at", "")[:10]

        desc_line = f"⭐{stars}"
        if language:
            desc_line += f" | {language}"
        if topics:
            desc_line += f" | {topics}"
        if desc:
            desc_line += f" — {desc[:200]}"
        if pushed:
            desc_line += f" (active: {pushed})"

        entry = {
            "title": f"{full_name} (⭐{stars})",
            "url": url,
            "source": "GitHub",
            "date": repo.get("pushed_at", "")[:10] or repo.get("created_at", "")[:10],
            "description": desc_line,
            "image_url": "",
            "category": f"github:{repo.get('query', '')}",
            "_score": int(__import__('math').log10(max(stars, 1)) * 100),  # log scale: 50★→170, 1K→300, 100K→500
            "_source_type": "community",
            "_metrics": {
                "github_stars": stars,
                "github_language": language,
                "github_full_name": full_name,
            },
        }

        new_seen[norm_url] = {
            "title": entry["title"],
            "source": "GitHub",
            "date": entry["date"],
            "first_seen": now,
        }
        items.append(entry)

    return items, new_seen


# ─── Source: Insight RSS (newsletters, research blogs, podcasts) ──────────────
# Curated feeds that provide analysis, frameworks, and synthesis — not press releases.
# These are the "Greg Isenberg inputs" — sources that explain WHY, not just WHAT.
#
# Score formula: title length × 2 + description length. Insight sources start with
# higher base scores than scraper items because they're pre-curated.

INSIGHT_FEEDS = [
    ("Import AI", "https://importai.substack.com/feed"),
    ("OpenAI Research", "https://openai.com/blog/rss.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/entries/"),
    ("TLDR AI", "https://tldr.tech/api/rss/ai"),
    ("The Sequence", "https://thesequence.substack.com/feed"),
    ("Stratechery", "https://stratechery.com/feed/"),
    ("No Priors Podcast", "https://feeds.megaphone.fm/nopriors"),
    ("Google AI Blog RSS", "https://blog.google/technology/ai/rss/"),
    ("DeepMind Blog RSS", "https://deepmind.google/blog/feed/"),
    ("Latent Space", "https://www.latent.space/feed"),
    ("AI Snake Oil", "https://www.aisnakeoil.com/feed/"),
    ("One Useful Thing", "https://www.oneusefulthing.org/feed"),
    ("Marginal Revolution", "https://marginalrevolution.com/feed"),
]
INSIGHT_MAX_AGE_SECONDS = 14 * 86400  # 14 days — newsletters are less frequent


def insight_rss_new(seen_urls):
    """Fetch curated insight RSS feeds. Each item = analysis/framework, not news.
    Returns (items, new_seen)."""
    items = []
    new_seen = {}
    now = now_iso()

    for source_name, feed_url in INSIGHT_FEEDS:
        try:
            result = subprocess.run(
                ["curl", "-sL", "--max-time", "15", feed_url],
                capture_output=True, text=True, timeout=20
            )
            if result.returncode != 0:
                print(f"[WARN] Insight RSS '{source_name}' failed (rc={result.returncode})", file=sys.stderr)
                continue

            xml = result.stdout

            # Parse both RSS and Atom
            # RSS: <item>...</item>, Atom: <entry>...</entry>
            is_atom = '<feed' in xml[:500]
            if is_atom:
                entry_blocks = re.split(r'</?entry>', xml)
            else:
                entry_blocks = re.split(r'</?item>', xml)

            for block in entry_blocks[1::2]:  # every other (content between tags)
                # Extract title
                title_match = re.search(r'<title[^>]*><!\[CDATA\[(.*?)\]\]></title>|<title[^>]*>(.*?)</title>',
                                        block, re.DOTALL)
                title = ""
                if title_match:
                    title = (title_match.group(1) or title_match.group(2) or "").strip()
                if not title:
                    continue

                # Extract link
                link = ""
                link_match = re.search(r'<link[^>]*href="([^"]*)"', block)  # Atom
                if not link_match:
                    link_match = re.search(r'<link>(.*?)</link>', block)  # RSS
                if link_match:
                    link = link_match.group(1).strip()
                if not link:
                    continue

                norm_url = normalize_url(link)
                if not norm_url or norm_url in seen_urls:
                    continue

                # Extract date
                date_str = ""
                date_match = re.search(r'<published>([^<]*)</published>|<pubDate>([^<]*)</pubDate>',
                                       block, re.DOTALL)
                if date_match:
                    raw_date = date_match.group(1) or date_match.group(2) or ""
                    # Normalize to YYYY-MM-DD
                    try:
                        from email.utils import parsedate_to_datetime
                        dt = parsedate_to_datetime(raw_date)
                        date_str = dt.strftime("%Y-%m-%d")
                        age = (datetime.now(timezone.utc) - dt).total_seconds()
                        if age > INSIGHT_MAX_AGE_SECONDS:
                            continue
                    except Exception:
                        date_str = raw_date[:10]  # best effort

                # Extract description/summary
                desc = ""
                desc_match = re.search(r'<description>(.*?)</description>|<summary[^>]*>(.*?)</summary>',
                                       block, re.DOTALL)
                if desc_match:
                    raw_desc = (desc_match.group(1) or desc_match.group(2) or "").strip()
                    # Strip CDATA, HTML tags
                    raw_desc = re.sub(r'<!\[CDATA\[|\]\]>', '', raw_desc)
                    raw_desc = re.sub(r'<[^>]+>', '', raw_desc)
                    desc = raw_desc[:500].strip()

                # Score: title depth + description depth
                score = len(title) * 2 + len(desc)

                entry = {
                    "title": title,
                    "url": norm_url,
                    "source": source_name,
                    "date": date_str,
                    "description": desc,
                    "image_url": "",
                    "category": f"insight:{source_name}",
                    "_score": score,
                    "_source_type": "insight",
                    "_metrics": {"feed": source_name},
                }

                new_seen[norm_url] = {
                    "title": title,
                    "source": source_name,
                    "date": date_str,
                    "first_seen": now,
                }
                items.append(entry)

        except subprocess.TimeoutExpired:
            print(f"[WARN] Insight RSS '{source_name}' timed out", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Insight RSS '{source_name}' error: {e}", file=sys.stderr)

    return items, new_seen


# ─── Helper: atomic write ────────────────────────────────────────────────────

def atomic_write(path, content):
    """Atomically write a file via temp + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(path)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    # ── Lock ──────────────────────────────────────────────────────────────
    lock_fd = acquire_lock()
    if lock_fd is None:
        sys.exit(0)  # another instance running

    try:
        state = load_state()
        seen_urls = state.get("seen_urls", {})
        last_run = state.get("last_run", None)
        all_new_seen = {}  # accumulate seen entries; commit only after pending write
        all_items = []

        # 1. Google AI Blog (RSS)
        try:
            ga_items, ga_seen = google_ai_blog_new(seen_urls, last_run)
            all_items.extend(ga_items)
            all_new_seen.update(ga_seen)
        except Exception as e:
            print(f"[WARN] Google AI Blog check failed: {e}", file=sys.stderr)

        # 2. DeepMind (sitemap)
        try:
            dm_items, dm_seen = deepmind_new(seen_urls)
            all_items.extend(dm_items)
            all_new_seen.update(dm_seen)
        except Exception as e:
            print(f"[WARN] DeepMind check failed: {e}", file=sys.stderr)

        # 3. Anthropic/OpenAI (existing scraper)
        try:
            ao_items, ao_seen = anthropic_openai_new(seen_urls)
            all_items.extend(ao_items)
            all_new_seen.update(ao_seen)
        except Exception as e:
            print(f"[WARN] Anthropic/OpenAI scraper failed: {e}", file=sys.stderr)

        # 4. Hacker News (Algolia — free)
        try:
            hn_items, hn_seen = hackernews_new(seen_urls)
            all_items.extend(hn_items)
            all_new_seen.update(hn_seen)
        except Exception as e:
            print(f"[WARN] HN check failed: {e}", file=sys.stderr)

        # 5. Reddit (JSON API — free)
        try:
            rd_items, rd_seen = reddit_new(seen_urls)
            all_items.extend(rd_items)
            all_new_seen.update(rd_seen)
        except Exception as e:
            print(f"[WARN] Reddit check failed: {e}", file=sys.stderr)

        # 6. GitHub Trending (Search API — free, 2h cache)
        try:
            gh_items, gh_seen = github_trending_new(seen_urls)
            all_items.extend(gh_items)
            all_new_seen.update(gh_seen)
        except Exception as e:
            print(f"[WARN] GitHub check failed: {e}", file=sys.stderr)

        # 7. Insight RSS (newsletters, research blogs, podcasts — free)
        try:
            in_items, in_seen = insight_rss_new(seen_urls)
            all_items.extend(in_items)
            all_new_seen.update(in_seen)
        except Exception as e:
            print(f"[WARN] Insight RSS check failed: {e}", file=sys.stderr)

        # No new items → update last_run, save, exit silently
        if not all_items:
            state["last_run"] = now_iso()
            save_state(state)
            return

        # ── Build pending items in temp file (NOT real file yet) ────────────
        # We defer the rename until AFTER read-all succeeds. If read-all fails,
        # the temp is discarded and blogwatcher items will be re-detected correctly.
        env = {**os.environ, "BLOGWATCHER_DB": BLOGWATCHER_DB}
        try:
            pending = {"items": [], "checked_at": now_iso()}
            if PENDING_FILE.exists():
                try:
                    pending = json.loads(PENDING_FILE.read_text())
                    if not isinstance(pending, dict):
                        pending = {"items": [], "checked_at": now_iso()}
                except json.JSONDecodeError:
                    pending = {"items": [], "checked_at": now_iso()}

            seen_urls_in_pending = {item.get("url") for item in pending.get("items", [])
                                     if isinstance(item, dict)}
            for entry in all_items:
                u = entry.get("url")
                if u and u not in seen_urls_in_pending:
                    pending["items"].append(entry)
                    seen_urls_in_pending.add(u)
            pending["checked_at"] = now_iso()

            # Write to TEMP file only — don't rename yet
            pending_tmp = PENDING_FILE.with_suffix(".pending_tmp")
            pending_tmp.write_text(json.dumps(pending, indent=2))
        except Exception as e:
            print(f"[CRIT] Failed to build pending file: {e}", file=sys.stderr)
            # Do NOT commit anything — retry on next run
            return

        # ── Mark blogwatcher as read BEFORE committing pending ──────────────
        read_all_ok = True
        try:
            subprocess.run(
                [BLOGWATCHER_BIN, "read-all", "--blog", GOOGLE_AI_BLOG_NAME, "--yes"],
                capture_output=True, text=True, timeout=30,
                env=env
            )
        except Exception as e:
            print(f"[WARN] blogwatcher read-all failed: {e}", file=sys.stderr)
            read_all_ok = False

        if not read_all_ok:
            # Discard temp pending — blogwatcher still sees items as unread.
            # Next run will re-detect the same items and retry read-all.
            # State is NOT saved, so monitor and blogwatcher stay in sync.
            try:
                pending_tmp.unlink()
            except Exception:
                pass
            print("[WARN] Skipping state save — read-all failed, will retry next run", file=sys.stderr)
            return

        # ── read-all succeeded — now it's safe to commit everything ─────────

        # 1. Commit pending file (rename temp → real, atomic)
        try:
            pending_tmp.replace(PENDING_FILE)
        except Exception as e:
            print(f"[CRIT] Failed to rename pending file: {e}", file=sys.stderr)
            return

        # 2. Merge new_seen into state
        seen_urls.update(all_new_seen)
        state["seen_urls"] = seen_urls
        state["last_run"] = now_iso()
        save_state(state)

        # ── Output ─────────────────────────────────────────────────────────
        grouped = defaultdict(list)
        for entry in all_items:
            grouped[entry.get("source", "Unknown")].append(entry)

        total = len(all_items)
        sources = ", ".join(sorted(grouped.keys()))
        print(f"🔥 **{total} new AI news item{'s' if total > 1 else ''}** from {sources}\n")

        for source in sorted(grouped.keys()):
            for entry in grouped[source]:
                print(format_item(entry))
                print()

        tz = datetime.now().astimezone().tzname() or "Local"
        print(f"_Checked at {datetime.now().strftime('%H:%M')} {tz}_ · [Apify slot: empty]")

    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
