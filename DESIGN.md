---
name: sochub
description: Case management for security incidents, in the same world as sochub.io. Ink rail with a lime signal, lavender accent, stone paper by day and near-black by night.
colors:
  # Light theme (default)
  canvas: "#f3f0eb"
  surface: "#faf7f4"
  panel: "#ffffff"
  raised: "#f5f2ee"
  raised-2: "#ece8e6"
  line: "rgb(27 27 27 / 0.1)"
  line-strong: "rgb(27 27 27 / 0.22)"
  fg: "#1b1b1b"
  fg-2: "#3a3836"
  fg-3: "#5c5955"
  muted: "#68655e"
  accent: "#5546d6"
  accent-hover: "#4335b8"
  on-accent: "#ffffff"
  lime: "#5f7317"
  magenta: "#8a2be2"
  lavender: "#a4a8ff"
  signal: "#d6e37f"
  severity-critical: "#b42318"
  severity-high: "#b54708"
  severity-medium: "#8a6100"
  severity-low: "#1d63a3"
  severity-info: "#5c5955"
  rail: "#111113"
  rail-2: "#1f1f23"
  rail-ink: "#f2f2f0"
  rail-line: "rgb(255 255 255 / 0.09)"
  # Dark theme (prefers-color-scheme: dark, or html[data-theme=dark])
  canvas-dark: "#0a0a0a"
  surface-dark: "#0f0f10"
  panel-dark: "#111113"
  raised-dark: "#18181b"
  raised-2-dark: "#202024"
  line-dark: "rgb(255 255 255 / 0.09)"
  line-strong-dark: "rgb(255 255 255 / 0.18)"
  fg-dark: "#f2f2f0"
  fg-2-dark: "#cfcfd2"
  fg-3-dark: "#a3a3a8"
  muted-dark: "#8e8e95"
  accent-dark: "#a4a8ff"
  accent-hover-dark: "#c4c7ff"
  on-accent-dark: "#0a0a0a"
  lime-dark: "#d6e37f"
  magenta-dark: "#e08cff"
  rail-dark: "#000000"
  rail-2-dark: "#17171a"
  severity-critical-dark: "#ff5d6c"
  severity-high-dark: "#ffad4d"
  severity-medium-dark: "#ffd866"
  severity-low-dark: "#6cb8ff"
  severity-info-dark: "#a3a3a8"
typography:
  headline:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    letterSpacing: "-0.022em"
  title:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 600
  body:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    fontFeature: "\"rlig\" 1, \"calt\" 1, \"cv11\" 1, \"ss01\" 1"
  numeral:
    fontFamily: "Inter Variable, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 600
    lineHeight: 1
    fontFeature: "\"tnum\" 1"
  label:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "11px"
    fontWeight: 500
    letterSpacing: "0.1em"
  tag:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SF Mono, Menlo, monospace"
    fontSize: "10.5px"
    fontWeight: 600
    lineHeight: "16px"
    letterSpacing: "0.06em"
rounded:
  xs: "2px"
  sm: "3px"
  md: "4px"
  lg: "6px"
  xl: "8px"
spacing:
  gutter-mobile: "16px"
  gutter: "24px"
  card-pad: "16px"
  rail: "240px"
  rail-collapsed: "64px"
  topbar: "56px"
  content-max: "1600px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-ghost:
    textColor: "{colors.fg-2}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-danger:
    textColor: "{colors.severity-critical}"
    rounded: "{rounded.md}"
    padding: "0 14px"
    height: "36px"
  button-danger-hover:
    backgroundColor: "{colors.severity-critical}"
    textColor: "#ffffff"
  input:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "0 12px"
    height: "36px"
  card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.md}"
    padding: "{spacing.card-pad}"
  tag:
    typography: "{typography.tag}"
    rounded: "{rounded.sm}"
    padding: "1px 6px"
  filter-chip:
    textColor: "{colors.fg}"
    rounded: "{rounded.sm}"
    padding: "0 8px"
    height: "28px"
  rail:
    backgroundColor: "{colors.rail}"
    textColor: "{colors.rail-ink}"
    width: "{spacing.rail}"
  rail-item-active:
    backgroundColor: "{colors.rail-2}"
    textColor: "{colors.rail-ink}"
---

# Design System: sochub

## Overview

**Creative North Star: "The Signal Desk"**

The app is the same product as sochub.io. An ink-black rail carries the white hex mark in both themes, lavender-violet is the one accent, and lime is the signal: the bar beside the active page, and the colour of every good outcome. Since 2026-11-18 this world replaces the earlier case-file one entirely. None of its material survives (no manila, no rubber stamps, no folder tabs, no Archivo).

Light mode is editorial and technical: warm stone paper, white sheets with hairline edges, ink text, sharp 4px corners, mono caps for labels and tags, tabular figures for every number. Dark mode is the website's near-black with a faint violet and lime glow on the working ground only. Density is high but quiet: a responder under time pressure scans rows, not decoration.

The organising behaviour is drill-down. Every number on a dashboard is a link into the filtered list behind it, and the filter arrives as a removable chip. A widget that cannot be followed is unfinished.

**Key Characteristics:**
- Ink rail (240px, 64px collapsed) in both themes; active item = lifted row, lavender icon, 2px lime signal bar.
- Stone paper ground, white sheets, hairline lines; flat, no glow in light mode.
- One accent (lavender-violet), one signal (lime), one fixed five-step severity ramp.
- Inter for UI, JetBrains Mono for labels, tags, timestamps and code; tabular figures everywhere.
- Status and severity are tinted mono tags: tone-coloured text, 40% hairline, 9% fill.
- Every dashboard figure, bar, slice and cell drills into its filtered list.

## Colors

A warm neutral world with one cool accent, one acid signal, and a severity ramp that owns the warm hues.

### Primary
- **Signal Violet** (accent; lavender in dark): links, primary buttons, focus ring, active tab underline, the "new" status, filter chips. The only colour a person clicks toward.

### Secondary
- **Lime Signal** (lime for text, signal for fills): the active-nav bar on the rail, good deltas, resolved status, clean verdicts, TLP:GREEN. The darker lime is the text-safe shade on paper; the pale signal and lavender values are fills only (dots, bars, highlights), never text on paper.

### Tertiary
- **Severity Ramp** (critical red, high rust, medium ochre, low blue, info grey): severity, malicious and suspicious verdicts, TLP red and amber, SLA breach clocks, and the destructive button. Every view uses this same ramp in this same order.
- **Magenta** (magenta): inline code in copilot answers and a dark-mode glow hue. Rare.

### Neutral
- **Stone Paper** (canvas): the working ground behind every page, and the top bar.
- **Sheet White / Night Sheet** (panel): cards, tables, inputs, secondary buttons.
- **Raised Stone** (raised, raised-2): hover rows, skeletons, neutral tags.
- **Ink** (fg, fg-2, fg-3, muted): titles, body, secondary meta, placeholders.
- **Hairline** (line, line-strong): every border; line-strong for inputs and outlined buttons.
- **Rail Ink** (rail, rail-2, rail-ink, rail-line): the navigation field only; rail goes true black in dark so it still reads against the #111113 sheets.

### Named Rules
**The One Ramp Rule.** Severity owns red, rust, ochre and blue, in one fixed order (critical, high, medium, low, info), resolved through `tones.ts`. Nothing else borrows those hues.

**The Status Is Not Rank Rule.** Case status never uses the severity ramp: new is accent, open and in progress are ink, pending and closed are neutral, resolved is lime.

**The Fills-Only Rule.** Lavender and the pale signal lime are fills on paper, never text; text uses accent and lime, which hold AA on stone and white.

## Typography

**Display Font:** Inter Variable (with ui-sans-serif, system-ui)
**Body Font:** Inter Variable, with stylistic set ss01 and cv11 on
**Label/Mono Font:** JetBrains Mono Variable (with ui-monospace, SF Mono, Menlo)

**Character:** One neutral grotesk for everything a person reads, and a mono for everything a machine stamped: labels, tags, IDs, timestamps, SLA clocks.

### Hierarchy
- **Headline** (600, 24px, -0.022em): the page title, one per view, under the top bar. No kicker above it.
- **Title** (600, 14px): card and panel titles in sentence case.
- **Body** (400, 14px; 12px for meta): rows, forms, descriptions capped near 60-70ch.
- **Numeral** (600, 24px, tabular, line-height 1): dashboard figures, tinted in the severity tone only when non-zero.
- **Label** (mono 500, 11px, 0.1em, uppercase): table column headers, rail group names (Operate, Intel, Automate, Admin), field-group labels.
- **Tag** (mono 600, 10.5px, 0.06em, uppercase): severity, status, verdict and TLP tags.

### Named Rules
**The Tabular Rule.** Every number (counts, IDs, durations, percentages) is set with tabular figures so columns align.

**The No Eyebrow Rule.** Mono caps label data (columns, rail groups, field groups). They never sit above a page or section heading as a kicker, and sections are never numbered.

## Layout

App shell: fixed ink rail on the left (240px, 64px collapsed; off-canvas drawer with a dimmed scrim below 1024px), a 56px top bar on the canvas (breadcrumb, ⌘K search, Copilot, notifications, New case), and a scrolling main area. A skip link to `#main` is the first focusable element. Page content sits in a 1600px max container with a 16px gutter on mobile and 24px from 640px.

Hubs (Cases, Alerts, Observables, Settings) are a page title over underlined route tabs and one white sheet. Tables inside a hub bleed to the sheet's edges (negative margin) with only top and bottom hairlines, so there is never a card inside a card.

Dashboards are grids of sheets on paper. Stat tiles are cells of a ledger: a grid with 1px gaps over a line-coloured ground draws the hairlines between cells. Lists drive their filters from the URL (`?status=`, `?severity=`, `?sla=`, `?age=`, `?owner=`), so every filtered view is linkable and the back button works. Paginated lists offer 10, 40 (default) and 100 rows.

At 390px, table columns collapse: severity moves above the title and row actions sit under the title rather than in a trailing column.

## Elevation & Depth

Flat by default. Depth comes from tonal layering (stone canvas, white panel, raised hover) and hairlines, not shadows. The only shadows are a 1px hairline-weight shadow on resting sheets, and a soft violet-tinted lift on hover for interactive console cards. Dark mode adds two faint radial glows (violet top right, lime bottom right) to the working ground only; never on sheets, never in light mode.

### Named Rules
**The Paper Not Glass Rule.** No blur, no glow on components, no gradient fills. A sheet is white (or night) with a hairline.

## Shapes

Sharp and small: 4px for buttons, inputs and cards; 3px for tags, chips and rail controls; 2px for severity dots; 6px only for code blocks. Borders are 1px hairlines everywhere; the only 2px strokes are the active tab underline (accent) and the rail signal bar (lime). Severity dots are rounded squares, not circles.

## Components

### Buttons
Quiet and square.
- **Shape:** 4px corners; 32px (sm), 36px (md), 44px (lg).
- **Primary:** solid accent, white text (near-black on lavender in dark), semibold. One per view, usually New case or Save.
- **Secondary / Outline:** white sheet or transparent with a strong hairline, ink text; raised fill on hover.
- **Ghost:** no border, fg-2 text, raised fill on hover. Used for Clear filters and toolbar icons.
- **Danger:** outlined in critical at 50%, critical text; fills solid only on hover or keyboard focus. Destructive buttons sit apart from the primary action (separated, usually at the far end or in a danger section) and confirm through an in-app dialog, never `window.confirm`.
- **Focus:** 2px accent outline, 2px offset, on every interactive element.

### Tags (severity, status, verdict, TLP)
- **Style:** mono caps 10.5px, tone-coloured text, a 40% tone hairline and a 9% tone fill, 3px corners.
- **State:** tones come only from `tones.ts`. Neutral metadata tags (counts, labels) use the sans Badge on raised stone instead.

### Filter Chips
- **Style:** 28px, 3px corners, accent hairline at 40% over a 10% accent fill, ink text, a trailing × icon.
- **Behaviour:** a chip is a drill-down filter that arrived from a dashboard; activating it removes that URL parameter. Ordinary filters stay as selects in the toolbar.

### Cards / Containers
- **Corner Style:** 4px.
- **Background:** panel.
- **Shadow Strategy:** flat (see Elevation).
- **Border:** 1px line; header separated by a hairline, 12px by 16px.
- **Internal Padding:** 16px.

### Inputs / Fields
- **Style:** panel fill, strong hairline, 4px corners, 36px (32px small); selects carry an inset chevron.
- **Focus:** 2px accent outline drawn inside the edge.
- **Error / Disabled:** critical border and `aria-invalid`; 50% opacity, not-allowed cursor.

### Navigation
- **Rail:** ink field, white mark and app name, mono group labels at 50% rail ink, sentence-case items at 70% rail ink. Hover lifts the row to rail-2. Active: lifted row, full rail ink, lavender icon, 2px lime bar on the left edge, `aria-current="page"`. Organization switcher and the user, theme toggle, docs and sign-out sit at the foot.
- **Tabs:** plain 13px labels on the row's rule; the active tab turns ink with a 2px accent underline. Tablists move with arrow keys, Home and End; route tabs are NavLinks with `aria-current`.
- **Top bar:** breadcrumb (ink current, fg-3 parents linked), search trigger with ⌘K hint, Copilot, bell, and the primary New case.

### Stat Tile (signature)
A ledger cell: 12px fg-3 label with a chevron, a 24px tabular figure, optional 12px sub-line. The whole cell is a link to its filtered list (hover raises it, the chevron turns accent), so the keyboard path is a Tab stop per number. The figure takes the critical or high tone only when it is non-zero.

### SLA Clock
In lists already scoped to SLA risk, rows show how far over (critical, "29d 9h over") or how soon due (high, "due in 3h") in mono tabular figures, instead of repeating a BREACHED tag on every row.

### Empty, Loading, Error
Skeleton rows shaped like the content and fading down; centred empty state with one sentence and one action; inline error band in critical at 5% fill with a Try again button.

## Do's and Don'ts

### Do:
- **Do** use the semantic tokens (canvas, panel, line, fg, accent, lime, severity-*, rail-*); never raw hex in components.
- **Do** make every dashboard number, bar, slice and matrix cell a link to its filtered list, with a keyboard path (a link, or a table/legend of links beside a mouse-only chart).
- **Do** show a drill-down filter as a removable chip and keep it in the URL.
- **Do** let hub tables bleed to the sheet edges with top and bottom hairlines only.
- **Do** keep destructive actions outlined in critical, isolated from the primary action, and confirmed in-app.
- **Do** resolve every severity, status, verdict and TLP colour through `tones.ts`.
- **Do** set numbers in tabular figures and machine values (IDs, hashes, timestamps) in mono.

### Don't:
- **Don't** put a card inside a card; a table in a hub sheet is the sheet.
- **Don't** colour a status with the severity ramp, or invent a second severity scale for one view.
- **Don't** put eyebrows or kickers above headings, and don't number sections.
- **Don't** use lavender or the pale signal lime as text on paper.
- **Don't** add glow, blur, gradients or hard offset shadows; light mode is flat.
- **Don't** bring back anything from the retired case-file world: manila, rubber-stamp rotation or ink texture, folder-tab silhouettes, Archivo.
- **Don't** use glyph icons or emoji as icons; icons are Lucide line icons at 14-16px, `aria-hidden` beside a text label or with an `aria-label`.
