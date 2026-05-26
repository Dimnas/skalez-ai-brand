#!/usr/bin/env python3
"""skalez.ai carousel renderer — moviepy + PIL + figlet.

Usage:
    python render_carousel.py plans/design_plan.json exports/2024-01-15/

Design plan JSON:
    {
      "story": "Claude 4 SWE-bench",
      "video_asset": "cosmos_1190871104.mp4",
      "slides": [
        {"n": 1, "type": "cover", "heading": "CLAUDE", "sub": "SWE-BENCH KING"},
        {"n": 2, "type": "text", "heading": "72.5%", "sub": "BEST EVER"},
        {"n": 3, "type": "media", "img": "path/to/screenshot.png"},
        {"n": 4, "type": "cta", "cmd": "follow @skalez.ai"}
      ]
    }
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Monkey-patch for moviepy compatibility with Pillow >= 10
if not hasattr(Image, 'ANTIALIAS'):
    Image.ANTIALIAS = Image.LANCZOS

import numpy as np
from moviepy.editor import (
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
)

# ── Config ──
WIDTH, HEIGHT = 1080, 1350
FPS = 30
DURATION = 4.0  # seconds per slide
FONT_DIR = Path(__file__).parent.parent / "assets" / "fonts"
VIDEO_DIR = Path(__file__).parent.parent / "assets" / "pinterest-visuals"

# Colors
BG = "#000000"
FG = "#FFFFFF"
DIM = "#888888"
ACCENT = "#33FF00"
AMBER = "#FFB627"


def figlet(text: str, font_file: str = "ANSI_Shadow.flf") -> str:
    """Run figlet with a local font file, return ASCII art."""
    font_path = FONT_DIR / font_file
    if not font_path.exists():
        font_path = Path("/tmp") / font_file
    env = os.environ.copy()
    env["FIGLET_FONTDIR"] = str(font_path.parent)
    result = subprocess.run(
        ["figlet", "-f", font_path.name, text],
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout


def render_heading_image(text: str, font_file: str = "ANSI_Shadow.flf", font_size: int = 24) -> Image.Image:
    """Render figlet ASCII art as a PIL image with transparency."""
    ascii_art = figlet(text, font_file)
    lines = ascii_art.rstrip("\n").split("\n")

    # Find monospace font
    font = None
    for font_name in ["Courier", "Menlo", "Monaco", "DejaVu Sans Mono", "Liberation Mono"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except:
            continue
    if font is None:
        font = ImageFont.load_default()

    # Measure
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    max_width = 0
    total_height = 0
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        max_width = max(max_width, w)
        line_heights.append(h)
        total_height += h

    # Padding
    pad_x, pad_y = 40, 40
    img_w = max_width + pad_x * 2
    img_h = total_height + pad_y * 2

    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    y = pad_y
    for i, line in enumerate(lines):
        draw.text((pad_x, y), line, fill=FG, font=font)
        y += line_heights[i]

    return img


def pil_to_array(img: Image.Image) -> np.ndarray:
    """Convert PIL Image to numpy array for moviepy."""
    return np.array(img)


def make_slide(slide_cfg: dict, video_path: Path) -> CompositeVideoClip:
    """Build one slide: video background + heading + optional media."""
    # Load video background
    video = VideoFileClip(str(video_path), audio=False)
    video = video.resize(newsize=(WIDTH, HEIGHT))
    # Trim or loop to DURATION
    if video.duration < DURATION:
        video = video.loop(duration=DURATION)
    else:
        video = video.subclip(0, DURATION)

    layers = [video]

    ctype = slide_cfg.get("type", "text")

    # ── Cover / Text / CTA: heading overlay ──
    if ctype in ("cover", "text", "cta"):
        heading = slide_cfg.get("heading", "")
        sub = slide_cfg.get("sub", "")
        cmd = slide_cfg.get("cmd", "")

        # Main heading
        if heading:
            img = render_heading_image(heading, "ANSI_Shadow.flf", font_size=28)
            head_clip = ImageClip(pil_to_array(img)).set_duration(DURATION)
            head_clip = head_clip.set_position((60, 100))
            layers.append(head_clip)

        # Sub heading
        if sub:
            sub_img = render_heading_image(sub, "ANSI_Compact.flf", font_size=18)
            sub_clip = ImageClip(pil_to_array(sub_img)).set_duration(DURATION)
            y_offset = 100 + img.height + 40 if heading else 100
            sub_clip = sub_clip.set_position((60, y_offset))
            layers.append(sub_clip)

        # CTA command
        if cmd:
            cta_img = render_heading_image(cmd, "ANSI_Shadow.flf", font_size=20)
            cta_clip = ImageClip(pil_to_array(cta_img)).set_duration(DURATION)
            cta_clip = cta_clip.set_position((60, HEIGHT - 300))
            layers.append(cta_clip)

    # ── Media slide: image centered ──
    if ctype == "media":
        img_path = slide_cfg.get("img")
        if img_path:
            if img_path.startswith("http"):
                import urllib.request
                tmp = "/tmp/skalez_media.jpg"
                urllib.request.urlretrieve(img_path, tmp)
                img_path = tmp
            if Path(img_path).exists():
                media = ImageClip(img_path).set_duration(DURATION)
                media = media.resize(height=HEIGHT * 0.5)
                if media.w > WIDTH * 0.8:
                    media = media.resize(width=WIDTH * 0.8)
                media = media.set_position("center")
                layers.append(media)

        caption = slide_cfg.get("caption", "")
        if caption:
            cap_img = Image.new("RGBA", (WIDTH, 60), (0, 0, 0, 0))
            cap_draw = ImageDraw.Draw(cap_img)
            try:
                cap_font = ImageFont.truetype("Courier", 20)
            except:
                cap_font = ImageFont.load_default()
            cap_draw.text((60, 10), caption, fill=DIM, font=cap_font)
            cap_clip = ImageClip(pil_to_array(cap_img)).set_duration(DURATION)
            cap_clip = cap_clip.set_position((0, HEIGHT - 200))
            layers.append(cap_clip)

    return CompositeVideoClip(layers, size=(WIDTH, HEIGHT)).set_duration(DURATION)


def render_carousel(plan_path: str, output_dir: str):
    """Render full carousel from design plan."""
    with open(plan_path) as f:
        plan = json.load(f)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    video_asset = plan.get("video_asset", "")
    video_path = VIDEO_DIR / video_asset if video_asset else None
    if video_path and not video_path.exists():
        video_path = None

    # If no video asset specified, use first available
    if video_path is None:
        available = list(VIDEO_DIR.glob("*.mp4"))
        if available:
            video_path = available[0]
        else:
            raise RuntimeError("No video assets found in " + str(VIDEO_DIR))

    print(f"Using video: {video_path.name}")

    slides = plan.get("slides", [])

    for slide in slides:
        n = slide["n"]
        print(f"Rendering slide {n:02d}...")
        clip = make_slide(slide, video_path)
        clip = clip.fadein(0.3).fadeout(0.3)

        out_path = out / f"slide_{n:02d}.mp4"
        clip.write_videofile(
            str(out_path),
            fps=FPS,
            codec="libx264",
            audio=False,
            threads=4,
            preset="fast",
            logger=None,
        )
        print(f"  Saved: {out_path}")
        clip.close()

    print(f"\nDone. {len(slides)} slides in {out}/")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python render_carousel.py plans/design_plan.json [output_dir]")
        sys.exit(1)

    plan = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "./output"
    render_carousel(plan, out)
