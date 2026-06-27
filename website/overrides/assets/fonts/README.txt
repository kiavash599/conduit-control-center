Self-hosted webfonts for the CCC documentation site (no Google Fonts, no CDN).

Add these two variable woff2 files here, renamed exactly as below so they match
website/overrides/assets/stylesheets/extra.css:

  Vazirmatn-Variable.woff2   (Persian body text)
      from https://github.com/rastikerdar/vazirmatn/releases  (webfonts / variable woff2)
  Inter-Variable.woff2       (English body text)
      from https://github.com/rsms/inter/releases  (InterVariable.woff2 -> rename)

Also drop the licenses next to them (both fonts are OFL):
  OFL-Vazirmatn.txt
  OFL-Inter.txt

These are downloaded ONCE to self-host. At runtime the site makes no external
font requests. If the files are absent, the page still renders using the system
fallback stack (Vazirmatn -> Inter -> Tahoma/system) — fine for RTL/LTR testing,
but add them before deploying for correct Persian typography.
