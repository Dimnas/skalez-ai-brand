# DESIGN.md — skalez.ai Carousel System

## Color (Grayscale)

| Token | Hex | Usage |
|-------|------|-------|
| `bg` | `#0A0A0F` | Canvas background |
| `fg` | `#F5F5F0` | Primary text |
| `dim` | `#6B6B70` | Secondary/muted text |
| `accent` | `#33FF00` | Terminal green — prompts, highlights |
| `amber` | `#FFB627` | Tags, urgent labels |
| `border` | `#2A2A2E` | 1px borders for media frames |

**Rule:** No color in chrome. Only media assets carry color.

## Typography

| Role | Font | Weight | Size | Line |
|------|------|--------|------|------|
| ANSI header | IBM Plex Mono | 400 | 8px | 1.0 |
| Headline | IBM Plex Mono | 700 | 64px | 1.05 |
| Subheadline | IBM Plex Mono | 500 | 32px | 1.15 |
| Body | IBM Plex Mono | 400 | 18px | 1.55 |
| Prompt | IBM Plex Mono | 400 | 14px | 1.0 |
| Tag | IBM Plex Mono | 600 | 12px | 1.0 |

## Spacing
- Canvas padding: `100px 80px 80px` (top sides bottom)
- Headline to body gap: `20px`
- Body to next element: `48px`
- Media frame to caption: `40px`

## Components

### Prompt Line
```
$ skalez.ai --daily
```
- `$` in terminal green (`accent`)
- Rest in dim
- 14px, appears at top of text slides

### ANSI Header
```
███████╗██╗  ██╗...
```
- 8px font-size, line-height 1.0
- White on black
- For brand name, section titles

### Headline
- 64px, weight 700, sentence case
- Tight line-height (1.05)
- Max 3 lines

### Media Frame
- 920×640px, 1px border (`border`)
- Image inside: `object-fit: contain`
- No border-radius, no shadow

### Tag
- Uppercase, letter-spacing 0.15em
- Amber color, 12px
- Format: `[DROP]`, `[BREAKING]`, `[NEW]`

### CTA
```
> follow @skalez.ai |
```
- `>` in amber
- Command in terminal green
- Blinking cursor (`|` block, 1Hz)

## Motion
- Cursor blink: 1s steps(1) infinite
- No other animation on carousels
- For Reels: typewriter effect optional

## Layout Rules
- No window chrome (no title bar, no status bar, no rounded corners)
- No centering except media frames
- Left-align all text
- Let the black space breathe
