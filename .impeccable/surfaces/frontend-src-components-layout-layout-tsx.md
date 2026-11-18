---
version: 1
slug: "frontend-src-components-layout-layout-tsx"
primary_target: "frontend/src/components/layout/Layout.tsx"
related_targets: ["frontend/src/index.css"]
---

# App shell and all signed-in views
Mode: Operate. Audience: solo responders and tiny IR teams working incidents from first alert to closed case (see PRODUCT.md). Scope: the whole web app (shell, every route); guest portal and auth pages inherit the world.
Constraints: semantic tokens only (frontend/src/index.css); standard web controls; keyboard and WCAG AA contrast; light theme by default, dark theme via prefers-color-scheme or the rail toggle. 2026-11-18: replaces the 2026-11-17 case-file world at the user's request ("colors related to the current website so the logo looks better", tone of the user-named reference site abliteration.ai).
## Direction contract
THESIS: sochub the app is the same product as sochub.io: an ink rail carrying the white hex mark, lavender as the single accent, lime as the signal. Light mode borrows an editorial, technical restraint: warm stone paper, white sheets, sharp 4px corners, mono caps for data labels. Every number on a dashboard is a door into the cases behind it.
OWN-WORLD: Ink rail (#111113) in both themes owns navigation and the logo; active item = lifted row, lavender icon, a lime signal bar (the website's glowing dot). Light: stone paper ground #f3f0eb, white sheets, ink #1b1b1b text, lavender-violet accent #5546d6 for text/primary, lavender #a4a8ff and lime #d6e37f as fills only. Dark: the website's #0a0a0a with a faint violet and lime glow, lavender #a4a8ff accent, lime for good deltas. Inter for UI, JetBrains Mono for eyebrows, tags and code; tabular figures for numbers. Status and severity are tinted mono tags (hairline edge, 9% fill).
STORY: The analyst lands on Overview, sees what is at risk, clicks any tile, bar, slice or cell and arrives at the exact filtered list behind it (filter shown as a removable chip), opens the case, works it under underlined section tabs, closes it.
FIRST VIEWPORT: Left: 240px ink rail with mark, grouped navigation (Operate, Intel, Automate, Admin) in sentence case under mono group labels, organization switcher and user at the foot. Top: thin bar with breadcrumb, ⌘K search, Copilot, bell, New case. Center: page title over white sheets. Signature: the ink rail + lime signal bar, and drill-down everywhere (widgets -> pre-filtered lists).
FORM: brand-continuity world (sochub.io) with a light editorial mode; user-directed, no seed roll.
RAISES:
- From the case-file world (retired): destructive actions stay outlined and isolated; one fixed severity ramp in every view; no nested cards.
- From the reference site: sharp corners, restraint, mono caps for data labels only (no eyebrows or section numbers); no glow in light mode.
FINISH: unreviewed and undocumented is unfinished; ends with the critique, fixes, and a regenerated DESIGN.md
