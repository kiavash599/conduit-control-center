# Conduit Control Center — Brand Usage Guide

**CCC Logo System v1.0** · 2026-06-29 · PROPOSAL (review candidate; repo unchanged)

There is **one** official CCC logo. Every asset in this package is a *technical
adaptation* of it (see [README](README.md)). This guide governs how the logo is
used so the identity stays consistent over time.

---

## 1. Official Logo

- **Master logo:** `master/ccc-logo-master.svg` — the full mark: split **dual-C ring** with a central **globe** and **6 network nodes**. The SVG is the source of truth.
- **Official color palette**

  | Role | Name | HEX | RGB |
  |---|---|---|---|
  | Brand (start) | Brand Cyan | `#22C7F0` | 34, 199, 240 |
  | Brand (end) | Brand Indigo | `#5B34E8` | 91, 52, 232 |
  | Surface / mono-dark | Ink | `#0F172A` | 15, 23, 42 |
  | Mono-light | White | `#FFFFFF` | 255, 255, 255 |

- **Gradient definition:** linear, **horizontal (left → right)**, 2 stops — `0% #22C7F0` → `100% #5B34E8`. No other gradient angles, stops, or colors are permitted in the logo.
- **Flat adaptation:** the same geometry rendered in the horizontal cyan→indigo gradient with **no gloss, bevel, glow, or 3D** (the production form). Use everywhere the full logo appears in color.
- **Monochrome adaptation:** the same geometry in a single solid color — **Ink `#0F172A`** on light, **White `#FFFFFF`** on dark (`variants/ccc-logo-mono-*`). For 1-bit / engraving / laser / embroidery use the **solid stencil glyph** (`variants/ccc-glyph-stencil*`).

---

## 2. Clear Space
Keep a minimum clear space on **all four sides** equal to **25% of the logo's
height** (≈ the diameter of the central globe). No text, graphics, edges, or other
logos may enter this zone. When in doubt, give it more room.

```
   ┌───────────────────────┐
   │        ↕ 0.25H        │
   │     ┌───────────┐      │
   │ 0.25H │  LOGO H │ 0.25H│
   │     └───────────┘      │
   │        ↕ 0.25H        │
   └───────────────────────┘
```

---

## 3. Minimum Size
| Form | Minimum (digital) | Minimum (print) | Notes |
|---|---|---|---|
| **Full logo** (globe + nodes) | **48 px** | ~12 mm | Below this, detail blurs → switch to the mark/simplified glyph |
| **Logo mark / simplified glyph** | **16 px** | ~5 mm | Dual-C ring + hub; the smallest faithful form |
| **Favicon** | **16 px** | — | Uses the simplified glyph |
| **App icon** | **48 px** (transparent) · **192 px** (maskable) | — | Maskable keeps the mark within the 66% safe zone |

---

## 4. Background Rules
| Background | Use | Asset |
|---|---|---|
| **Light** | Full-color gradient logo | `variants/ccc-logo-light` / `master` |
| **Dark** | Full-color gradient logo (it reads on dark); or White mono if a single color is required | `variants/ccc-logo-dark` / `ccc-logo-mono-white` |
| **Transparent** | The gradient or mono logo, depending on the surface it will sit on | any `variants/*` or `icons/*` (all PNGs are transparent) |
| **Photographic / busy** | Prefer a mono version (Ink or White) for contrast; add clear space | `ccc-logo-mono-*` |

**When to use which:** full-color gradient is the default. Use **monochrome** when color is unavailable, when contrast is poor, or for single-ink/engraved reproduction. Use the **maskable** icon only for platform adaptive-icon slots. Use the **simplified/stencil** glyph only at small sizes or in 1-bit/physical reproduction.

---

## 5. Incorrect Usage
See `system/ccc-logo-incorrect-usage.png`. Do **not**:
- stretch, squash, or otherwise distort proportions;
- rotate or skew the mark;
- recolor it or use unofficial colors;
- change the gradient (angle, stops, or colors);
- add effects — drop shadows, outer glow, bevel, 3D;
- place it on low-contrast or clashing backgrounds;
- re-arrange, remove, or re-space the ring / globe / nodes;
- re-draw it — always use the supplied files.

---

## 6. Typography
The logo mark contains **no text** — it is a symbol only. Any wordmark or sample
text shown beside the logo in mockups is for **demonstration only and is not part
of the logo mark**. For an adjacent "Conduit Control Center" wordmark or UI text,
use a neutral sans-serif (the project self-hosts **Inter**); set it in a brand-
neutral color (Ink on light, White on dark), never inside the gradient. The
typeface is not licensed or owned as part of the logo.

---

## 7. Export Specification
| Asset | File(s) | Intended usage |
|---|---|---|
| **SVG (master/variants)** | `master/ccc-logo-master.svg`, `variants/*.svg` | Source of truth; web, scalable, print |
| **PNG (full color)** | `master/ccc-logo-master-1024.png`, `variants/ccc-logo-full-color-1024.png` | Raster placements, docs, slides |
| **PNG (dark/light)** | `variants/ccc-logo-{dark,light}-1024.png` | Surface-specific raster placements |
| **Monochrome** | `variants/ccc-logo-mono-{black,white}.{svg,png}` | Single-ink UI/docs, low-color contexts |
| **Stencil (print/engraving)** | `variants/ccc-glyph-stencil.svg`, `…-{black,white}-1024.png` | Laser, engraving, embroidery, 1-bit |
| **App icons** | `icons/icon-{1024…16}.png` | App/PWA icons; ≤32 use simplified glyph |
| **Maskable** | `icons/icon-{512,192}-maskable.png` | Android/PWA adaptive icon slots |
| **Favicon** | `favicon/favicon.{svg,png,ico}` | Browser tabs; `.ico` packs 16/32/48 |
| **ICO** | `favicon/favicon.ico` | Legacy/Windows favicon |
| **System overview** | `system/ccc-logo-system-overview.png` | Reference proof sheet |

---

## 8. Versioning
**CCC Logo System v1.0** (2026-06-29).

The Logo System is versioned **independently of software releases**. Future logo
refinements increment this number (v1.1, v2.0, …) and are recorded here; they do
**not** track the application's `APP_VERSION` / CHANGELOG. A branding change and a
software change must never share a commit.
