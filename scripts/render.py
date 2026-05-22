#!/usr/bin/env python3
"""Render skalez.ai terminal carousel slides from HTML template."""

import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

try:
    import pyfiglet
    PYFIGLET = True
except ImportError:
    PYFIGLET = False

# ── Config ──
WIDTH, HEIGHT = 1080, 1350
TEMPLATE = Path(__file__).parent.parent / "templates" / "terminal-carousel.html"


def ansi_shadow(text):
    """Generate ANSI Shadow ASCII art via pyfiglet."""
    if not PYFIGLET:
        return text.upper()
    return pyfiglet.figlet_format(text, font='ansi_shadow')


def make_cover(config):
    """Build HTML for cover slide."""
    parts = []
    if config.get("ascii"):
        parts.append(f'\n    \u003cpre class="ascii-logo">{config["ascii"]}</pre\u003e')
    else:
        parts.append(f'\n    \u003cdiv class="date">{config.get("date", "")}</div\u003e')
    if config.get("ascii_header"):
        parts.append(f'\n    \u003cpre class="ansi-header">{ansi_shadow(config["ascii_header"])}</pre\u003e')
    parts.append(f'\n    \u003ch1 class="headline">{config.get("headline", "")}</h1\u003e')
    if config.get("sub"):
        parts.append(f'\n    \u003cp class="sub">{config["sub"]}</p\u003e')
    inner = "".join(parts)
    return f'\n  \u003cdiv class="cover">{inner}\n  \u003c/div\u003e'


def make_media(config):
    """Build HTML for media + caption slide."""
    img = config.get("img", "")
    tag = config.get("tag", "NEWS")
    headline = config.get("headline", "")
    body = config.get("body", "")
    lines = [
        '\n  <div class="media-slide">',
        '    <div class="media-frame">',
        f'      <img src="{img}" alt="">',
        '    </div>',
        '    <div class="media-caption">',
        f'      <span class="tag">{tag}</span>',
        f'      <h2 class="headline">{headline}</h2>',
        f'      <p class="body">{body}</p>',
        '    </div>',
        '  </div>',
    ]
    return "".join(lines)


def make_text(config):
    """Build HTML for text-only slide."""
    prompt = config.get("prompt", "#")
    headline = config.get("headline", "")
    body = config.get("body", "")
    lines = [
        '\n  <div class="text-slide">',
        f'    <span class="prompt">{prompt}</span>',
        f'    <h1 class="headline">{headline}</h1>',
        f'    <p class="body">{body}</p>',
        '  </div>',
    ]
    return "".join(lines)


def make_cta(config):
    """Build HTML for CTA slide."""
    prompt = config.get("prompt", "")
    cmd = config.get("cmd", "follow @skalez.ai")
    sub = config.get("sub", "AI drops, no noise.")
    lines = [
        '\n  <div class="cta">',
        f'    <p class="prompt">{prompt}</p>',
        f'    <div class="cmd">{cmd}<span class="cursor"></span></div>',
        f'    <p class="sub">{sub}</p>',
        '  </div>',
    ]
    return "".join(lines)


def render_slide(slide, template_html, total_slides):
    """Insert slide content into template."""
    ctype = slide.get("type", "text")
    indicator = f"{slide['n']:02d} / {total_slides:02d}"
    date_str = slide.get("date", "")

    builders = {"cover": make_cover, "media": make_media, "text": make_text, "cta": make_cta}
    content = builders[ctype](slide)

    html = template_html
    html = html.replace("<!-- INDICATOR -->", indicator)
    html = html.replace("<!-- DATE -->", date_str)
    html = html.replace("<!-- CONTENT_INSERTED_HERE -->", content)

    return html


def render_carousel(slides_data, output_dir="./output"):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    base = TEMPLATE.read_text()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})

        for slide in slides_data:
            html = render_slide(slide, base, len(slides_data))
            page.set_content(html)
            page.wait_for_timeout(500)

            img = page.locator(".media-frame img")
            if img.count() > 0:
                img.evaluate("el => { if(el.complete) return true; return new Promise(r => el.onload = r); }")
                page.wait_for_timeout(300)

            fn = f"slide_{slide['n']:02d}.png"
            page.screenshot(path=str(out / fn), full_page=False)
            print(f"Rendered {fn}")

        browser.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render.py slides.json [output_dir]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        slides = json.load(f)

    output = sys.argv[2] if len(sys.argv) > 2 else "./output"
    render_carousel(slides, output)
