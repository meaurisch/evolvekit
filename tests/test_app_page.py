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


# ---------------------------------------------------------------------------
# the page's scripts
# ---------------------------------------------------------------------------

SCRIPTS = sorted((STATIC / "js").rglob("*.js"))
SOURCE = "\n".join(p.read_text(encoding="utf-8") for p in SCRIPTS)

COMPONENTS = [
    "appBar", "button", "notice", "toast", "undoBar", "studyCard", "dropZone", "kindChoice", "caseStrip", "summaryRow",
    "planBar", "tuneDrawer", "settingRow", "infoTip", "goalPicker", "serviceChoice", "numberUnit", "statusStrip",
    "runTimeline", "intervalStrip", "verdictMark", "triesPlot", "casesView", "settingsPanel", "hoverCard", "chosenChip",
    "companion", "assistantDrawer", "proposalCard", "chatBubble", "compareView", "casePair", "runList", "lockedPage",
    "viewTabs", "textInput", "pepper", "mark",
]


@pytest.mark.parametrize("name", COMPONENTS)
def test_every_component_of_the_design_is_one_function_with_its_name(name):
    assert re.search(rf"(EK\.{name}\s*=|function {name}\s*\()", SOURCE), f"{name}: no function of that name in static/js"


def test_the_page_loads_only_files_that_exist_and_ship():
    import tomllib

    page = (STATIC / "app.html").read_text(encoding="utf-8")
    loaded = re.findall(r'(?:src|href)="(static/[^"]+)"', page)
    assert loaded, "the page loads its scripts and styles"
    globs = tomllib.loads((STATIC.parents[2] / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["evolvekit"]
    for rel in loaded:
        path = STATIC / rel[len("static/"):]
        assert path.is_file(), rel
        inside = path.relative_to(STATIC.parents[1]).as_posix()
        assert any(Path(inside).match(g) for g in globs), f"{rel} is not in pyproject's package-data"
    for script in SCRIPTS:
        assert f"static/js/{script.relative_to(STATIC / 'js').as_posix()}" in loaded, f"{script.name} is never loaded"
    assert "http://" not in page and "https://" not in page, "nothing loads from the internet"
    for css in (STATIC / "css").glob("*.css"):
        assert not re.search(r"url\(\s*['\"]?https?:", css.read_text(encoding="utf-8")), css.name


def test_every_script_parses():
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    for script in SCRIPTS:
        done = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
        assert done.returncode == 0, f"{script.name}: {done.stderr}"


BANNED = ["KPI", "seed", "generation", "instance", "config", "baseline", "confidence interval", "p-value"]
"""Words never on screen (the design's word rules); SQL only inside the expert box."""


def _without_code(text: str) -> str:
    """A template literal with its ${...} parts (code, braces nested) taken out."""
    out, depth, i = [], 0, 0
    while i < len(text):
        if depth == 0 and text.startswith("${", i):
            depth, i = 1, i + 2
            continue
        if depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
            if depth == 0:
                out.append(" ")
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _visible_strings(source: str) -> list[str]:
    """The string literals of the page's scripts that hold words: what can reach the screen."""
    found = re.findall(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`|' + r"'((?:[^'\\\n]|\\.)*)'", source)
    out = []
    for a, b, c in found:
        text = _without_code(a or b or c)
        text = re.sub(r"<[^>]*>", " ", text)
        if re.search(r"[A-Za-z]{3,} [A-Za-z]{2,}", text):
            out.append(text)
    return out


@pytest.mark.parametrize("word", BANNED)
def test_no_banned_word_reaches_the_screen(word):
    strings = _visible_strings(SOURCE) + _visible_strings((STATIC / "app.html").read_text(encoding="utf-8"))
    hits = [s for s in strings if re.search(rf"\b{re.escape(word)}s?\b", s, re.I)]
    assert not hits, f"{word!r} on screen: {hits[:3]}"


def test_sql_shows_only_in_the_expert_box():
    hits = [s for s in _visible_strings(SOURCE) if re.search(r"\bSQL\b", s)]
    allowed = ("for experts", "SQL returning", "(SQL", "the SQL")
    assert hits and all(any(a in s for a in allowed) for s in hits), hits


def test_every_route_the_old_page_used_is_still_reachable():
    """Everything the app could do stays reachable: each API route has a caller in the page."""
    from evolvekit.app import api

    unused = {"/api/harnesses/{}"}  # read by tests and tools, never by the page (as before the redesign)
    for method, pattern, _ in api.ROUTES:
        literal = re.sub(r"\(\?P<\w+>\[\^/\]\+\)", "{}", pattern.pattern)
        if literal in unused:
            continue
        words = [part for part in literal.split("/") if part and part != "{}" and part not in ("api", "studies")]
        if not words:  # /api/studies/{slug}: the study itself, read and changed through EK.studyPath(slug)
            assert "EK.studyPath(" in SOURCE
            continue
        last = words[-1]
        assert re.search(rf"[\"'/]{re.escape(last)}[\"'/?]", SOURCE), f"{method} {literal}: nothing in the page calls it"


# ---------------------------------------------------------------------------
# the verdict: rendered from the results document, never composed on the page
# ---------------------------------------------------------------------------

STUDY_PAGE = (STATIC / "js" / "pages" / "study.js").read_text(encoding="utf-8")


def _strip_branches(source: str) -> str:
    """The body of the status strip's `strip()`: where the headline is chosen."""
    start = source.index("function strip()")
    return source[start:source.index("function foundWords()", start)]


def test_the_study_page_headline_is_the_verdict_line_and_advice():
    """Design 3.4: the page's headline and the sentence under it are the
    results document's `final.line` and `final.advice`, verbatim."""
    body = _strip_branches(STUDY_PAGE)
    finished = body[body.index('d === "use"'):]
    assert "head: verdictHead" in finished and "sub: verdictSub" in finished
    assert "function verdictHead" in STUDY_PAGE and "f.line" in STUDY_PAGE[STUDY_PAGE.index("function verdictHead"):]
    assert "f.advice" in STUDY_PAGE[STUDY_PAGE.index("function verdictSub"):]
    for old in ("EK.better(f.mean)", "on one case", "One case gives no range, so no answer.", "but not sure enough"):
        assert old not in finished, f"the page composes its own verdict again: {old!r}"


def test_no_figure_on_the_study_page_says_better():
    """The same number, one way: one decimal, "lower"/"higher" by the measure's
    direction, in the chip and the held-back table; "better" only inside the line."""
    parts = (STATIC / "js" / "parts.js").read_text(encoding="utf-8")
    chip = parts[parts.index("EK.chosenChip = function"):parts.index("EK.hoverCard = function")]
    assert "EK.better" not in chip and "on the cases it learned from, not checked" in chip
    assert "EK.change(x.change_pct" in STUDY_PAGE, "the held-back table: one decimal, the measure's direction"
    assert "mean of the paired runs" in STUDY_PAGE, "the table's column says what its figure is"
    assert "improvement_pct" in STUDY_PAGE, "the chip of the best: the document's learned_from figure"


def test_the_change_words_round_like_the_report():
    """`EK.change` in core.js, run under node: the report's `_change` words."""
    import json
    import shutil
    import subprocess

    from evolvekit.app.report import _change

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    cases = [(4.78, "lower"), (-7.43, "lower"), (7.46, "higher"), (0.01, "lower"), (None, "lower")]
    script = ("var window = {}, document = {addEventListener: function () {}}, navigator = {}, location = {href: 'http://127.0.0.1/', hash: '', search: ''};\n"
              + core + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(function (c) { return window.EK.change(c[0], c[1]); })));")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [_change(v, d) for v, d in cases]
