"""The app's page (`evolvekit/app/static/`): the design's tokens, the word
rules, and that every file the page loads exists and ships with the package."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "evolvekit" / "app" / "static"

# The approved token list (hand-over pack, round 12): name -> (light, dark).
COLOURS = {
    "surface-page": ("#FDFBFC", "#1A1219"), "surface-panel": ("#F5EEF2", "#261B24"),
    "surface-raised": ("#FFFFFF", "#2E212B"), "surface-wash": ("#FBE4EE", "#45263A"),
    "ink-1": ("#1A1218", "#F7EEF3"), "ink-2": ("#5E525A", "#BFAEB9"), "ink-3": ("#73666E", "#A8969F"),
    "line-rule": ("#E2D7DD", "#3F2F3B"), "line-edge": ("#8C7F87", "#86737F"),
    "accent": ("#FF8A3D", "#FF8A3D"), "accent-ink": ("#A84400", "#FF8A3D"), "accent-line": ("#C65300", "#FF8A3D"),
    "on-accent": ("#1A1218", "#1A1219"), "accent-band": ("#FFE8D9", "#4A2B22"),
    "good": ("#1D6B40", "#7CD39C"), "good-tint": ("#E1F1E6", "#1D3326"),
    "warning": ("#7A5400", "#F1C55A"), "warning-tint": ("#FDF0CF", "#3A2D14"),
    "critical": ("#B02A2A", "#FF8F80"), "critical-tint": ("#FBE6DF", "#4A1F1C"),
    "focus": ("#A84400", "#FF8A3D"), "pepper-rim": ("transparent", "#EADBE3"),
}
OTHER = ["type-display", "type-title", "type-heading", "type-body", "type-small", "type-label", "type-data",
         *(f"space-{i}" for i in range(1, 9)), "radius-0", "radius-1", "radius-2", "radius-full", "elev-1", "elev-2",
         "dur-fast", "dur-base", "dur-slow", "dur-ambient", "ease-standard", "ease-enter", "ease-exit"]


def _blocks(css: str) -> dict[str, dict[str, str]]:
    """selector -> {variable: value} for every rule that sets variables."""
    out: dict[str, dict[str, str]] = {}
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        found = dict(re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", body))
        if found:
            out.setdefault(selector.strip(), {}).update({k: v.strip() for k, v in found.items()})
    return out


def test_every_token_is_a_css_variable_in_both_themes():
    blocks = _blocks((STATIC / "css" / "tokens.css").read_text(encoding="utf-8"))
    light, dark, follows = blocks[":root"], blocks[':root[data-theme="dark"]'], blocks[':root:not([data-theme="light"])']
    for name, (lv, dv) in COLOURS.items():
        assert light[name].upper() == lv.upper(), name
        assert dark[name].upper() == dv.upper(), name
        assert follows[name].upper() == dv.upper(), f"{name}: the dark theme the computer asks for differs from ?theme=dark"
    for name in OTHER:
        assert name in light, name
    assert {"elev-1", "elev-2"} <= set(dark)


def test_reduced_motion_stops_everything_ambient():
    css = (STATIC / "css" / "tokens.css").read_text(encoding="utf-8")
    reduced = css[css.index("prefers-reduced-motion"):]
    block = _blocks(reduced)[":root"]
    assert block["dur-ambient"] == "0ms" and block["dur-base"] == "0ms" and block["dur-slow"] == "120ms"


@pytest.mark.parametrize("font", ["YoungSerif-Regular.woff2", "Overpass-Variable.woff2", "OverpassMono-Variable.woff2"])
def test_the_fonts_ship_with_their_licences(font):
    path = STATIC / "fonts" / font
    assert path.read_bytes()[:4] == b"wOF2"
    licence = STATIC / "fonts" / ("OFL-" + font.split("-")[0] + ".txt")
    assert "SIL OPEN FONT LICENSE" in licence.read_text(encoding="utf-8").upper()
