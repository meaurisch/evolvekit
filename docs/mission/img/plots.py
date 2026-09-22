"""The report's two figures, as SVG, from the run directories. Standard library only.

    python docs/mission/img/plots.py runs/pyvrp-hard-1 runs/pyvrp-hard-llm docs/mission/img

Figure 1: best-so-far (percent of the defaults on the search's own seed) by
wall-clock hour, both runs. Figure 2: every confirmation's mean and 95 %
interval, on one axis.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

run1, run2, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
PY = sys.executable

FONT = "font-family='ui-sans-serif, system-ui, sans-serif' font-size='12'"
INK, GRID, RED, BLUE = "#1f2328", "#d0d7de", "#c8402a", "#2a63c8"


def series(run: Path) -> list[dict]:
    text = subprocess.run([PY, "-m", "evolvekit", "status", "--run-dir", str(run), "--json"],
                          capture_output=True, text=True, check=True).stdout
    return json.loads(text)["progress"]["series"]


def comparison(run: Path, label: str) -> dict:
    return json.loads((run / "confirm" / label / "comparison.json").read_text(encoding="utf-8"))


# -- figure 1: best-so-far by hour ------------------------------------------

def best_so_far(s1: list[dict], s2: list[dict]) -> str:
    W, H, L, R, T, B = 640, 300, 56, 16, 20, 40
    hours = max(p["elapsed_s"] for p in s1 + s2) / 3600
    lo, hi = 97.4, 100.2

    def x(h): return L + (W - L - R) * h / hours
    def y(v): return T + (H - T - B) * (hi - v) / (hi - lo)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' width='{W}' height='{H}' {FONT} fill='{INK}'>",
             "<rect width='100%' height='100%' fill='white'/>"]
    for v in (97.5, 98.0, 98.5, 99.0, 99.5, 100.0):
        parts.append(f"<line x1='{L}' y1='{y(v):.1f}' x2='{W - R}' y2='{y(v):.1f}' stroke='{GRID}'/>")
        parts.append(f"<text x='{L - 6}' y='{y(v) + 4:.1f}' text-anchor='end'>{v:.1f}</text>")
    for h in range(0, int(hours) + 1, 2):
        parts.append(f"<text x='{x(h):.1f}' y='{H - B + 16}' text-anchor='middle'>{h}</text>")
    parts.append(f"<text x='{(L + W - R) / 2:.0f}' y='{H - 6}' text-anchor='middle'>wall-clock hours</text>")
    parts.append(f"<text transform='translate(14,{(T + H - B) / 2:.0f}) rotate(-90)' text-anchor='middle'>best, % of the defaults' cost (own seed)</text>")
    for s, colour, name in ((s1, RED, "run 1: model-free"), (s2, BLUE, "run 2: model among the operators")):
        pts = []
        prev = None
        for p in s:
            h, v = p["elapsed_s"] / 3600, p["best_objective"]
            if prev is not None:
                pts.append(f"{x(h):.1f},{y(prev):.1f}")  # a step: the best changes when a generation ends
            pts.append(f"{x(h):.1f},{y(v):.1f}")
            prev = v
        parts.append(f"<polyline points='{' '.join(pts)}' fill='none' stroke='{colour}' stroke-width='2'/>")
        for p in s:
            parts.append(f"<circle cx='{x(p['elapsed_s'] / 3600):.1f}' cy='{y(p['best_objective']):.1f}' r='2.5' fill='{colour}'/>")
        last = s[-1]
        parts.append(f"<text x='{x(last['elapsed_s'] / 3600) - 4:.1f}' y='{y(last['best_objective']) - 6:.1f}' text-anchor='end' fill='{colour}'>{last['best_objective']:.2f}</text>")
    parts.append(f"<rect x='{L + 10}' y='{T + 6}' width='12' height='3' fill='{RED}'/><text x='{L + 28}' y='{T + 12}'>run 1: model-free operators</text>")
    parts.append(f"<rect x='{L + 10}' y='{T + 24}' width='12' height='3' fill='{BLUE}'/><text x='{L + 28}' y='{T + 30}'>run 2: a model among the operators</text>")
    parts.append("</svg>")
    return "\n".join(parts)


# -- figure 2: every confirmation on one axis --------------------------------

def forest(rows: list[tuple[str, dict, str]]) -> str:
    W, L, R, T, RH = 700, 300, 16, 24, 26
    H = T + RH * len(rows) + 44
    lo, hi = -1.5, 3.5

    def x(v): return L + (W - L - R) * (v - lo) / (hi - lo)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {W} {H}' width='{W}' height='{H}' {FONT} fill='{INK}'>",
             "<rect width='100%' height='100%' fill='white'/>"]
    for v in (-1, 0, 1, 2, 3):
        parts.append(f"<line x1='{x(v):.1f}' y1='{T - 8}' x2='{x(v):.1f}' y2='{H - 34}' stroke='{INK if v == 0 else GRID}'/>")
        parts.append(f"<text x='{x(v):.1f}' y='{H - 18}' text-anchor='middle'>{v:+d} %</text>")
    parts.append(f"<text x='{(L + W - R) / 2:.0f}' y='{H - 4}' text-anchor='middle'>mean improvement over the reference, 95 % interval (paired by instance)</text>")
    for i, (name, summary, colour) in enumerate(rows):
        cy = T + RH * i + RH / 2
        parts.append(f"<text x='{L - 8}' y='{cy + 4:.1f}' text-anchor='end'>{name}</text>")
        if summary.get("ci95"):
            a, b = summary["ci95"]
            parts.append(f"<line x1='{x(a):.1f}' y1='{cy:.1f}' x2='{x(b):.1f}' y2='{cy:.1f}' stroke='{colour}' stroke-width='2'/>")
            for v in (a, b):
                parts.append(f"<line x1='{x(v):.1f}' y1='{cy - 5:.1f}' x2='{x(v):.1f}' y2='{cy + 5:.1f}' stroke='{colour}' stroke-width='2'/>")
        parts.append(f"<circle cx='{x(summary['mean']):.1f}' cy='{cy:.1f}' r='4' fill='{colour}'/>")
        parts.append(f"<text x='{x(hi) + 2:.1f}' y='{cy + 4:.1f}' font-size='11'>n={summary['n']}</text>")
    parts.append("</svg>")
    return "\n".join(parts)


s1, s2 = series(run1), series(run2)
(out / "best-so-far.svg").write_text(best_so_far(s1, s2), encoding="utf-8", newline="\n")

rows = []
for run, colour, tag in ((run1, RED, "run 1"), (run2, BLUE, "run 2")):
    for label, what in (("validation", "validation, seeds 101-102 (finalist)"), ("test", "test, 10 tuning instances, seeds 1001-1003"),
                        ("fresh", "test, 4 fresh instances, seeds 1001-1003")):
        c = comparison(run, label)
        finalist = c["candidates"][0] if label != "validation" else max(
            c["per_candidate"], key=lambda k: c["per_candidate"][k]["summary"]["mean"] or -1e9)
        rows.append((f"{tag} {finalist}: {what}", c["per_candidate"][finalist]["summary"], colour))
c = comparison(run2, "head-to-head")
rows.append((f"run 2 finalist against run 1 finalist, 14 instances, seeds 2001-2003", c["per_candidate"][c["candidates"][0]]["summary"], INK))
(out / "confirmations.svg").write_text(forest(rows), encoding="utf-8", newline="\n")
print("written", out / "best-so-far.svg", out / "confirmations.svg")
