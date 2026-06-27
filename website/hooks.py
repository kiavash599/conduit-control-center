"""CCC docs landing-page generator (Option B).

Writes the site root index.html from website/landing.html at build time.
Scope is intentionally minimal: NO docs/ access, NO URL remapping, NO path
synthesis. Its only job is to provide the bilingual root landing page.
"""
import os


def on_post_build(config, **kwargs):
    here = os.path.dirname(__file__)
    landing = os.path.join(here, "landing.html")
    with open(landing, encoding="utf-8") as src:
        html = src.read()
    out = os.path.join(config["site_dir"], "index.html")
    with open(out, "w", encoding="utf-8") as dst:
        dst.write(html)
