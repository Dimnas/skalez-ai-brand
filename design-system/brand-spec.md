# skalez.ai — Brand Specification

## Identity
- **Name:** skalez.ai
- **Tagline:** _[TBD — terminal prompt style, e.g. `> skalez.ai --daily-hype`]_
- **Category:** AI News / Daily AI Hype
- **Audience:** Developers, builders, tech-curious, AI enthusiasts, students
- **Platforms:** Instagram (primary, daily carousels)
- **Cadence:** 1 carousel per day, 8 slides per carousel

## Core Philosophy
> "A post from your Terminal."  
skalez.ai looks like a screenshot taken straight from a macOS Terminal window — simplified, clean, structured, no decoration. The only color comes from the media assets (product screenshots, editorial illustrations). Everything else is pure typography on black.

## Visual Direction

### Color System (Grayscale + Terminal Accent)
| Token | Hex | Usage |
|-------|------|-------|
| `TERM_BG` | `#0A0A0F` | Primary background — warm black, not pure black |
| `TERM_BLACK` | `#000000` | Deepest black for contrast moments |
| `TERM_WHITE` | `#F5F5F0` | Primary text — warm white, not pure white |
| `TERM_GRAY` | `#6B6B70` | Secondary text, muted elements |
| `TERM_DARK` | `#141418` | Card/container backgrounds |
| `TERM_BORDER` | `#2A2A2E` | Subtle 1px borders, dividers |
| `TERM_CURSOR` | `#33FF00` | Terminal green highlight — use extremely sparingly |
| `TERM_ACCENT` | `#FFB627` | Amber for urgent tags/CTAs only |

**Rule:** The UI is B&W. Media assets (images, videos inside slides) are shown in full color. This creates maximum impact — the product/news asset pops against the austere terminal frame.

### Typography
| Role | Font | Weight | Size | Notes |
|------|------|--------|------|-------|
| **Headline** | SF Mono / IBM Plex Mono | 700 | 48–64px | All terminal-style. Sentence case. |
| **Subheadline** | SF Mono / IBM Plex Mono | 500 | 24–32px | `
# ` style, like a comment line |
| **Body** | SF Mono / IBM Plex Mono | 400 | 16–20px | `>` prompt prefix optional |
| **Caption/Tag** | SF Mono / IBM Plex Mono | 600 | 12–14px | Uppercase for flags: [NEW], [DROP], [BREAKING] |
| **Watermark** | SF Mono / IBM Plex Mono | 400 | 11px | `skalez.ai@instagram ~ %` style |

**Font stack:**
```css
font-family: 'SF Mono', 'IBM Plex Mono', 'Menlo', 'Monaco', 'Courier New', monospace;
```

### Layout
**Term Window Frame (permanent chrome on every slide):**
```
┌─ skalez.ai ─────────────────────────────┐
│                                         │
│     [Content area — image OR image+text]│
│                                         │
└─ skalez.ai@instagram ~ % ───────────────┘
```

- **2px border** around the entire canvas, `#2A2A2E`
- **Title bar** at top: left-aligned `skalez.ai`, right-aligned `[NEW]` or date
- **Status bar** at bottom: `skalez.ai@instagram ~ %` + timestamp
- **Content area** is pure dark, no gradients, no shadows

**Slide types:**
1. **Cover** — Terminal window with ASCII art logo, title, date
2. **Fact/Headline** — Single headline, centered or left-aligned, `
# ` or `>` prefixed
3. **Media+Text** — Terminal split-pane: left = image in an inset frame, right = text. Or full-width image with caption below.
4. **CTA** — Terminal command prompt: `> follow @skalez.ai for daily AI drops`

### Voice & Tone
- **Precise:** Every word matters. No filler.
- **Hype:** FOMO is the engine. Bold claims. Numbers. Urgency.
- **Terminal-native:** Speak like a CLI. Use flags, commands, status codes.
- **Anti-corporate:** No "we're excited to announce." No emojis. No gradients.

**Patterns that work:**
- `Claude 4 just dropped.`
- `Nobody's talking about this.`
- `Save this. You'll need it tomorrow.`
- `> run hype_engine --source=anthropic --today`

**Forbidden words:** (same as AI DAILY)
- unleash, revolutionary, groundbreaking, cutting-edge
- embark, harness, synergy, paradigm
- empower, transform (unless in context of actual code transformation)

## Asset Protocol
Adopted from huashu-design:
1. **Research:** Find 5+ AI news sources per day (Anthropic, OpenAI, Google AI, Perplexity, X)
2. **Curate:** Pick 1–3 stories worth posting
3. **Gather assets:** Find highest-quality media (screenshots, product images, editorial art)
4. **Score:** 8/10 threshold. Image must stand alone without text.
5. **Composite:** Drop into terminal frame. B&W chrome + color asset.
6. **Export:** 1080×1350 PNG, 8 slides, ready for Instagram

## Animation (Stories/Reels only — not carousels)
- Cursor blink: `|` or `_` blinking at 1Hz on CTA slides
- Typewriter effect: text appears character-by-character (optional, for Reels)
- CRT scanline: extremely subtle horizontal lines at 4% opacity (optional)

## Tooling
- **Design:** Figma (connected), huashu-design skill for refinement
- **Render:** HTML template → Playwright → PNG (existing pipeline)
- **Asset source:** Anthropic CDN, OpenAI media kit, product landing pages
- **Delivery:** Instagram upload (manual for now, maybe Composio later)

## File Structure
```
ai-daily-brand/
├── design-system/
│   ├── brand-spec.md          ← this file
│   ├── tokens.json            ← color/typo tokens for scripts
│   └── components/            ← reusable HTML/CSS components
├── assets/
│   ├── logos/
│   ├── icons/
│   └── textures/
├── templates/
│   ├── terminal-carousel.html ← master render template
│   └── story-overlay.html     ← for future Reels
├── scripts/
│   ├── fetch-news.py          ← daily research scraper
│   ├── render-carousel.py     ← HTML → PNG pipeline
│   └── upload-instagram.py    ← future: automated posting
├── exports/
│   └── YYYY-MM-DD/            ← daily drop folders
└── references/
    └── huashu-design/         ← copied framework
```

## Ship Checklist
- [ ] Terminal carousel template v1 renders clean
- [ ] 1 test carousel exported (8 slides)
- [ ] Figma file created with brand kit
- [ ] huashu-design skill installed
- [ ] Daily content pipeline defined
- [ ] skalez.ai Instagram account created / rebranded from AI DAILY
- [ ] GitHub repo shipped: github.com/Dimnas/skalez-ai-brand
