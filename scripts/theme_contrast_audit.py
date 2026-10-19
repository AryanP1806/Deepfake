"""WCAG contrast audit of every theme token pair in frontend/src/index.css (3 themes x light/dark). Exit code 1 on any failure."""
import re, itertools, sys
css = open(r"C:\deepfake\frontend\src\index.css", encoding="utf-8").read()

def lum(h):
    h = h.lstrip("#"); r, g, b = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
def cr(a, b):
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

def block(sel):
    m = re.search("^" + re.escape(sel) + r"\s*\{(.*?)\n\}", css, re.S | re.M)
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1))) if m else {}

theme_default = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", re.search(r"@theme \{(.*?)\n\}", css, re.S).group(1)))
fail = 0
for theme in ("workbench", "neomorphism", "claymorphism"):
    for mode in ("light", "dark"):
        v = dict(theme_default); v.update(block(f'[data-mode="{mode}"]')); v.update(block(f'[data-theme="{theme}"][data-mode="{mode}"]'))
        edge = {"workbench": v["--color-line-strong"]}
        m = re.search(rf'\[data-theme="{theme}"\]\[data-mode="{mode}"\] \{{.*?--edge-field: (#[0-9a-fA-F]{{6}}|var\(--[\w-]+\))', css, re.S)
        field = m.group(1) if m else "?"
        field = v.get(field[4:-1], field) if field.startswith("var") else field
        print(f"== {theme} / {mode}")
        for fg in ("--color-ink", "--color-ink-2", "--color-ink-3"):
            for bg in ("--color-bg", "--color-surface", "--color-raised"):
                c = cr(v[fg], v[bg]); ok = c >= 4.5
                if not ok: fail += 1; print(f"   FAIL text {fg} on {bg}: {c:.2f}")
        for k in ("--color-st-verified", "--color-st-corroborated", "--color-st-inferred", "--color-st-lead", "--color-st-unknown", "--color-crit", "--color-warn", "--color-good", "--color-v-fake", "--color-v-likely-fake", "--color-v-uncertain", "--color-v-likely-real", "--color-v-real"):
            c = cr(v[k], v["--color-surface"])
            need = 4.5 if k in ("--color-crit", "--color-warn", "--color-good") else 3.0
            if c < need: fail += 1; print(f"   FAIL {k} on surface: {c:.2f} (need {need})")
        c = cr(v["--color-crit-ink"], v["--color-crit"]);
        if c < 4.5: fail += 1; print(f"   FAIL crit-ink on crit {c:.2f}")
        c = cr(v["--color-good-ink"], v["--color-good"])
        if c < 4.5: fail += 1; print(f"   FAIL good-ink on good {c:.2f}")
        if field.startswith("#"):
            c = cr(field, v["--color-bg"]);
            if c < 3: fail += 1; print(f"   FAIL input border {field} vs bg {c:.2f} (need 3)")
            else: print(f"   input border {c:.2f}")
print("failures:", fail)
sys.exit(1 if fail else 0)
