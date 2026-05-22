#!/usr/bin/env python3
"""Render skalez.ai v11 hyper-minimal carousel."""

import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

try:
    import pyfiglet
except ImportError:
    pyfiglet = None

WIDTH, HEIGHT = 1080, 1350
TEMPLATE = Path(__file__).parent.parent / "templates" / "v11-minimal.html"


def ansi(text):
    if not pyfiglet:
        return text.upper()
    return pyfiglet.figlet_format(text, font='ansi_shadow')


def make_cover(cfg):
    parts = []
    if cfg.get("prompt"):
        parts.append(f'\n  \u003cdiv class="prompt"\u003e{cfg["prompt"]}\u003c/div\u003e')
    if cfg.get("ascii_header"):
        parts.append(f'\n  \u003cpre class="ansi"\u003e{ansi(cfg["ascii_header"])}\u003c/pre\u003e')
    parts.append(f'\n  \u003ch1\u003e{cfg.get("headline","")}\u003c/h1\u003e')
    if cfg.get("sub"):
        parts.append(f'\n  \u003cp\u003e{cfg["sub"]}\u003c/p\u003e')
    return f'\u003cdiv class="slide-cover"\u003e{ "".join(parts) }\n\u003c/div\u003e'


def make_media(cfg):
    img = cfg.get("img", "")
    tag = cfg.get("tag", "")
    headline = cfg.get("headline", "")
    body = cfg.get("body", "")
    lines = [
        '\u003cdiv class="slide-media"\u003e',
        f'  \u003cdiv class="media"\u003e\u003cimg src="{img}" alt=""\u003e\u003c/div\u003e',
    ]
    if tag:
        lines.append(f'  \u003cspan class="tag"\u003e{tag}\u003c/span\u003e')
    if headline:
        lines.append(f'  \u003ch2\u003e{headline}\u003c/h2\u003e')
    if body:
        lines.append(f'  \u003cp\u003e{body}\u003c/p\u003e')
    lines.append('\u003c/div\u003e')
    return '\n'.join(lines)


def make_text(cfg):
    prompt = cfg.get("prompt", "")
    headline = cfg.get("headline", "")
    body = cfg.get("body", "")
    lines = ['\u003cdiv class="slide-text"\u003e']
    if prompt:
        lines.append(f'  \u003cdiv class="prompt"\u003e{prompt}\u003c/div\u003e')
    if headline:
        lines.append(f'  \u003ch1\u003e{headline}\u003c/h1\u003e')
    if body:
        lines.append(f'  \u003cp\u003e{body}\u003c/p\u003e')
    lines.append('\u003c/div\u003e')
    return '\n'.join(lines)


def make_cta(cfg):
    prompt = cfg.get("prompt", "")
    cmd = cfg.get("cmd", "follow @skalez.ai")
    sub = cfg.get("sub", "")
    lines = ['\u003cdiv class="slide-cta"\u003e']
    if prompt:
        lines.append(f'  \u003cdiv class="prompt"\u003e{prompt}\u003c/div\u003e')
    lines.append(f'  \u003cdiv class="cmd"\u003e{cmd}\u003cspan class="cursor"\u003e\u003c/span\u003e\u003c/div\u003e')
    if sub:
        lines.append(f'  \u003cp style="margin-top:24px"\u003e{sub}\u003c/p\u003e')
    lines.append('\u003c/div\u003e')
    return '\n'.join(lines)


def render(slides, out_dir="./output"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = TEMPLATE.read_text()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

        builders = {"cover": make_cover, "media": make_media, "text": make_text, "cta": make_cta}

        for s in slides:
            html = base.replace("<!-- SLIDE_CONTENT --\u003e", builders[s.get("type","text")](s))
            page.set_content(html)
            page.wait_for_timeout(500)

            img = page.locator(".media img")
            if img.count() > 0:
                img.evaluate("el => { if(el.complete) return true; return new Promise(r => el.onload = r); }")
                page.wait_for_timeout(300)

            fn = f"slide_{s['n']:02d}.png"
            page.screenshot(path=str(out / fn), full_page=False)
            print(f"Rendered {fn}")

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render_v11.py slides.json [output_dir]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        slides = json.load(f)

    render(slides, sys.argv[2] if len(sys.argv) > 2 else "./output")
