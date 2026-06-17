# Conduit Control Center — Brand Assets (B1)

Source-of-truth branding assets for CCC. These are **design artifacts** from epic
**B1**. Integration (favicon wiring, manifest, README header, login/dashboard,
avatar upload) happens in later tasks (B2–B7) and is **not** part of this folder's
scope.

## Dual-role palette

Two colors, two non-overlapping roles. This separation is the core rule of the
identity — it is what makes a teal mark inside an indigo UI intentional rather
than a clash.

| Role | Color | Value | Used for | Never used for |
|---|---|---|---|---|
| **Brand / identity** | Teal-600 | `#0D9488` | The mark on light surfaces | Buttons, links, focus, any control |
| **Brand / identity (dark)** | Teal-400 | `#2DD4BF` | The mark on dark surfaces / Deep Ink | — |
| **Surface** | Deep Ink | `#0F172A` | Avatar & maskable background; neutral wordmark on light | — |
| **UI / action** | Indigo-600 | `#4F46E5` | Buttons, links, active nav, focus rings | **The logo — indigo never appears in any brand asset** |

The **wordmark** is rendered in the **neutral theme text color** (not teal, not
indigo) so it stays legible and theme-aware.

## Assets

| File | What | Use |
|---|---|---|
| `ccc-logo-master.svg` | Layered master (all variants + palette) | Source of truth; do not export directly |
| `ccc-mark.svg` | Full mark — 1 hub + 6 nodes, teal | ≥48px: dashboard, login, docs, PWA, avatar source |
| `ccc-mark-simplified.svg` | Simplified mark — 1 hub + 3 nodes, thick strokes | ≤32px: favicons, small UI |
| `ccc-mark-mono.svg` | Monochrome mark (`currentColor`) | Single-color contexts (black-only / white-only); print; stamps |
| `ccc-logo-lockup.svg` | Mark + "Conduit Control Center" wordmark | README header, login |
| `avatar/ccc-avatar-512.svg` | Full-bleed Deep Ink, Teal-400 mark | GitHub/social avatar source |
| `icon-512-maskable.svg` | Mark inside the 80% safe zone, full-bleed | Android maskable / PWA |
| `proof-sheet.html` | Renders the size matrix (16/32/48/180/512), light/dark/mono, crops, swatches | Review / sign-off |

## Geometry

- Full mark drawn on a **48-unit** grid; simplified mark on a **32-unit** grid.
- Legibility floor: effective spoke weight **≥ 2px** and node diameter **≥ 3px**
  at a 16px render — the simplified mark is the only variant used at ≤32px.
- The hub is deliberately larger than the outer nodes so the structure reads
  from **geometry alone**, satisfying the monochrome requirement (no reliance on
  color difference to communicate the shape).
- Clear space around the mark = **1× hub diameter**. Lockup gap = wordmark cap
  height.

## Do / Don't

- **Do** keep the mark teal; **don't** recolor it indigo.
- **Do** use the simplified mark at small sizes; **don't** shrink the 6-node mark
  below 48px.
- **Do** keep avatar/maskable detail inside the centered safe circle; **don't**
  run mark detail to the edges (circular crops will clip it).
- **Do** use `ccc-mark-mono.svg` where only one ink is available; **don't**
  approximate mono by flattening the teal.
- **Don't** stretch, rotate, or add effects (shadows/gradients) to the mark.

## Notes for export (B2)

- Convert the lockup wordmark to outlines so it renders without the system font.
- Generate `favicon.svg` with a `prefers-color-scheme` swap (Teal-600 light /
  Teal-400 dark) plus `favicon.ico` (16/32/48), `favicon-16/32.png`,
  `icon-192/512.png`, `icon-192/512-maskable.png`, `apple-touch-icon.png` (180),
  and the avatar PNG — all from these SVG sources.
