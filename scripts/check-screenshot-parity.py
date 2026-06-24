#!/usr/bin/env python3
"""
Manual Screenshot Parity Guard (v1) for CCC documentation.

Scope (v1, manual tool only -- no CI, no GitHub Action, no auto-enforcement):
  1. Existence/validity: every screenshot referenced by EN guide, FA guide, and
     README resolves to a real file in docs/screenshots/.
  2. EN<->FA chapter set-parity: per chapter, the set of screenshots embedded in
     docs/user-guide/NN.md equals that in docs/fa/user-guide/NN.md, minus
     entries in the ledger Deferrals allow-list. README is single-language and
     is EXCLUDED from set-parity (existence-only).
  3. Orphan placeholders: flag chapters where one edition still has a screenshot
     placeholder ("Screenshot needed" / "تصویر مورد نیاز") while the other edition
     already embeds screenshots for that chapter.
  4. Hygiene: no raw/temp/scratch files in docs/screenshots/.

Usage:  python3 scripts/check-screenshot-parity.py
Exit:   0 = all checks PASS, 1 = one or more FAIL.
Run authoritatively (e.g. on Windows) -- the sandbox mount can be stale.
"""
import os, re, sys, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "docs", "screenshots")
EN_DIR = os.path.join(ROOT, "docs", "user-guide")
FA_DIR = os.path.join(ROOT, "docs", "fa", "user-guide")
README = os.path.join(ROOT, "README.md")
LEDGER = os.path.join(SHOTS, "INTEGRATION-SYNC.md")

EN_EMBED = re.compile(r"\]\(\.\./screenshots/([a-z0-9-]+\.png)\)")
FA_EMBED = re.compile(r"\]\(\.\./\.\./screenshots/([a-z0-9-]+\.png)\)")
RM_EMBED = re.compile(r"docs/screenshots/([a-z0-9-]+\.png)")
EN_PLACEHOLDER = "Screenshot needed"
FA_PLACEHOLDER = "تصویر مورد نیاز"  # تصویر مورد نیاز

def chap_key(path):
    return os.path.basename(path).split("-", 1)[0]

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()

def scan(dir_, rx):
    out = {}
    for p in sorted(glob.glob(os.path.join(dir_, "[0-9]*.md"))):
        out[chap_key(p)] = set(rx.findall(read(p)))
    return out

def placeholders(dir_, token):
    out = {}
    for p in sorted(glob.glob(os.path.join(dir_, "[0-9]*.md"))):
        out[chap_key(p)] = read(p).count(token)
    return out

def load_deferrals():
    # parse the ledger "## Deferrals" table -> set of (chapter, asset)
    d = set()
    if not os.path.exists(LEDGER):
        return d
    lines = read(LEDGER).splitlines()
    grab = False
    for ln in lines:
        if ln.strip().startswith("## Deferrals"):
            grab = True; continue
        if grab and ln.startswith("## "):
            break
        if grab and ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            # columns: ID | Chapter | Asset | Reason | Tracking
            def blank(c):
                return (c in ("", "—", "-", "Chapter", "Asset")) or bool(re.fullmatch(r"-{2,}", c))
            if len(cells) >= 3 and not blank(cells[1]) and not blank(cells[2]):
                d.add((cells[1], cells[2]))
    return d

def main():
    fails = []
    disk = set(os.path.basename(p) for p in glob.glob(os.path.join(SHOTS, "*.png")))
    en = scan(EN_DIR, EN_EMBED)
    fa = scan(FA_DIR, FA_EMBED)
    rm = set(RM_EMBED.findall(read(README))) if os.path.exists(README) else set()
    en_ph = placeholders(EN_DIR, EN_PLACEHOLDER)
    fa_ph = placeholders(FA_DIR, FA_PLACEHOLDER)
    deferrals = load_deferrals()

    print("=" * 64)
    print("CCC Screenshot Parity Guard (v1, manual)")
    print("=" * 64)
    print(f"Assets on disk: {len(disk)}  |  Deferrals: {len(deferrals)}")

    # 1. Existence
    print("\n[1] Existence / validity of referenced assets")
    missing = []
    for area, refs in (("EN", set().union(*en.values()) if en else set()),
                       ("FA", set().union(*fa.values()) if fa else set()),
                       ("README", rm)):
        for a in sorted(refs):
            if a not in disk:
                missing.append(f"  {area}: {a} (referenced, not on disk)")
    if missing:
        fails.append("existence"); print("  FAIL"); print("\n".join(missing))
    else:
        print("  PASS")

    # 2. EN<->FA chapter set-parity (README excluded)
    print("\n[2] EN<->FA chapter set-parity (README excluded)")
    parity_fail = False
    for ch in sorted(set(en) | set(fa)):
        e = set(en.get(ch, set())); f = set(fa.get(ch, set()))
        miss_fa = {a for a in e - f if (ch, a) not in deferrals}
        miss_en = {a for a in f - e if (ch, a) not in deferrals}
        if miss_fa or miss_en:
            parity_fail = True
            print(f"  ch{ch}: EN={len(e)} FA={len(f)}  missing-in-FA={sorted(miss_fa)}  missing-in-EN={sorted(miss_en)}")
    if parity_fail:
        fails.append("parity"); print("  FAIL (see above)")
    else:
        print("  PASS")

    # 3. Orphan placeholders (one edition embedded, other still placeholdered)
    print("\n[3] Orphan placeholders")
    orphan_fail = False
    for ch in sorted(set(en) | set(fa) | set(en_ph) | set(fa_ph)):
        e_has = len(en.get(ch, set())) > 0; f_has = len(fa.get(ch, set())) > 0
        e_ph = en_ph.get(ch, 0); f_ph = fa_ph.get(ch, 0)
        if e_has and f_ph > 0:
            orphan_fail = True; print(f"  ch{ch}: EN embedded but FA still has {f_ph} placeholder(s)")
        if f_has and e_ph > 0:
            orphan_fail = True; print(f"  ch{ch}: FA embedded but EN still has {e_ph} placeholder(s)")
    if orphan_fail:
        fails.append("orphan"); print("  FAIL (see above)")
    else:
        print("  PASS")

    # 4. Hygiene
    print("\n[4] Hygiene of docs/screenshots/")
    junk = []
    for p in glob.glob(os.path.join(SHOTS, "*")):
        b = os.path.basename(p)
        if b.endswith(".png.png") or b.startswith("_") or "raw" in b.lower() or b.startswith("scr"):
            junk.append("  " + b)
    if junk:
        fails.append("hygiene"); print("  FAIL"); print("\n".join(junk))
    else:
        print("  PASS")

    print("\n" + "=" * 64)
    if fails:
        print(f"RESULT: FAIL ({', '.join(fails)})")
        sys.exit(1)
    print("RESULT: PASS")
    sys.exit(0)

if __name__ == "__main__":
    main()
