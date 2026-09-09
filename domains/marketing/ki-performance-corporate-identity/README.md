# KI performance — Corporate Identity

The brand is **dark, mint-accented and typographically plain**. One background, one accent, one font. Most of the discipline is in what you *don't* add.

**Which brand to use.** KI performance is a **sub-brand**: use it when the material is explicitly KI performance, meaning delivery work, client decks for our engagements, or the offering. KI group is the **parent brand and the default** for corporate, cross-company and first-touch material (white-led, yellow accent, black wordmark, no tagline), and it is a different identity with different rules. The KI group parent-brand CI is not documented in this domain yet; ask before applying the rules below to corporate material.

**Where this actually lives.** The authoritative source is the internal `html-ci-ki-performance` build skill, which carries the full deck scaffold, template library and graphics set. This document is the **human-readable extract**: the durable brand rules, for anyone who needs to know what the brand is without building a deck. When the two disagree, the skill wins.

## Colors

| Role | Hex | Use for |
|---|---|---|
| **Background (dark)** | `#0C0F1A` | The standard background. Almost everything sits on this. |
| **Accent (mint green)** | `#28CD80` | Headline accents, highlights, bullets, charts, pills. |
| **Accent soft** | `#74E8AE` | Decoration only — never text. |
| **Text primary** | `#FFFFFF` | Body text on dark. |
| **Text muted** | `#9FA8C0` | Secondary text, captions, sub-headlines. |
| **Text muted light** | `#D7DEEE` | Tertiary text. |
| **Panel** | `#141826` | Subtle raised card or band. |
| **Hairline** | `#26304A` | Faint borders. |

**Mint is the only accent.** Don't invent a second hue — not for charts, not for highlights, not for a second data series. Where you need differentiation, use mint plus white plus the grays, or opacity.

The one scoped exception: a red/amber/green **status** traffic-light, for backlog or epic status only, always with a legend. Never for decoration, emphasis, bullets or charts.

## Typography

**Montserrat, for everything** — headings and body, one family. Never a second font, never a "creative" display face.

| Element | Size / weight |
|---|---|
| Hero title | 56px / 700 |
| Slide title | 40px / 700 |
| Eyebrow | 22px / 700, mint |
| Sub-title | 20px / 500, muted |
| Body and bullets | 20px / 400 |
| Caption, footnote, page number | 13px, muted |

Weights in use: 700 for titles and bold, 500–600 for sub-headers, 400 for body.

## Logo

The **white wordmark with the "A KI GROUP COMPANY" tagline** — the tagline is part of the mark, so don't add or remove it.

It appears on **every slide**: bottom-left on content slides, top-left and larger on title and closing slides. The source asset (`ki-performance-logo-white.svg`) lives with the internal `html-ci-ki-performance` build skill; this text-only domain does not carry the image file itself.

## Layout

- **16:9, on a 1280 × 720 coordinate system.** Don't change the slide size.
- **Content margin: 64px.** Logo bottom-left, page number bottom-right.
- **Usable content height is about 592px** — keep the body within ~550px so nothing collides with the logo and footer.
- **Header plus a multi-element body → top-align.** Only a single short hero element gets vertically centered; centering a header-plus-grid detaches them and reads as floating.
- **Footnotes anchor to the bottom** of the slide, not directly under the last line.

## Text length

Fixed-height slides mean overflow spills onto the footer. Condense first; if it still doesn't fit, split across two slides. **Never shrink below the type scale or change box geometry** — that breaks the CI.

| Element | Max |
|---|---|
| Hero title | ≤ 60 chars, ≤ 2 lines |
| Slide title | ≤ 70 chars |
| Eyebrow / sub-title | ≤ 40 chars |
| Bullet | ≤ 80 chars |
| Card body | ≤ ~120 chars |

## Visual style

- **Bullets are square and mint** — an 11×11px block, with white bullet text. This is the brand's signature list style. No round bullets, no other symbols.
- **Icons**: outline, white or mint, ~1.6 stroke, clean and technical. Inline SVG.
- **Charts**: dark background, primary series mint, secondary white or gray. CI colors only.
- **Images**: dark, teal, high-contrast, serious-tech, with the brand's green-tint overlay. No light, cheerful stock photography.
- **Backdrops**: rotate across a deck rather than stamping one everywhere. One decorative element per slide, behind the content.

## The do-not list

These are all rules that exist because something was built and rejected:

- **No colors outside the palette.** Dark background, mint, white, two grays, two structural tones. Period.
- **No light or white layouts "for variety."** The brand is dark.
- **No font other than Montserrat**, and no external font links — the font is embedded, and a link reintroduces both a network dependency and a GDPR touchpoint.
- **No accent line or underline beneath a title.** Explicitly forbidden.
- **No round bullets.**
- **No decorative stripes** that aren't in the reference material.
- **No flat bullet list when the content has structure.** Contrast, sequence, growth, cycle and system content each get a real layout; proof-in-numbers gets stat cards.
- **No same backdrop or corner accent on every slide.**
- **No thin or icon-like arrows between content blocks**, and no arrow touching or piercing a panel border — keep clear space.
- **No cycle drawn as four corner boxes with arrows**, and no loop glyph for a one-way handoff — loops mean genuinely repeating cycles.
- **Retired and not to be reused:** the wave-mesh backdrop and the dot-grid accent.
- **Nothing linked externally** in a final file — embed images, fonts and graphics so the file is one sendable artifact.

## Building something

For decks, use the internal `html-ci-ki-performance` skill rather than applying these rules by hand — it ships the scaffold with the palette, the embedded font, the logo and a template library already in place, so the CI is correct by construction.

Decks are built as **self-contained HTML** — single file, arrow-key navigation, prints to PDF. Not PowerPoint, unless someone explicitly asks for it.

## Still missing

The KI group parent-brand CI, logo files in other variants (dark background, monochrome, favicon, social), document and template basics (letterhead, Word/PowerPoint templates, email signature), and tone of voice for external communication are not yet on this layer.
