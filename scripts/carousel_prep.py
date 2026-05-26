#!/usr/bin/env python3
"""
Carousel Writer Pre-Processor (Layer 2 wrapper).

Runs BEFORE the LLM. Does three things:
1. Extracts ONE unprocessed item from pending_items.json → .current_item.json
2. Picks the next video asset (round-robin rotation) → injects into .current_item.json
3. If no items, exits silently (stdout empty → $0 run, no LLM invoked)

Safety hardening (2026-05-25 red-team):
- File locking prevents overlapping cron runs from double-processing
- Atomic writes (tmp + rename) for .current_item.json and .carousels_processed.json
- Weighted selection: prefers rich descriptions but with FIFO fairness — no starvation
- "processing" flag prevents re-pick on LLM failure
- try/except guards on all JSON reads
"""

import fcntl
import json
import os
import sys
import time
from pathlib import Path

# ─── Config — override via env vars ──────────────────────────────────────────
_DATA_DIR = os.environ.get("AI_NEWS_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
SCRAPER_DIR = Path(_DATA_DIR)
PENDING_FILE = SCRAPER_DIR / ".pending_items.json"
PROCESSED_FILE = SCRAPER_DIR / ".carousels_processed.json"
CURRENT_ITEM_FILE = SCRAPER_DIR / ".current_item.json"
LOCK_FILE = SCRAPER_DIR / ".carousel_prep.lock"

# Available video assets (in rotation order — user-prepared with positioning + opacity)
# All 10 variants: each shape has 2 position variants (BR/TR, bot/mid for world)
VIDEO_ASSETS = [
    "diamond-BR.mp4",
    "diamond-TR.mp4",
    "hex-BR.mp4",
    "hex-TR.mp4",
    "sphere-BR.mp4",
    "sphere-TR.mp4",
    "star-BR.mp4",
    "star-TR.mp4",
    "world-bot.mp4",
    "world-mid.mp4",
]

# Set VIDEO_DIR env var to point to your video assets directory
VIDEO_DIR = Path(os.environ.get("VIDEO_DIR", str(Path(__file__).resolve().parent.parent / "assets" / "pinterest-visuals")))

# Max age in seconds for items in the pending queue before they get priority boost
MAX_ITEM_AGE = 3600  # 1 hour


def acquire_lock():
    """Try to acquire an exclusive file lock. Returns True on success."""
    try:
        LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (IOError, OSError):
        return None


def release_lock(fd):
    """Release the lock file."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except Exception:
        pass


def atomic_write(path, data_str):
    """Write string to path atomically via temp file + rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data_str)
    tmp.replace(path)


def pick_video_asset(processed):
    """Pick next video asset in rotation, skipping missing files.
    Returns None if NO assets exist (caller should handle gracefully)."""
    last = processed.get("last_video_asset", "")
    try:
        idx = VIDEO_ASSETS.index(last)
        next_idx = (idx + 1) % len(VIDEO_ASSETS)
    except ValueError:
        next_idx = 0

    # Try each asset in order, wrap around if one is missing
    for i in range(len(VIDEO_ASSETS)):
        candidate = VIDEO_ASSETS[(next_idx + i) % len(VIDEO_ASSETS)]
        if (VIDEO_DIR / candidate).exists():
            return candidate

    # All assets missing — return first as name but log warning
    print(f"[WARN] No video assets found in {VIDEO_DIR} — carousel will use black background",
          file=sys.stderr)
    return None


def load_json_safe(path, default=None):
    """Load JSON with corruption guard. Returns default on failure."""
    if not path.exists():
        return default if default is not None else {}
    try:
        raw = path.read_text().strip()
        if not raw:
            return default if default is not None else {}
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(f"[WARN] Corrupt JSON in {path} — using defaults", file=sys.stderr)
        return default if default is not None else {}


def main():
    # ── Lock ──────────────────────────────────────────────────────────────
    lock_fd = acquire_lock()
    if lock_fd is None:
        # Another instance is running — safe to exit
        sys.exit(0)

    try:
        # ── Load state ───────────────────────────────────────────────────
        pending = load_json_safe(PENDING_FILE, {"items": []})
        items = pending.get("items", [])
        if not isinstance(items, list):
            items = []

        processed = load_json_safe(PROCESSED_FILE, {"processed_urls": {}, "last_video_asset": ""})
        processed_urls = processed.get("processed_urls", {})
        if not isinstance(processed_urls, dict):
            processed_urls = {}

        # ── Find unprocessed items ───────────────────────────────────────
        now = time.time()
        unprocessed = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "")
            if not url:
                continue

            # Skip if already processed AND not currently being retried
            proc_entry = processed_urls.get(url)
            if proc_entry is not None:
                # URL is in processed — check if it's fully done or still in-flight
                if isinstance(proc_entry, dict):
                    if not proc_entry.get("processing"):
                        # Fully processed — skip
                        continue
                    # Has "processing" flag — check if it's stale (>5 min = retry)
                    proc_ts = proc_entry.get("processing")
                    if isinstance(proc_ts, str):
                        try:
                            from datetime import datetime, timezone
                            proc_dt = datetime.fromisoformat(proc_ts.replace("Z", "+00:00"))
                            age = (datetime.now(timezone.utc) - proc_dt).total_seconds()
                            if age < 300:  # still fresh — skip
                                continue
                        except Exception:
                            pass  # can't parse timestamp — retry
                else:
                    # String or other value — treat as processed
                    continue
            # URL not in processed OR stale processing flag → candidate

            # ── Content sufficiency gate ──────────────────────────────────
            desc_len = len(item.get("description", ""))
            source_type = item.get("_source_type", "")

            # Community, insight, and RSS items pass even with thin descriptions —
            # their value is the signal/curation, not raw description length
            SKIP_GATE_TYPES = {"community", "insight", "rss"}
            if source_type not in SKIP_GATE_TYPES:
                if desc_len < 50:
                    source = item.get("source", "unknown")
                    print(f"[SKIP] {source} item has only {desc_len} chars — insufficient for carousel", file=sys.stderr)
                    continue
                if desc_len == 0 and (not item.get("image_url") or not item["image_url"].startswith("http")):
                    continue

            # ── Scoring: community signal takes priority ──────────────────
            base_score = item.get("_score", 0)
            if not isinstance(base_score, (int, float)):
                base_score = 0

            # If no explicit _score, fall back to description length
            if base_score == 0:
                base_score = desc_len

            # Age boost: older items get priority bump (FIFO fairness)
            date_str = item.get("date", "") or pending.get("checked_at", "") or ""
            age_seconds = MAX_ITEM_AGE  # default: not stale
            if date_str:
                try:
                    from datetime import datetime, timezone
                    if "T" in date_str:
                        item_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    else:
                        item_dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    age_seconds = max(0, now - item_dt.timestamp())
                except Exception:
                    pass

            # Age boost: older items get a modest priority bump (FIFO fairness).
            # CAPPED at 200 to prevent a 1-month-old item from swamping
            # legitimate community signal (HN/Reddit/GitHub _score).
            age_boost = min(200, max(0, (age_seconds - MAX_ITEM_AGE) / 60))
            total_score = base_score + age_boost

            unprocessed.append((total_score, item))

        if not unprocessed:
            sys.exit(0)

        # ── Pick best item ───────────────────────────────────────────────
        unprocessed.sort(key=lambda x: x[0], reverse=True)
        _, best_item = unprocessed[0]

        # ── Mark as "processing" BEFORE writing current_item ────────────
        # This prevents re-pick even if the LLM fails before updating processed_urls
        url = best_item["url"]
        now_iso = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        processed_urls[url] = {
            "processing": now_iso,
            "title": best_item.get("title", "")[:100],
        }
        processed["processed_urls"] = processed_urls

        # Update rotation state
        video_asset = pick_video_asset(processed)
        if video_asset:
            processed["last_video_asset"] = video_asset

        atomic_write(PROCESSED_FILE, json.dumps(processed, indent=2))

        # ── Write current_item (atomic) ──────────────────────────────────
        best_item["_video_asset"] = video_asset
        best_item["_last_video_asset"] = processed.get("last_video_asset", video_asset or "none")
        atomic_write(CURRENT_ITEM_FILE, json.dumps(best_item, indent=2))

        # ── Remove chosen item from pending to prevent unbounded growth ──
        # Now that this item is in-flight (processing flag set), remove it
        # from the pending queue so it doesn't linger forever.
        best_url = best_item["url"]
        new_items = [i for i in items if isinstance(i, dict) and i.get("url") != best_url]
        pending["items"] = new_items
        atomic_write(PENDING_FILE, json.dumps(pending, indent=2))

        # ── Output context for LLM ───────────────────────────────────────
        print(f"ARTICLE TO PROCESS: {best_item.get('title', 'Untitled')} ({best_item.get('source', 'Unknown')})")
        print(f"URL: {best_item.get('url', '')}")
        desc = best_item.get('description', '')
        print(f"Description: {desc[:200] if desc else '(none)'}")
        print(f"Image: {best_item.get('image_url', 'none')}")
        print(f"Category: {best_item.get('category', '')}")
        print(f"VIDEO ASSET (pre-selected): {video_asset or 'NONE — will use black background'}")
        print(f"Last used asset: {processed.get('last_video_asset', 'none')}")

    finally:
        release_lock(lock_fd)


if __name__ == "__main__":
    main()
