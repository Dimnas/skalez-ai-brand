#!/usr/bin/env python3
"""Render skalez.ai v12 pixel-native carousel."""

import json, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

WIDTH, HEIGHT = 1080, 1350
TEMPLATE = Path(__file__).parent.parent / "templates" / "v12-pixel.html"

# Pixel hand cursor SVG — blocky macOS-style pointer
HAND_CURSOR = """\u003csvg viewBox="0 0 32 44" xmlns="http://www.w3.org/2000/svg" class="hand-cursor"\u003e
  \u003crect x="10" y="0" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="0" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="4" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="4" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="4" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="4" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="8" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="8" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="8" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="8" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="8" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="2" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="26" y="12" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="2" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="26" y="16" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="2" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="26" y="20" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="2" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="26" y="24" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="28" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="28" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="28" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="28" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="22" y="28" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="6" y="32" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="32" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="32" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="18" y="32" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="10" y="36" width="4" height="4" fill="#FFFFFF"/>
  \u003crect x="14" y="36" width="4" height="4" fill="#FFFFFF"/>
\u003c/svg>"""


def make_cover(cfg):
    parts = [f'\n{HAND_CURSOR}']
    if cfg.get("label"):
        parts.append(f'\n  \u003cdiv class="label"\u003e{cfg["label"]}\u003c/div\u003e')
    if cfg.get("mega1"):
        parts.append(f'\n  \u003cdiv class="mega"\u003e{cfg["mega1"]}\u003c/div\u003e')
    if cfg.get("mega2"):
        parts.append(f'\n  \u003cdiv class="mega"\u003e{cfg["mega2"]}\u003c/div\u003e')
    if cfg.get("sub"):
        parts.append(f'\n  \u003cdiv class="sub"\u003e{cfg["sub"]}\u003c/div\u003e')
    return f'\u003cdiv class="slide-cover"\u003e{ "".join(parts) }\n\u003c/div\u003e'


def make_media(cfg):
    img = cfg.get("img", "")
    caption = cfg.get("caption", "")
    lines = [
        '\u003cdiv class="slide-media"\u003e',
        f'  \u003cdiv class="media"\u003e\u003cimg src="{img}" alt=""\u003e\u003c/div\u003e',
    ]
    if caption:
        lines.append(f'  \u003cdiv class="media-caption"\u003e{caption}\u003c/div\u003e')
    lines.append('\u003c/div\u003e')
    return '\n'.join(lines)


def make_text(cfg):
    label = cfg.get("label", "")
    mega1 = cfg.get("mega1", "")
    mega2 = cfg.get("mega2", "")
    sub = cfg.get("sub", "")
    lines = ['\u003cdiv class="slide-text"\u003e']
    if label:
        lines.append(f'  \u003cdiv class="label"\u003e{label}\u003c/div\u003e')
    if mega1:
        lines.append(f'  \u003cdiv class="mega"\u003e{mega1}\u003c/div\u003e')
    if mega2:
        lines.append(f'  \u003cdiv class="mega"\u003e{mega2}\u003c/div\u003e')
    if sub:
        lines.append(f'  \u003cdiv class="sub"\u003e{sub}\u003c/div\u003e')
    lines.append('\u003c/div\u003e')
    return '\n'.join(lines)


def make_cta(cfg):
    cmd = cfg.get("cmd", "follow @skalez.ai")
    lines = ['\u003cdiv class="slide-cover"\u003e']
    lines.append(f'\n  {HAND_CURSOR}')
    lines.append(f'\n  \u003cdiv class="cta"\u003e{cmd}\u003cspan class="cursor"\u003e\u003c/span\u003e\u003c/div\u003e')
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
        print("Usage: python render_v12.py slides.json [output_dir]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        slides = json.load(f)

    render(slides, sys.argv[2] if len(sys.argv) > 2 else "./output")
