"""Generate the README visuals in assets/ (stdlib only).

Every number here is real, recorded while delegate v2 was built and first run on
2026-11-27. Run:  python scripts/make_visuals.py
Each chart is written twice (light and dark); the README picks one with <picture>.
"""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

SANS = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace"
SERIF = "Georgia, 'Times New Roman', serif"

# Chart tokens: the dataviz reference palette (validated: first three slots pass all-pairs
# CVD + normal-vision checks in both modes).
THEMES = {
    "light": dict(surface="#fcfcfb", border="#e4e3df", text="#0b0b0b", text2="#52514e",
                  muted="#7a7974", grid="#ebeae6", codex="#2a78d6", gemini="#eb6834",
                  claude="#1baf7a", good="#0ca30c", good_wash="#e6f4e3", line="#8a8984"),
    "dark": dict(surface="#1a1a19", border="#33332f", text="#f0efec", text2="#c3c2b7",
                 muted="#94938c", grid="#2a2a27", codex="#3987e5", gemini="#d95926",
                 claude="#199e70", good="#0ca30c", good_wash="#1f2e1d", line="#7a7974"),
}
# Brand tokens from docs/index.html (the project site), for banner + diagram.
BRAND = {
    "light": dict(paper="#faf8f2", card="#fffdf8", ink="#24251f", muted="#595c52",
                  accent="#a53b20", accent_wash="#f7e4d8", sage="#41624c", sage_wash="#e5eddf",
                  hairline="#d9d8cc", plate="#f1efe3", plate_ink="#514f43"),
    "dark": dict(paper="#1a1c19", card="#222521", ink="#f1eee3", muted="#bdbfb2",
                 accent="#ffac89", accent_wash="#493127", sage="#b2d6af", sage_wash="#303e32",
                 hairline="#41453d", plate="#2a2d27", plate_ink="#d9d6c8"),
}
ART = dict(elephant_fill="#c0c2ad", elephant_ink="#41483c", fish_fill="#eba46e",
           fish_ink="#82492d", ivory="#f9f2d9")

# --- real data -------------------------------------------------------------------------

# Test count on the v2-engine branch after each merge, and who built that step.
TEST_GROWTH = [
    ("Task 1", 5, "codex"), ("Tasks 2+5", 34, "codex"), ("Task 3", 40, "codex"),
    ("Task 4", 50, "gemini"), ("Task 7", 59, "gemini"), ("Task 6", 71, "gemini"),
    ("Task 9", 79, "gemini"), ("Task 8", 96, "gemini"), ("Review fixes", 120, "gemini"),
    ("Claude review", 126, "claude"), ("Real-CLI fixes", 129, "claude"),
]
# Bugs fixed before v2 shipped, by the stage that caught them.
BUGS_CAUGHT = [
    ("Claude's test runs between tasks", 8),
    ("Gemini goldfish review", 8),
    ("Claude's independent review", 5),
    ("Real-CLI end-to-end run", 5),
    ("CI on Linux + macOS", 1),
]
CI_OS = ["Windows", "Ubuntu", "macOS"]
CI_PY = ["3.11", "3.12", "3.13"]
WHO = {"codex": "Codex (ChatGPT)", "gemini": "Gemini (agy)", "claude": "Claude"}


def svg(width: int, height: int, body: str, title: str, desc: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="t d">\n'
        f"<title id=\"t\">{escape(title)}</title>\n<desc id=\"d\">{escape(desc)}</desc>\n{body}\n</svg>\n"
    )


def text(x, y, s, *, size=13, fill, weight=400, anchor="start", family=SANS, extra=""):
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{extra}>{escape(str(s))}</text>')


def bar_h(x, y, w, h, fill, r=4):
    """Horizontal bar anchored at the baseline (left), rounded only at the data end."""
    r = min(r, w, h / 2)
    return (f'<path d="M{x},{y} H{x + w - r} Q{x + w},{y} {x + w},{y + r} V{y + h - r} '
            f'Q{x + w},{y + h} {x + w - r},{y + h} H{x} Z" fill="{fill}"/>')


def card(width, height, c):
    return f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{c["surface"]}" stroke="{c["border"]}"/>'


# --- charts ----------------------------------------------------------------------------

def chart_tests(mode: str) -> str:
    c = THEMES[mode]
    W, H = 880, 360
    left, right, top, bottom = 64, 30, 92, 300
    ymax = 140
    n = len(TEST_GROWTH)
    step = (W - left - right) / (n - 1)
    X = lambda i: left + i * step
    Y = lambda v: bottom - (bottom - top) * v / ymax
    out = [card(W, H, c),
           text(28, 38, "Test suite growth while v2 was built", size=17, weight=650, fill=c["text"]),
           text(28, 60, "Tests on the v2-engine branch after each merge · marker color = who built that step",
                size=12.5, fill=c["text2"])]
    for v in (0, 35, 70, 105, 140):
        out.append(f'<line x1="{left}" x2="{W - right}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="{c["grid"]}" stroke-width="1"/>')
        out.append(text(left - 10, Y(v) + 4, v, size=11, fill=c["muted"], anchor="end"))
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, (_, v, _) in enumerate(TEST_GROWTH))
    out.append(f'<polyline points="{pts}" fill="none" stroke="{c["line"]}" stroke-width="2" stroke-linejoin="round"/>')
    for i, (label, v, who) in enumerate(TEST_GROWTH):
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(v):.1f}" r="5" fill="{c[who]}" stroke="{c["surface"]}" stroke-width="2">'
                   f'<title>{escape(label)}: {v} tests ({WHO[who]})</title></circle>')
        out.append(f'<text x="{X(i):.1f}" y="{bottom + 18}" font-family="{SANS}" font-size="10.5" fill="{c["muted"]}" '
                   f'text-anchor="end" transform="rotate(-28 {X(i):.1f} {bottom + 18})">{escape(label)}</text>')
    # selective direct labels: first and last value
    out.append(text(X(0) + 10, Y(5) - 8, "5", size=12, weight=650, fill=c["text"]))
    out.append(text(X(n - 1), Y(129) - 14, "129 tests", size=12.5, weight=650, fill=c["text"], anchor="end"))
    # legend (3 identities)
    lx = W - right - 330
    for k, who in enumerate(("codex", "gemini", "claude")):
        x = lx + k * 112
        out.append(f'<circle cx="{x}" cy="34" r="5" fill="{c[who]}"/>')
        out.append(text(x + 10, 38, WHO[who], size=12, fill=c["text2"]))
    return svg(W, H, "\n".join(out), "Test suite growth while delegate v2 was built",
               "Tests grew from 5 after Task 1 to 129 at release. Codex built Tasks 1-3 and 5, Gemini built "
               "Tasks 4 and 6-9 plus the first review-fix pass, Claude made the last two fix rounds.")


def chart_bugs(mode: str) -> str:
    c = THEMES[mode]
    W, H = 880, 300
    label_w, left, right, top = 260, 290, 70, 92
    row, bar = 40, 18
    vmax = 8
    total = sum(v for _, v in BUGS_CAUGHT)
    out = [card(W, H, c),
           text(28, 38, f"{total} bugs caught before v2 shipped, by where they were caught", size=17, weight=650, fill=c["text"]),
           text(28, 60, "All fixed before release. Most were caught by a check other than the code's author.",
                size=12.5, fill=c["text2"])]
    span = W - left - right
    for i, (label, v) in enumerate(BUGS_CAUGHT):
        y = top + i * row
        out.append(text(left - 16, y + bar / 2 + 4.5, label, size=13, fill=c["text"], anchor="end"))
        w = span * v / vmax
        out.append(bar_h(left, y, w, bar, c["codex"]) .replace("/>", f"><title>{escape(label)}: {v}</title></path>"))
        out.append(text(left + w + 8, y + bar / 2 + 4.5, v, size=13, weight=650, fill=c["text"]))
    return svg(W, H, "\n".join(out), "Bugs caught before delegate v2 shipped",
               "; ".join(f"{l}: {v}" for l, v in BUGS_CAUGHT) + f". Total {total}.")


def ci_matrix(mode: str) -> str:
    c = THEMES[mode]
    W, H = 560, 262
    left, top, cw, ch = 150, 98, 120, 40
    out = [card(W, H, c),
           text(28, 38, "CI: 9 of 9 jobs pass", size=17, weight=650, fill=c["text"]),
           text(28, 60, "GitHub Actions · pytest · 129 tests per job", size=12.5, fill=c["text2"])]
    for j, py in enumerate(CI_PY):
        out.append(text(left + j * cw + cw / 2, top - 8, f"Python {py}", size=12, fill=c["text2"], anchor="middle"))
    for i, os_ in enumerate(CI_OS):
        y = top + i * (ch + 8)
        out.append(text(left - 16, y + ch / 2 + 4.5, os_, size=13, fill=c["text"], anchor="end"))
        for j in range(len(CI_PY)):
            x = left + j * cw + 6
            out.append(f'<rect x="{x}" y="{y}" width="{cw - 12}" height="{ch}" rx="8" fill="{c["good_wash"]}"/>')
            out.append(f'<path d="M{x + 30},{y + 21} l6,6 l11,-13" fill="none" stroke="{c["good"]}" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>')
            out.append(text(x + 56, y + ch / 2 + 4.5, "pass", size=12.5, weight=600, fill=c["text"]))
    return svg(W, H, "\n".join(out), "CI matrix: 9 of 9 jobs pass",
               "Windows, Ubuntu and macOS, each on Python 3.11, 3.12 and 3.13: all nine jobs pass.")


# --- banner + architecture ------------------------------------------------------------

def _symbols() -> dict[str, str]:
    html = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    out = {}
    for sid in ("elephant", "goldfish", "brand"):
        m = re.search(r'<symbol id="%s"([^>]*)>(.*?)</symbol>' % sid, html, re.S)
        body = re.sub(r"<!--.*?-->", "", m.group(2), flags=re.S)
        for var, val in (("--elephant-fill", ART["elephant_fill"]), ("--elephant-ink", ART["elephant_ink"]),
                         ("--fish-fill", ART["fish_fill"]), ("--fish-ink", ART["fish_ink"]), ("--ivory", ART["ivory"])):
            body = body.replace(f"var({var})", val)
        out[sid] = f'<symbol id="{sid}"{m.group(1)}>{body}</symbol>'
    return out


def banner(mode: str) -> str:
    b = BRAND[mode]
    sym = _symbols()
    W, H = 1280, 400
    brand_sym = sym["brand"].replace('fill="currentColor"', f'fill="{b["accent"]}"')
    body = f"""<defs>{sym['elephant']}{sym['goldfish']}{brand_sym}
<pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse"><path d="M22 0H0V22" fill="none" stroke="{b['hairline']}" stroke-width=".6" opacity=".55"/></pattern></defs>
<rect width="{W}" height="{H}" rx="20" fill="{b['paper']}"/>
<rect x="690" y="40" width="550" height="320" rx="16" fill="{b['plate']}" stroke="{b['hairline']}"/>
<rect x="690" y="40" width="550" height="320" rx="16" fill="url(#grid)"/>
<use href="#elephant" x="705" y="105" width="300" height="200"/>
<g fill="none" stroke="{b['plate_ink']}" stroke-width="1.6" stroke-dasharray="5 6" opacity=".7">
  <path d="M990 175 C1040 150 1060 135 1090 128"/><path d="M990 205 C1040 230 1060 245 1090 252"/></g>
<use href="#goldfish" x="1080" y="88" width="140" height="80"/>
<use href="#goldfish" x="1080" y="214" width="140" height="80"/>
<text x="855" y="335" font-family="{MONO}" font-size="12" letter-spacing="1.6" fill="{b['plate_ink']}" text-anchor="middle">CLAUDE</text>
<text x="1150" y="186" font-family="{MONO}" font-size="12" letter-spacing="1.6" fill="{b['plate_ink']}" text-anchor="middle">CODEX</text>
<text x="1150" y="312" font-family="{MONO}" font-size="12" letter-spacing="1.6" fill="{b['plate_ink']}" text-anchor="middle">GEMINI</text>
<use href="#brand" x="72" y="74" width="40" height="40"/>
<text x="124" y="106" font-family="{SERIF}" font-size="38" fill="{b['ink']}">delegate</text>
<text x="72" y="190" font-family="{SERIF}" font-size="52" fill="{b['ink']}">One elephant.</text>
<text x="72" y="250" font-family="{SERIF}" font-size="52" font-style="italic" fill="{b['accent']}">Two goldfish.</text>
<text x="72" y="300" font-family="{SANS}" font-size="19" fill="{b['muted']}">Claude orchestrates. Codex and Gemini build and review,</text>
<text x="72" y="326" font-family="{SANS}" font-size="19" fill="{b['muted']}">on the subscriptions you already pay for.</text>"""
    return svg(W, H, body, "delegate: one elephant, two goldfish",
               "Claude (an elephant) orchestrates; Codex and Gemini (two goldfish) work and review with fresh context.")


def architecture(mode: str) -> str:
    b = BRAND[mode]
    t = THEMES[mode]
    W, H = 1100, 470

    def box(x, y, w, h, title, sub, *, fill, stroke, title_fill=None):
        lines = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>',
                 text(x + 16, y + 28, title, size=15, weight=650, fill=title_fill or b["ink"])]
        for k, s in enumerate(sub):
            lines.append(text(x + 16, y + 50 + k * 19, s, size=12.5, fill=b["muted"]))
        return "\n".join(lines)

    def arrow(d, label=None, lx=0, ly=0, color=None):
        col = color or b["muted"]
        s = f'<path d="{d}" fill="none" stroke="{col}" stroke-width="1.6" marker-end="url(#ah-{mode})"/>'
        if label:
            s += text(lx, ly, label, size=11.5, fill=b["muted"], family=MONO)
        return s

    parts = [f'<defs><marker id="ah-{mode}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
             f'<path d="M0 0 10 5 0 10z" fill="{b["muted"]}"/></marker></defs>',
             f'<rect width="{W}" height="{H}" rx="16" fill="{b["paper"]}" stroke="{b["hairline"]}"/>',
             text(32, 44, "How a task flows", size=19, weight=650, fill=b["ink"], family=SERIF),
             text(32, 68, "Claude decides; the delegate engine enforces. Nothing reaches done without evidence on the current code.",
                  size=13, fill=b["muted"]),
             # Claude
             box(32, 110, 230, 120, "Claude · the elephant", ["plans, writes briefs", "triages every 🔴/🟡", "makes the final calls"],
                 fill=b["card"], stroke=b["hairline"]),
             # engine
             box(330, 100, 300, 270, "delegate engine", ["task state on disk (resume anywhere)", "diff_hash of the scoped diff",
                 "timeouts + process-tree kill", "secret scan before anything leaves", "risk tiers: low / medium / high",
                 "done refuses without fresh evidence", "approve: human-only, real console"],
                 fill=b["accent_wash"], stroke=b["accent"], title_fill=b["accent"]),
             # workers
             box(700, 96, 360, 82, "Codex · worker", ["codex exec -s workspace-write  (sandboxed)"], fill=b["card"], stroke=b["hairline"]),
             box(700, 196, 360, 82, "Project gates", ["your lint / typecheck / tests, sequential"], fill=b["card"], stroke=b["hairline"]),
             box(700, 296, 360, 100, "Goldfish reviewers", ["Codex (read-only) + Gemini via agy (plan)",
                 "in parallel, no chat history"], fill=b["card"], stroke=b["hairline"]),
             arrow("M262 170 H322", "calls", 272, 160),
             arrow("M630 137 H692", "work", 640, 127),
             arrow("M630 237 H692", "verify", 636, 227),
             arrow("M630 337 H692", "review", 634, 327),
             arrow("M880 396 V432 Q880 440 872 440 H24 Q16 440 16 432 V178 Q16 170 24 170 H30", "🔴 🟡 🟢 findings → Claude triages (fix | contest)", 380, 432),
             f'<rect x="32" y="262" width="230" height="96" rx="12" fill="{b["sage_wash"]}" stroke="{b["sage"]}" stroke-width="1.2"/>',
             text(48, 290, "You", size=15, weight=650, fill=b["sage"]),
             text(48, 312, "delegate approve <id>", size=12.5, fill=b["muted"], family=MONO),
             text(48, 331, "for high-risk changes", size=12.5, fill=b["muted"]),
             arrow("M262 310 H322", None)]
    return svg(W, H, "\n".join(parts), "delegate architecture",
               "Claude calls the delegate engine. The engine runs the Codex worker, the project gates and the two goldfish "
               "reviewers; findings go back to Claude for triage; high-risk changes need the human's approval.")


# --- terminal demo (real output from the first end-to-end run) -----------------------

DEMO = [
    ("cmd", "delegate task new slug --title \"slugify helper\" --files slug.py test_slug.py"),
    ("cmd", "delegate verify slug"),
    ("ok", "PASS test"),
    ("cmd", "delegate review slug --goal \"lowercase ASCII URL slug, with a test\""),
    ("warn", "codex: unavailable"),
    ("out", "gemini: ran"),
    ("finding", "gemini-3-1 🟡 slug.py:5 — accents become hyphens, not ASCII — \"café\" → \"caf\""),
    ("cmd", "delegate triage slug gemini-3-1 fix \"normalize accents to ASCII\""),
    ("dim", "# fix test-first → verify → review again (round is clean)"),
    ("cmd", "delegate status slug"),
    ("out", "phase: ready · risk: medium · next: delegate done slug"),
    ("cmd", "delegate done slug"),
    ("dim", "# exit 0: task marked done, metrics recorded"),
]


def terminal() -> str:
    W, lh, top = 980, 25, 64
    H = top + lh * len(DEMO) + 26
    col = dict(bg="#222620", bar="#2b3029", edge="#454b40", text="#e6e8d9", muted="#b4bcaa",
               orange="#f3b28b", green="#b0d79c", yellow="#e3c27a")
    out = [f'<rect width="{W}" height="{H}" rx="12" fill="{col["bg"]}" stroke="{col["edge"]}"/>',
           f'<path d="M12 .5 H{W - 12} a11.5 11.5 0 0 1 11.5 11.5 V38 H.5 V12 A11.5 11.5 0 0 1 12 .5Z" fill="{col["bar"]}"/>',
           *(f'<circle cx="{24 + k * 18}" cy="19" r="5.5" fill="{f}"/>' for k, f in enumerate(("#d9705a", "#e3c27a", "#8fbf7a"))),
           text(W / 2, 24, "delegate · first real run, 2026-11-27 (output condensed)", size=12.5, fill=col["muted"], anchor="middle", family=MONO)]
    for i, (kind, s) in enumerate(DEMO):
        y = top + i * lh
        if kind == "cmd":
            out.append(f'<text x="22" y="{y}" font-family="{MONO}" font-size="14"><tspan fill="{col["orange"]}">❯ </tspan>'
                       f'<tspan fill="{col["text"]}">{escape(s)}</tspan></text>')
        else:
            fill = {"ok": col["green"], "warn": col["yellow"], "out": col["text"], "dim": col["muted"],
                    "finding": col["yellow"]}[kind]
            out.append(text(40, y, s, size=13.5, fill=fill, family=MONO))
    return svg(W, H, "\n".join(out), "Terminal: the first real delegate run",
               "Real output: verify passes, Codex is unavailable (quota), Gemini finds that accents become hyphens, "
               "Claude triages and fixes it test-first, a clean round follows and done passes.")


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    for mode in ("light", "dark"):
        for name, fn in (("banner", banner), ("architecture", architecture), ("chart-tests", chart_tests),
                         ("chart-bugs", chart_bugs), ("ci-matrix", ci_matrix)):
            (ASSETS / f"{name}-{mode}.svg").write_text(fn(mode), encoding="utf-8")
    (ASSETS / "terminal-demo.svg").write_text(terminal(), encoding="utf-8")
    print("wrote", len(list(ASSETS.glob("*.svg"))), "SVGs to", ASSETS)


if __name__ == "__main__":
    main()
