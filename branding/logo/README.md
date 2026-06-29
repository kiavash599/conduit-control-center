# Official CCC Logo System v1.0 — PROPOSAL (review candidate)

> **Status: NOT adopted. The repository is unchanged.** Generated in the scratch
> outputs area only. After approval, a single **isolated branding commit** wires
> these assets in.
>
> **Governance:** see `BRAND-USAGE-GUIDE.md` (palette, gradient, clear space,
> minimum sizes, background rules, incorrect usage, typography, export spec,
> versioning). `system/ccc-logo-incorrect-usage.png` illustrates the don'ts.

## Principle — one logo, many technical adaptations
There is **one** official Conduit Control Center logo. The favicon, app icons,
monochrome versions, maskable icon, and the solid stencil glyph are **technical
adaptations of that same logo** — not alternate logos or secondary branding.
They exist only to preserve recognizability where the full mark cannot reproduce
faithfully (16×16, small icons, monochrome, print, engraving, laser, embroidery,
maskable, platform requirements).

**Identity anchor:** the split **dual-C ring** (cyan `#22C7F0` → indigo `#5B34E8`)
is constant in every form. The **globe + 6 network nodes** are detail that drops
out only where it cannot survive (≤32 px, 1-bit, engraving, embroidery). The
simplified glyph is the *same* logo, reduced — never a different mark.

## Inventory (35 files)
```
branding/logo/
  master/     ccc-logo-master.svg            ccc-logo-master-1024.png
  variants/   ccc-logo-full-color(.svg/-1024.png)
              ccc-logo-dark(.svg/-1024.png)   ccc-logo-light(.svg/-1024.png)
              ccc-logo-mono-black(.svg/-1024.png)  ccc-logo-mono-white(.svg/-1024.png)
              ccc-glyph-stencil.svg           ccc-glyph-stencil-black-1024.png
                                              ccc-glyph-stencil-white-1024.png
  icons/      icon-{1024,512,256,192,180,128,64,48,32,24,16}.png   (transparent)
              icon-512-maskable.png  icon-192-maskable.png         (opaque safe-zone)
  favicon/    favicon.svg  favicon.png(48)  favicon.ico(16/32/48)
  system/     ccc-logo-system-overview.png   (the proof sheet)
  build/      generate-icons.py  generate-svg.py  (+ gen2 additions; reproducible)
  README.md
```

## Adaptation map (context → asset → why)
| Context | Asset | Why this form |
|---|---|---|
| Primary / web / print (vector) | `master/ccc-logo-master.svg` | Full mark, scales infinitely |
| On dark / on light surfaces | `variants/ccc-logo-{dark,light}` | Same gradient mark; transparent |
| Monochrome (1-color UI/doc) | `variants/ccc-logo-mono-{black,white}` | Single solid color, full detail |
| Engraving / laser / embroidery / 1-bit | `variants/ccc-glyph-stencil*` | Solid single-color simplified glyph; no thin lines/gradients |
| App icons (≥48) | `icons/icon-{1024…48}.png` | Full mark, transparent |
| Favicon / small icons (≤32) | `icons/icon-{32,24,16}.png`, `favicon/*` | **Simplified glyph** for legibility |
| Android adaptive (maskable) | `icons/icon-{512,192}-maskable.png` | Mark in 66% safe zone on opaque Ink bg |

## Validation performed
- **Transparent backgrounds:** all logo/icon PNGs RGBA, alpha (0,255). ✓
- **Maskable:** opaque full-bleed (alpha 255,255), mark within the 66% safe zone. ✓
- **Pixel-perfect:** every file re-opened and confirmed at exact target size. ✓
- **Small-size legibility:** 16/24/32 + favicon use the simplified glyph; verified the C-ring + hub stay recognizable. ✓
- **SVG cleanliness:** all SVGs parse as well-formed XML; minimal markup, single gradient def, `viewBox 0 0 512 512`, round caps. ✓
- **System consistency:** one parametric geometry (center 256, R 205) drives every size/variant/SVG → identical proportions, padding, alignment. See `system/ccc-logo-system-overview.png`. ✓

## Planned Branding Commit (after your approval — NOT done yet)
A **single, branding-only commit** (no functional code) that performs the official
transition **B1 → CCC Logo System**:
1. Install this pack into the repo (proposed `branding/logo/…`, superseding `docs/brand/` B1).
2. Update **brand documentation** (`docs/brand/README.md`): retire the teal-only rule; document the gradient identity + the "one logo, adaptations" principle, identity anchor, and clearspace.
3. Replace **Dashboard favicon** (frontend template/static), **MkDocs favicon**, and **website favicons** with the new `favicon.*`.
4. Replace **application/PWA icons** (192/512 + maskable) and **branding previews**.
5. Replace existing logo assets (`docs/brand/*.svg`, website branding lockup).

Clean-history guarantees: branding isolated from features; one commit scoped to
branding paths only; exact favicon/manifest wiring re-inspected at commit time and
reported before changes. (Targets seen so far: `docs/brand/`, `website/overrides/assets/branding/`,
`mkdocs.yml`, dashboard `frontend/templates`+`static`.)

## Known limitations
- Recreation is **flat gradient**, not the upload's photographic 3D gloss/glow (correct for production).
- No SVG rasterizer in this environment (librsvg/cairo absent, PyPI blocked); PNG/ICO drawn with Pillow. Re-run `build/` anywhere to regenerate identically.
