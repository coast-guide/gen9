# gen9-design

The Gen9 design system: one source for tokens, type, the logo and brand assets, used by every Gen9 surface. Today that means gen9-ui and the Keycloak login and email theme.

It is not a Docker stack. Apps receive committed copies through `make design-sync` (see [Sync](#sync)), so every stack still builds on its own.

## Idea

The interface is drawn like a drafting sheet, with the two colors of an illuminated page, ultramarine and gold leaf:

- **Ink and hairlines** give structure: near-black text, 1 px borders, quiet surfaces.
- **Lapis** (ultramarine) marks interaction: links, focus rings, selection.
- **Gold** (leaf) is for small points only: the 9 on the app icon, and the dot beside work that is under way or waiting for you. It never carries text.
- **Primary actions are ink pills**, the quiet confidence of Apple and OpenAI product UIs rather than colored buttons.

## Foundations

| | |
| --- | --- |
| Components | [shadcn/ui](https://ui.shadcn.com) (style *Maia*) on [Base UI](https://base-ui.com), the shadcn default since July 2026, with accessible primitives. Code is copied into apps, not installed as a black box |
| Styling | Tailwind CSS 4; semantic CSS variables in OKLCH (`theme.css`) with shadcn's names (`--background`, `--primary`, …), plus `--brand`, `--link`, `--leaf`, `--success` |
| Type | [Instrument Sans](https://fonts.google.com/specimen/Instrument+Sans) (OFL, variable weight and width), self-hosted: no third-party font requests. Mono is the system monospace, for code only |
| Icons | Hugeicons (shadcn's icon set for this style), 1.8 px stroke |
| Themes | Light and dark, following the system by default; users can pick one in gen9-ui |

### Color

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--background` | `#F6F7F9` canvas | `#0D0F16` | Page |
| `--card` | `#FFFFFF` | `#151824` | Sheets, cards |
| `--foreground` | `#151722` ink | `#EEF0F6` | Text |
| `--muted-foreground` | `#5A5F70` graphite | `#9AA0B4` | Secondary text |
| `--border` | `#E3E5EB` hairline | `#262A38` | Dividers, outlines |
| `--primary` | ink | light ink | Primary buttons |
| `--brand` / `--link` / `--ring` | `#3446E0` lapis | `#8C98FF` (link, ring) | Interaction |
| `--leaf` | `#C8962E` | `#DDA94A` | Small points only: the 9 on the app icon, a dot beside work under way |

Measured text contrast (WCAG 2.2 AA needs 4.5:1):

| Pair | Ratio |
| --- | --- |
| Ink on canvas | 16.6:1 |
| Graphite on canvas | 5.9:1 |
| Lapis on white | 6.8:1 |
| Dark graphite | 7.4:1 |
| Dark lapis | 7.3:1 |

Leaf is 2.7:1, which is why it is decorative only.

### Shape and type

- **Radius by hierarchy:** controls 12 px (`--radius`), cards 20 px, sheets 28 px, buttons pill.
- **Type scale:** 16 px body. Fluid `text-display`, `text-headline` and `text-title` grow from phone to desktop with `clamp()`, with tighter tracking at larger sizes.
- **Elevation:** only for overlays and the landing specimen. No decorative gradients.
- **Long words:** a word longer than its line (a URL, an error, a skill's description) breaks
  rather than running off a phone's screen at large text (WCAG 1.4.4, 1.4.10). Only where such
  words appear (Tailwind's `break-words`: answers, steps, Settings, plugin rows), not on the whole
  page, which split the recovery codes' numbers in two.

## Mobile first

Every rule starts at phone width and adds space as the screen grows (Tailwind `sm:`/`lg:` are min-width). The base layer in `theme.css` enforces:

| Rule | Why |
| --- | --- |
| 44×44 px targets on touch screens (`pointer-coarse:` sizes, `touch-target` utility) | [WCAG 2.2 SC 2.5.5](https://www.w3.org/WAI/WCAG22/Understanding/target-size-enhanced) (AA minimum 24 px, SC 2.5.8); Apple HIG |
| Form text at least 16 px on touch screens | iOS Safari zooms into inputs smaller than 16 px |
| `viewport-fit=cover` + `pt-safe`/`pb-safe`/`px-safe` utilities | Content clears notches and the home indicator |
| `min-h-dvh`, never `100vh` | Mobile browser toolbars change the viewport height |
| Primary actions in the thumb zone | Landing page actions and the chat composer are pinned to the bottom on phones |
| `prefers-reduced-motion` | All animation and transition collapse to ~0 |
| `:focus-visible` ring in lapis | Visible keyboard focus everywhere |

## Brand

| File | Use |
| --- | --- |
| `brand/mark.svg`, `brand/mark-on-dark.svg` | The mark: the 9 in square brackets, the slot a team fills with its own work. Ink brackets, a lapis 9. Compact spaces, avatars for the agent |
| `brand/wordmark.svg`, `brand/wordmark-on-dark.svg` | The mark, then `gen9` in Instrument Sans semibold outlines |
| `brand/app-icon.svg` | Lapis tile, white brackets, gold 9: favicon and app icon (works on light and dark tabs) |
| `brand/app-icon-maskable.svg`, `brand/png/*` | Full-bleed version and PNG sizes for iOS touch icon, PWA icons (192, 512, maskable) |
| `brand/favicon.ico` | 16/32/48 px |
| `react/logo.tsx` | `<Mark/>` and `<Wordmark/>` React components (ink follows `currentColor`, the 9 uses `--link`). `<Mark decorative/>` hides the mark from screen readers where the text beside it already names Gen9 |
| `brand/preview.html` | Every mark and wordmark at 16 to 128 px, on light and dark |

In an email the mark is type, not a drawing: `[9]` in bold with the 9 in lapis, then `gen9` (gen9-keycloak's `theme/src/email/html/template.ftl`). Gmail shows neither an inline SVG nor an embedded image.

Rebuild the assets from source, in a scratch folder: `scripts/wordmark.py` (the 9 and the word as outlines from the font, via fontTools and brotli), then `scripts/brand.py` (the SVGs; it prints the two outlines that `react/logo.tsx` carries), then `scripts/raster.py` (the PNGs and `favicon.ico`, via CairoSVG and Pillow).

## Sync

```bash
make design-sync     # copy tokens, font, logo and icons into the apps
make design-check    # fail if any copy differs from gen9-design (run before committing)
```

`sync.sh` lists every source → destination pair. Copies are committed because each stack builds on its own; never edit them by hand.

## Files

| Path | Purpose |
| --- | --- |
| `theme.css` | Tokens, Tailwind `@theme` mapping, base layer, mobile utilities |
| `fonts/` | Instrument Sans variable WOFF2 + OFL license |
| `brand/` | Logo, icons, preview |
| `react/logo.tsx` | Logo components |
| `scripts/` | Asset generators |
| `sync.sh` | Copies into apps; `--check` verifies |
