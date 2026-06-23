# CCC Diagram Style System

Authoritative style specification for all CCC documentation diagrams (**DGM-01 … DGM-12**). Every diagram produced in Phase L.1B (and later) **must** conform to this document. It is the single source of truth for typography, color, node/edge styling, layout, the Mermaid theme, and SVG/PNG export.

> [!NOTE]
> This file defines style only. It does not render diagrams and does not modify any `.mmd` source. The Mermaid sources live in [`source/`](source/); their specs live in [`specifications/`](specifications/); rendered output will go to [`svg/`](svg/) and [`png/`](png/).

## 1. Design intent

Modern SaaS documentation style — calm, flat, high-contrast, generous whitespace. Reference quality: Cloudflare Docs, Stripe Docs, Tailscale Docs, GitHub Docs.

Explicitly **not**: Visio, PowerPoint, marketing graphics, or cartoon illustrations. No gradients, drop shadows, skeuomorphism, 3-D, clip-art, or decorative icons.

## 2. Typography

| Token | Value |
|---|---|
| Font family | `Inter, "Segoe UI", Arial, sans-serif` |
| Node label size | 14px |
| Edge label size | 13px |
| Subgraph / group title size | 13px, weight 600 |
| Font weight (labels) | 400 (regular); 600 only for the focal node and group titles |
| Letter spacing | default (none) |

Rules:

- Inter is the primary face; `Segoe UI` and `Arial` are fallbacks for systems without Inter. Diagrams must remain legible under any fallback, so never rely on Inter-specific metrics.
- Labels are short. Keep a device name and its example IP on one line (e.g. `Raspberry Pi · 192.168.1.50`).
- Use a middle dot `·` (U+00B7) as the inline separator and `<br/>` for an intentional second line. Do not introduce other separators.

## 3. Color palette

### 3.1 Core (from the approved design system)

| Role | Hex |
|---|---|
| Primary | `#2563EB` |
| Success | `#16A34A` |
| Warning | `#D97706` |
| Danger | `#DC2626` |
| Neutral | `#6B7280` |

### 3.2 Derived tokens (fills, text, lines)

Each semantic role uses a light tint as fill, the core color as the 2px border, and a dark shade as text. These are fixed; do not improvise new shades.

| Role | Fill | Border (stroke) | Text |
|---|---|---|---|
| Primary | `#EFF6FF` | `#2563EB` | `#1E40AF` |
| Success | `#F0FDF4` | `#16A34A` | `#166534` |
| Warning | `#FFFBEB` | `#D97706` | `#92400E` |
| Danger | `#FEF2F2` | `#DC2626` | `#991B1B` |
| Neutral | `#F9FAFB` | `#6B7280` | `#374151` |

| Shared token | Hex | Use |
|---|---|---|
| Line / edge color | `#6B7280` | all connector strokes |
| Edge label text | `#374151` | text on edge labels |
| Edge label background | `#FFFFFF` | halo behind edge labels |
| Canvas background | transparent | see §10 / §11 |

## 4. Semantic color usage

Color carries meaning; it is never decorative. Apply exactly one class per node.

| Class | When to use | Examples in the DGM set |
|---|---|---|
| `primary` | The focal subject of the diagram — the thing the chapter is teaching about, or CCC itself. **At most one or two per diagram.** | Raspberry Pi / Conduit node (DGM-01, 03, 04, 05, 06, 07, 08); CCC (DGM-12); the domain name (DGM-09); the subject host (DGM-04). |
| `neutral` | Supporting infrastructure and generic actors that are not the focus. | Router, Internet, DNS, web server, other devices (Laptop/Phone/TV), public-IP nodes. |
| `success` | A positive / completed / healthy outcome. | "Assigns 192.168.1.50" target, "conduit.example.com points to the current IP", the "No change" healthy branch. |
| `warning` | A point requiring attention, or a state-change branch. | Decision diamond and the "Update DNS record" branch in DGM-12. |
| `danger` | An error, failure, or blocked state. | Reserved — none of DGM-01…DGM-12 currently require it; use only if a future diagram shows a failure path. |

Guidance:

- Default everything to `neutral`, then promote the focal node(s) to `primary` and outcome/decision nodes to `success`/`warning`.
- Do not use more than three semantic colors in a single diagram; most will use only `primary` + `neutral` (+ one accent).
- The Internet and cloud actors are `neutral`, never `primary`.

## 5. Node classes (Mermaid `classDef`)

These class definitions are authoritative. Append them to each diagram at production time (Phase L.1B); do not redefine colors inline per node.

```mermaid
classDef primary fill:#EFF6FF,stroke:#2563EB,color:#1E40AF,stroke-width:2px;
classDef neutral fill:#F9FAFB,stroke:#6B7280,color:#374151,stroke-width:2px;
classDef success fill:#F0FDF4,stroke:#16A34A,color:#166534,stroke-width:2px;
classDef warning fill:#FFFBEB,stroke:#D97706,color:#92400E,stroke-width:2px;
classDef danger  fill:#FEF2F2,stroke:#DC2626,color:#991B1B,stroke-width:2px;
```

Assignment uses `class <id1>,<id2> <className>;` (or `:::className` inline). Decision diamonds (`{ }`) take `warning`. Subgraph containers stay unfilled (transparent fill, `#6B7280` 1px dashed border, title weight 600) — they group, they do not compete for attention.

## 6. Edge & arrow styles

| Property | Value |
|---|---|
| Stroke width | 2px |
| Stroke color | `#6B7280` |
| Arrowhead | Mermaid default thin/open arrow (`-->`). No large, filled, or ornamental heads. |
| Edge style | solid for real flow; dotted (`-.->`) only for "logical/optional" relationships (none required in the current 12) |
| Edge labels | 13px, `#374151`, on a `#FFFFFF` background halo; keep to ≤ 4 words (e.g. "Requests an IP", "TCP 443", "Assigns") |
| Bidirectional pairs | model as two separate single-direction edges (as in DGM-10), never a double-headed arrow |

## 7. Geometry

| Property | Value |
|---|---|
| Node corner radius | **8px** (`rx="8" ry="8"` on every node `<rect>`) |
| Node border width | 2px |
| Decision node | diamond (Mermaid `{ }`), 2px border, `warning` class |
| Node padding | ~12px horizontal, ~8px vertical (Mermaid default is acceptable) |

Mermaid does not expose a global corner-radius variable for `[rectangle]` nodes, so 8px rounding is enforced in **SVG post-processing** (§10), not in the source. Do not switch source node shapes to achieve rounding.

## 8. Layout rules

- **Direction:** prefer horizontal flow (`flowchart LR`). Use top-down (`flowchart TD`) only where the structure is inherently hierarchical: one-to-many trees (DGM-01, DGM-03, DGM-11) and the decision flow (DGM-12). Respect each diagram's existing direction as set in its source.
- **Complexity:** keep diagrams to roughly ≤ 6 nodes and one idea each. No nested subgraphs beyond one level.
- **Crossings:** avoid edge crossings. Order nodes so converging edges (e.g. DGM-05 NAT) stay tidy; if crossings appear after the first render, adjust node order before adding visual workarounds.
- **Spacing:** `nodeSpacing: 50`, `rankSpacing: 60` (see §9). Consistent spacing across all 12 so they feel like one set.
- **Alignment:** single primary axis of flow; do not mix LR and TD within one diagram.
- **Whitespace:** let the renderer breathe; never compress to fit. Width is derived, not fixed.

## 9. Mermaid theme rules

Two interchangeable mechanisms — both must produce identical results.

### 9.1 Per-file init directive (for GitHub inline rendering)

Prepend to a source only if inline GitHub appearance must match (optional; the canonical render path is §9.2):

```
%%{init: {
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, \"Segoe UI\", Arial, sans-serif",
    "fontSize": "14px",
    "lineColor": "#6B7280",
    "primaryColor": "#F9FAFB",
    "primaryBorderColor": "#6B7280",
    "primaryTextColor": "#374151",
    "tertiaryColor": "#FFFFFF"
  }
}}%%
```

### 9.2 Canonical render config (`mermaid-config.json` for mermaid-cli)

This file is the authoritative render configuration for Phase L.1B (path to be created then, e.g. `docs/diagrams/mermaid-config.json`):

```json
{
  "theme": "base",
  "themeVariables": {
    "fontFamily": "Inter, \"Segoe UI\", Arial, sans-serif",
    "fontSize": "14px",
    "lineColor": "#6B7280",
    "primaryColor": "#F9FAFB",
    "primaryBorderColor": "#6B7280",
    "primaryTextColor": "#374151",
    "tertiaryColor": "#FFFFFF"
  },
  "flowchart": {
    "htmlLabels": true,
    "curve": "linear",
    "nodeSpacing": 50,
    "rankSpacing": 60,
    "padding": 12,
    "useMaxWidth": true
  },
  "themeCSS": ".node rect, .node polygon { stroke-width: 2px; } .edgeLabel { background-color: #FFFFFF; color: #374151; font-size: 13px; } .cluster rect { fill: none; stroke: #6B7280; stroke-dasharray: 4 3; }"
}
```

Rules:

- `htmlLabels` **must stay `true`** — sources use `<br/>` and `&nbsp;`; with htmlLabels off they would render literally.
- The base `themeVariables` set the neutral default; semantic color comes from the `classDef` block (§5), which overrides per node.
- Do not use Mermaid's `dark`/`forest`/`neutral` built-in themes; only `base` + these overrides.

## 10. SVG export rules

SVG is the **primary committed artifact**.

1. **Render:** `mmdc -i source/<file>.mmd -o svg/<asset>.svg -c mermaid-config.json -b transparent`, where `<asset>` is the manifest filename (e.g. `network-ip-address.svg`). Background **transparent**.
2. **Filenames:** exactly the manifest column value (kebab-case, no DGM prefix), in `svg/`. Never rename.
3. **Post-process (required):**
   - Set `rx="8" ry="8"` on every node `<rect>` (enforces §7 corner radius).
   - Confirm node borders are 2px and use the palette stroke colors from §3.2.
   - Keep text as live `<text>` (selectable, accessible) with the §2 font-family chain — do **not** convert text to paths.
   - Preserve the `viewBox`; remove any hard-coded `max-width`/fixed pixel `width`/`height` so the asset scales responsively (`useMaxWidth` already helps).
4. **Hygiene:** strip editor metadata/comments; ensure valid, self-contained SVG (no external font/image refs).
5. **Accessibility:** add a `<title>` (and `<desc>` where useful) from the diagram's spec Purpose; docs embedding the asset must supply alt text.

## 11. PNG export rules

PNG is a **derived** artifact for contexts that cannot use SVG.

1. **Produce directly from Mermaid** (preferred over rasterizing the SVG, since no high-fidelity SVG rasterizer is guaranteed): `mmdc -i source/<file>.mmd -o png/<asset>.png -c mermaid-config.json -b transparent -s 2`.
2. **Scale:** `-s 2` minimum (2× for retina); `-s 3` for any hero asset. Width derives from content; do not upscale a 1× raster.
3. **Background:** transparent, to match both light and dark documentation themes. (If a specific embed requires a solid backdrop, render a white-background variant rather than baking color into the canonical asset.)
4. **Filenames:** the manifest base name with `.png`, in `png/`.
5. **Parity:** the PNG must be visually identical to the SVG — same theme config, same classes; never restyle for PNG.

## 12. Dark-mode note

Diagrams use light tints with dark text and are tuned for light backgrounds; on a transparent canvas they remain readable on light pages and on the muted backgrounds GitHub/most docs use in dark mode (the borders and dark label text retain contrast). A dedicated dark variant is out of scope for the current 12; revisit only if a fully dark documentation theme is adopted.

## 13. Conformance checklist (per diagram, at production time)

- [ ] Font family is the §2 chain; node text 14px, edge labels 13px.
- [ ] Every node has exactly one semantic class (§4/§5); focal node is `primary`.
- [ ] No more than three semantic colors used.
- [ ] All borders 2px; all node corners `rx/ry=8`.
- [ ] Edges 2px `#6B7280`, default thin arrowheads; labels ≤ 4 words on white halo.
- [ ] Direction matches the source; ≤ ~6 nodes; no avoidable crossings.
- [ ] Rendered with `mermaid-config.json`; `htmlLabels` on.
- [ ] SVG: transparent bg, live text, 8px corners, manifest filename, responsive viewBox, `<title>` set.
- [ ] PNG: transparent bg, ≥ 2× scale, manifest filename, identical to SVG.
- [ ] No real domains, public IPs, tokens, QR codes, usernames, or credentials (generic teaching values only).
