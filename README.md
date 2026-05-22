# skalez.ai — Terminal-Native AI News

Instagram carousel system for daily AI news. Terminal aesthetic. B&W chrome. ANSI Shadow headers. Pure type.

## Structure
- `design-system/` — Brand spec, color tokens, typography
- `templates/` — HTML render templates (v11 minimal, v12 pixel)
- `scripts/` — Playwright render pipeline
- `test_posts/` — Draft carousel JSONs
- `.agents/context/` — Impeccable design context

## Usage
```bash
# v11 — ultra-minimal IBM Plex Mono + ANSI Shadow
python3 scripts/render_v11.py test_posts/draft.json output/

# v12 — pixel-native "Press Start 2P" + blocky cursor
python3 scripts/render_v12.py test_posts/v12_test.json output/
```

## Brand
- **Name:** skalez.ai
- **Aesthetic:** macOS Terminal, simplified (Claude-style)
- **Colors:** #0A0A0F bg, #F5F5F0 text, #33FF00 accent, #FFB627 tags
- **Fonts:** IBM Plex Mono (v11), Press Start 2P (v12)
- **Headers:** ANSI Shadow ASCII art via pyfiglet (v11), pixel-native (v12)
- **Motion:** Blinking cursor on CTA only

## Status
WIP — iterating with Impeccable design framework.
