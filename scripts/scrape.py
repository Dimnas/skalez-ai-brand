#!/usr/bin/env python3
"""
AI News Scraper — Layer 1 Content Ingestion  v2.0 (red team fixes applied)
Pulls Anthropic & OpenAI news from official blogs + aggregators.
Output: ~/.hermes/scraper/unified_feed.json
Fixes: junk filtering, date normalization, HTML cleanup, img detection, delta mode.
"""

import subprocess, json, re, html as html_mod, os, time, hashlib
from datetime import datetime, timezone, timedelta

SCRAPER_DIR = os.path.expanduser('~/.hermes/scraper')
IMG_DIR = os.path.join(SCRAPER_DIR, 'downloaded_images')
OUTPUT_FILE = os.path.join(SCRAPER_DIR, 'unified_feed.json')
STATE_FILE = os.path.join(SCRAPER_DIR, '.scraper_state.json')
os.makedirs(IMG_DIR, exist_ok=True)

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'

# ── Junk URL patterns (fix #1) ──
JUNK_URL_PATTERNS = [
    '/about', '/sign-in', '/sign-up', '/login', '/contact',
    '/artificial-intelligence-news/',  # archive category pages
    '/author/', '/page/', '/tag/', '/category/',
]
JUNK_TITLE_PATTERNS = [
    'about us', 'the decoder', 'ai community', 'archive',
    'frontier radar', 'ai research', 'ai in practice',
    'sign in', 'sign up', 'log in',
]

# ── Util: ISO date normalization (fix #4) ──
MONTHS = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
          'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}

def normalize_date(raw):
    """Convert ANY date format to ISO YYYY-MM-DD."""
    if not raw: return ''
    raw = raw.strip()
    # Already ISO
    m = re.match(r'^(\d{4})-(\d{2})-(\d{2})', raw)
    if m: return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    # "May 6, 2026"
    m = re.match(r'(\w{3,4})\s+(\d{1,2}),?\s*(\d{4})', raw)
    if m:
        mon = MONTHS.get(m.group(1).lower()[:3], 1)
        return f'{m.group(3)}-{mon:02d}-{int(m.group(2)):02d}'
    # RFC 2822 "Tue, 06 May 2026 00:00:00 GMT"
    m = re.match(r'\w{3},\s*(\d{1,2})\s+(\w{3,4})\s+(\d{4})', raw)
    if m:
        mon = MONTHS.get(m.group(2).lower()[:3], 1)
        return f'{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}'
    return raw[:10] if len(raw) >= 10 else raw

def clean_text(s):
    """Aggressive HTML entity removal (fix #5)."""
    if not s: return ''
    s = html_mod.unescape(s)
    s = html_mod.unescape(s)  # double-unescape for nested entities
    s = re.sub(r'&#?[a-z0-9]+;', '', s, flags=re.I)  # kill remaining entities
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def is_junk(title, url):
    """Blocklist check for non-article pages (fix #1)."""
    t = title.lower().strip()
    for pattern in JUNK_TITLE_PATTERNS:
        if pattern == t or (len(pattern) > 5 and pattern in t and len(t) < 40):
            return True
    for pattern in JUNK_URL_PATTERNS:
        if pattern in url.lower():
            return True
    return False

def is_bad_image(url):
    """Detect SVGs / opengraph illustrations that aren't real photos (fix #3)."""
    if not url: return True
    if 'opengraph-illustration' in url: return True
    if url.endswith('.svg'): return True
    if 'placeholder' in url.lower(): return True
    return False

# ── State management (fix #9) ──
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {'last_run': None, 'seen_ids': []}

def save_state(seen_ids):
    with open(STATE_FILE, 'w') as f:
        json.dump({'last_run': datetime.now(timezone.utc).isoformat(), 'seen_ids': seen_ids}, f)

# ── HTTP ──
def fetch(url, timeout=20):
    for attempt in range(3):
        try:
            r = subprocess.run(['curl', '-sL', url,
                '-H', f'User-Agent: {UA}',
                '-H', 'Accept: text/html,application/xhtml+xml',
                '-m', str(timeout)],
                capture_output=True, text=True, timeout=timeout+5)
            if r.returncode == 0 and len(r.stdout) > 200:
                return r.stdout
            time.sleep(1.5)
        except:
            time.sleep(2)
    return ''

def download_image(url, item_id):
    ext = url.split('?')[0].rsplit('.', 1)[-1][:4]
    if ext not in ('jpg', 'jpeg', 'png', 'webp', 'web'):
        ext = 'jpg'
    path = os.path.join(IMG_DIR, f'{item_id}.{ext}')
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return path
    subprocess.run(['curl', '-sL', url, '-o', path, '-m', '20'],
        capture_output=True, timeout=25)
    if os.path.exists(path) and os.path.getsize(path) > 2000:
        return path
    # Clean up tiny/broken downloads
    if os.path.exists(path): os.remove(path)
    return None

def extract_og(page):
    """Extract OG metadata from HTML page."""
    t = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', page)
    d = re.search(r'<meta\s+property="og:description"\s+content="([^"]*)"', page)
    i = re.search(r'<meta\s+property="og:image"\s+content="([^"]*)"', page)
    return (
        clean_text(t.group(1)) if t else '',
        clean_text(d.group(1)[:600]) if d else '',
        i.group(1) if i else ''
    )

# ═══════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════
def main():
    state = load_state()
    all_items = []
    print(f"[{datetime.now().strftime('%H:%M:%S')}] AI News Scraper v2.0")

    # ── SOURCE 1: ANTHROPIC BLOG (direct) ──
    print("📡 Anthropic Blog...")
    page = fetch('https://www.anthropic.com/news')
    slugs = set(re.findall(r'href="(/news/[^"]+)"', page))
    slugs = {s for s in slugs if len(s) > 7 and not s.endswith('/')}

    for slug in sorted(slugs)[:30]:
        article = fetch(f'https://www.anthropic.com{slug}', timeout=12)
        if not article: continue
        
        title, desc, img_url = extract_og(article)
        if not title or len(title) < 5: continue
        
        # Strip "| Anthropic" suffix
        title = re.sub(r'\s*\|\s*Anthropic\s*$', '', title).strip()
        
        # Date from visible text
        dt_match = re.search(r'(\w+ \d{1,2}, \d{4})', article)
        iso_date = normalize_date(dt_match.group(1)) if dt_match else ''
        
        # Skip old junk if we have state
        item_id = hashlib.md5(f'anthropic:{slug}'.encode()).hexdigest()[:12]
        if item_id in state['seen_ids']:
            continue
        
        item = {
            'id': item_id,
            'title': title[:200],
            'description': desc,
            'image_url': img_url if not is_bad_image(img_url) else '',
            'image_note': 'svg-illustration' if 'opengraph' in (img_url or '') else '',
            'date': iso_date,
            'url': f'https://www.anthropic.com{slug}',
            'source': 'Anthropic',
            'via': None,
            'category': 'Product' if 'product' in article.lower() else 'Announcement',
        }
        
        # Only download real raster images
        if item['image_url'] and not is_bad_image(item['image_url']):
            local = download_image(item['image_url'], item['id'])
            if local: item['local_image'] = local
        
        all_items.append(item)
        print(f"  ✓ {title[:70]}")

    # ── SOURCE 2: ANTHROPIC RESEARCH (fix #8) ──
    print("📡 Anthropic Research...")
    rpage = fetch('https://www.anthropic.com/research')
    rslugs = set(re.findall(r'href="(/research/[^"]+)"', rpage))
    rslugs = {s for s in rslugs if '/team/' not in s and not s.endswith('/') and len(s) > 12}

    for slug in sorted(rslugs)[:20]:
        article = fetch(f'https://www.anthropic.com{slug}', timeout=12)
        if not article: continue
        
        title, desc, img_url = extract_og(article)
        if not title or len(title) < 5: continue
        title = re.sub(r'\s*\|\s*Anthropic\s*$', '', title).strip()
        
        dt_match = re.search(r'(\w+ \d{1,2}, \d{4})', article)
        iso_date = normalize_date(dt_match.group(1)) if dt_match else ''
        
        item_id = hashlib.md5(f'anthro-research:{slug}'.encode()).hexdigest()[:12]
        if item_id in state['seen_ids']:
            continue
        
        item = {
            'id': item_id,
            'title': title[:200],
            'description': desc,
            'image_url': img_url if not is_bad_image(img_url) else '',
            'date': iso_date,
            'url': f'https://www.anthropic.com{slug}',
            'source': 'Anthropic',
            'via': None,
            'category': 'Research',
        }
        if item['image_url']:
            local = download_image(item['image_url'], item['id'])
            if local: item['local_image'] = local
        all_items.append(item)
        print(f"  ✓ {title[:70]}")

    # ── SOURCE 3: THE DECODER (Anthropic + OpenAI tags) ──
    for tag in ['anthropic', 'openai']:
        print(f"📡 The Decoder /{tag}/...")
        # Fetch first 3 pages
        all_links = set()
        for pn in ['', '/page/2/', '/page/3/']:
            dp = fetch(f'https://the-decoder.com/tag/{tag}/{pn}', timeout=15)
            if not dp: continue
            links = set(re.findall(r'<a\s+href="(https://the-decoder\.com/[^"]+)"[^>]*>', dp))
            all_links |= links
        
        all_links = {l for l in all_links if '/tag/' not in l and '/author/' not in l and '/page/' not in l}
        
        for link in sorted(all_links)[:20]:
            article = fetch(link, timeout=12)
            if not article: continue
            
            title, desc, img_url = extract_og(article)
            if not title or len(title) < 5: continue
            if is_junk(title, link): continue
            
            date_match = re.search(r'<meta\s+property="article:published_time"\s+content="([^"]+)"', article)
            iso_date = normalize_date(date_match.group(1)) if date_match else ''
            
            item_id = hashlib.md5(f'decoder:{link}'.encode()).hexdigest()[:12]
            if item_id in state['seen_ids']:
                continue
            
            # Determine actual source company
            source, via = 'General', 'The Decoder'
            tl = title.lower()
            if 'openai' in tl or 'chatgpt' in tl or 'gpt-' in tl:
                source, via = 'OpenAI', 'The Decoder'
            elif 'anthropic' in tl or 'claude' in tl:
                source, via = 'Anthropic', 'The Decoder'
            
            item = {
                'id': item_id,
                'title': title[:200],
                'description': desc,
                'image_url': img_url,
                'date': iso_date,
                'url': link,
                'source': source,
                'via': via,
                'category': 'News',
            }
            if item['image_url']:
                local = download_image(item['image_url'], item['id'])
                if local: item['local_image'] = local
            all_items.append(item)
            print(f"  ✓ [{source}] {title[:70]}")

    # ── SOURCE 4: HN ALGOLIA (OpenAI URLs) ──
    print("📡 HN Algolia — OpenAI...")
    for pg in [0, 1]:
        r = subprocess.run(['curl', '-sL',
            f'https://hn.algolia.com/api/v1/search_by_date?query=openai&tags=story&hitsPerPage=15&page={pg}',
            '-m', '10'], capture_output=True, text=True, timeout=15)
        try:
            hits = json.loads(r.stdout).get('hits', [])
        except:
            continue
        
        for h in hits:
            url = h.get('url', '')
            title = clean_text(h.get('title', ''))
            if not url or not title: continue
            if 'openai.com' not in url: continue
            if is_junk(title, url): continue
            
            item_id = hashlib.md5(f'hn:{url}'.encode()).hexdigest()[:12]
            if item_id in state['seen_ids']:
                continue
            
            # HN has no description — try to fetch the page, fallback to title
            desc = ''
            # Attempt page fetch (may be Cloudflare-blocked)
            pg_content = fetch(url, timeout=8)
            if pg_content and 'cloudflare' not in pg_content.lower():
                _, desc, og_img = extract_og(pg_content)
            
            iso_date = normalize_date(h.get('created_at', ''))
            
            item = {
                'id': item_id,
                'title': title[:200],
                'description': desc or f'OpenAI announcement: {title}',
                'image_url': '',
                'date': iso_date,
                'url': url,
                'source': 'OpenAI',
                'via': 'Hacker News',
                'category': 'Announcement',
            }
            all_items.append(item)
            print(f"  ✓ [OpenAI] {title[:70]}")

    # ── DEDUPLICATE (fix #6 — fuzzy matching) ──
    seen_fingerprints = {}  # fingerprint -> item
    unique = []
    for item in all_items:
        # Create fingerprint from key content words
        words = set(re.findall(r'[a-z]{4,}', item['title'].lower()))
        # Also include key words from description
        if item.get('description'):
            desc_words = set(re.findall(r'[a-z]{4,}', item['description'].lower()))
            words |= desc_words
        
        # Sort and take top distinctive words as fingerprint
        common = {'this', 'that', 'with', 'from', 'their', 'they', 'have', 'been', 'more', 'than', 'will', 'into', 'over', 'also'}
        distinctive = sorted(words - common)[:12]
        fingerprint = ' '.join(distinctive)
        
        # Check for near-duplicate (70%+ word overlap)
        is_dup = False
        for fp, existing in seen_fingerprints.items():
            fp_words = set(fp.split())
            overlap = len(distinctive) and len(fp_words & set(distinctive)) / max(len(distinctive), len(fp_words))
            if overlap > 0.6:
                is_dup = True
                # Keep the one with the better description
                if len(item.get('description', '')) > len(existing.get('description', '')):
                    seen_fingerprints[fingerprint] = item
                break
        
        if not is_dup:
            seen_fingerprints[fingerprint] = item
            unique.append(item)

    # ── SORT by date (all ISO now) ──
    unique.sort(key=lambda x: x.get('date', '0000-00-00'), reverse=True)

    # ── FILTER out old items (older than 90 days) ──
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    fresh = [i for i in unique if i.get('date', '') >= cutoff]

    # ── SAVE ──
    anthro_count = sum(1 for i in fresh if i['source'] == 'Anthropic')
    openai_count = sum(1 for i in fresh if i['source'] == 'OpenAI')

    output = {
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'total_items': len(fresh),
        'anthro_count': anthro_count,
        'openai_count': openai_count,
        'items': fresh,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    # Save state for next run
    save_state([item['id'] for item in fresh])

    n_img = len([f for f in os.listdir(IMG_DIR) if os.path.getsize(os.path.join(IMG_DIR, f)) > 2000])
    print(f'\n{"="*60}')
    print(f'✅ v2.0: {len(fresh)} items (Anthropic: {anthro_count}, OpenAI: {openai_count})')
    print(f'   📷 {n_img} images | {OUTPUT_FILE}')
    print(f'   Next run will only fetch new items (delta mode)')
    print(f'{"="*60}')

if __name__ == '__main__':
    main()
