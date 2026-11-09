"""The app's page (`evolvekit/app/static/`): the design's tokens, the word
rules, and that every file the page loads exists and ships with the package."""

from __future__ import annotations

import json
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
    heads, subs = re.findall(r"head: ([^,]+),", finished), re.findall(r"sub: ([^,]+),", finished)
    assert len(heads) >= 7 and set(heads) == {"verdictHead(f)"}, heads
    assert len(subs) == len(heads) and set(subs) == {"verdictSub(f)"}, subs
    assert "function verdictHead" in STUDY_PAGE and "f.line" in STUDY_PAGE[STUDY_PAGE.index("function verdictHead"):]
    assert "f.advice" in STUDY_PAGE[STUDY_PAGE.index("function verdictSub"):]
    for old in ("EK.better(f.mean)", "on one case", "One case gives no range, so no answer.", "but not sure enough"):
        assert old not in finished, f"the page composes its own verdict again: {old!r}"


def test_the_home_card_speaks_the_verdicts_words():
    """One world: a finished card's lines are the verdict's `line` and `advice`
    (the home document's `verdict`), its label the state line's words; the card
    composes no verdict of its own."""
    words = (STATIC / "js" / "words.js").read_text(encoding="utf-8")
    card = words[words.index("EK.cardOf = function"):]
    finished = card[card.index('s.kind === "finished"'):card.index('s.kind === "stopped"')]
    assert "v.line" in finished and "v.advice" in finished and "v.says" in finished, finished
    assert "s.verdict" in finished
    for old in ("EK.answerWords", "EK.verdictWord", "EK.better", "on one case", "No answer"):
        assert old not in finished, f"the card composes its own verdict again: {old!r}"


def test_no_figure_on_the_study_page_says_better():
    """The same number, one way: one decimal, "lower"/"higher" by the measure's
    direction, in the chip and the held-back table; "better" only inside the line."""
    parts = (STATIC / "js" / "parts.js").read_text(encoding="utf-8")
    chip = parts[parts.index("EK.chosenChip = function"):parts.index("EK.hoverCard = function")]
    assert "EK.better" not in chip and "on the cases it learned from, not checked" in chip
    assert "EK.change(x.change_pct" in STUDY_PAGE, "the held-back table: one decimal, the measure's direction"
    assert "mean of the paired runs" in STUDY_PAGE, "the table's column says what its figure is"
    drawer = STUDY_PAGE[STUDY_PAGE.index("function comboDrawer"):STUDY_PAGE.index("function runsDrawer")]
    assert "EK.better" not in drawer, "the combination drawer: one decimal and the direction, in every row"
    assert "Details shows" not in STUDY_PAGE and "${r.final.line}" not in STUDY_PAGE, "the line is the headline, once"


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


def test_the_verdict_words_are_escaped_once():
    """`EK.figures` (the headline's path), run under node: an apostrophe is one
    `&#39;`, never an entity whose digits were wrapped as a figure, and markup in
    the words stays text."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    core = (STATIC / "js" / "core.js").read_text(encoding="utf-8")
    lines = ["at worst 0.3 % worse than your starting point (Demo tour's own defaults).", "<b>&</b> 12"]
    script = ("var window = {}, document = {addEventListener: function () {}}, navigator = {}, location = {href: 'http://127.0.0.1/', hash: '', search: ''};\n"
              + core + "\nconsole.log(JSON.stringify(" + json.dumps(lines)
              + ".map(function (t) { var E = window.EK; return String(E.html`<p>${E.figures(t)}</p>`); })));")
    done = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    tour, markup = json.loads(done.stdout)
    assert tour == ('<p>at worst <span class="fig">0.3 %</span> worse than your starting point'
                    " (Demo tour&#39;s own defaults).</p>"), tour
    assert markup == '<p>&lt;b&gt;&amp;&lt;/b&gt; <span class="fig">12</span></p>', markup


# ---------------------------------------------------------------------------
# persona round 1 (code studies): a run that starts or ends while the page is
# open, one best so far, a code study's words, no figure where no run differed,
# nothing pushed off the right edge
# ---------------------------------------------------------------------------

COMPARE_PAGE = (STATIC / "js" / "pages" / "compare.js").read_text(encoding="utf-8")
NODE_GLOBALS = ("var window = {}, document = {addEventListener: function () {}}, navigator = {}, "
                "location = {href: 'http://127.0.0.1/', hash: '', search: ''};")


def _page_function(js: str, name: str) -> str:
    """The source of one of a page's two-space-indented functions."""
    start = js.index(f"  function {name}(")
    return js[start:js.index("\n  }\n", start) + 4]


def _node(*parts: str):
    """Run the page's own scripts and `parts` under node; the last part prints JSON."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    scripts = [(STATIC / "js" / name).read_text(encoding="utf-8") for name in ("core.js", "parts.js", "words.js")]
    script = "\n".join([NODE_GLOBALS, *scripts, "var EK = window.EK, html = EK.html;", *parts])
    done = subprocess.run([node, "-"], input=script, capture_output=True, encoding="utf-8", check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_study_page_notices_a_new_run_and_a_runs_end():
    """Persona round 1, F4: after a bake-off started from the terminal the Study page kept the first
    run's verdict and "Past runs (1)", and its runs drawer said "running · 10 min left" of a finished
    run. The page asks the runs route while it is open, live run or not; it moves to a new run when it
    showed the newest one; the header, the notices and an open runs drawer follow every change."""
    assert 'EK.studyPath(st.slug, "/runs")' in STUDY_PAGE
    assert "if (!st.status || !isLive()) return;" not in STUDY_PAGE, "the poll no longer stops once no run is live"
    watch = _page_function(STUDY_PAGE, "watchRuns")
    assert "newerRun(" in watch and "follow(" in watch and 'EK.drawerKind() === "runs"' in watch
    assert "study-head" in watch and "notices()" in watch
    drawer = _page_function(STUDY_PAGE, "runsDrawer")
    assert 'kind: "runs"' in drawer and "watchRuns(" in drawer, "the drawer opens on what is known, then brings it up to date"
    out = _node(_page_function(STUDY_PAGE, "newerRun"), _page_function(STUDY_PAGE, "runsKey"), """
      var a = {id: "a", kind: "finished", line: "finished · not proven"}, b = {id: "b", kind: "running", line: "running · 1 min left"};
      process.stdout.write(JSON.stringify({
        follows: newerRun([a], [a, b], "a"), same: newerRun([a, b], [a, b], "b"),
        older: newerRun([{id: "0"}, a], [{id: "0"}, a, b], "0"), first: newerRun([], [a], ""),
        ended: runsKey([a, b]) !== runsKey([a, Object.assign({}, b, {kind: "finished", line: "finished · probably better"})]),
        still: runsKey([a, b]) === runsKey([a, Object.assign({}, b)])}));""")
    assert out == {"follows": "b", "same": None, "older": None, "first": None, "ended": True, "still": True}, out


def test_the_strip_shows_the_one_best_so_far_in_study_status_words():
    """Persona round 1, F3: the strip said "Best so far: 8.99 % better" (the Tries digest's figure) while
    `study status` said "0.4 % lower". The strip reads the status document's `best` -- `pct` to decide,
    `says` to say it -- and labels it as the chip does; the Tries line is labelled as what it is."""
    best = _page_function(STUDY_PAGE, "bestPct")
    assert "st.status.best" in best and "st.tries" not in best and "t.best" not in best
    body = _strip_branches(STUDY_PAGE)
    assert "bestSays()" in body and "on the cases it learned from" in _page_function(STUDY_PAGE, "bestSays")
    assert "EK.better(b)" not in body and "EK.better(b2)" not in body
    words = (STATIC / "js" / "words.js").read_text(encoding="utf-8")
    card = words[words.index("EK.cardOf = function"):]
    assert "s.best_says" in card and "EK.better(s.best_pct)" not in card, "the Home card: the same words"
    plots = (STATIC / "js" / "plots.js").read_text(encoding="utf-8")
    legend = plots[plots.index("EK.triesLegend = function"):]
    assert "best so far, each case’s change averaged" in legend[:600], "the plot's line is on another base: said so"


def test_a_code_study_speaks_of_code_not_settings():
    """Persona round 1, F5: a code study showed "Settings 0", "TODAY'S SETTINGS" and a
    "SETTING / TODAY / NEW" table. Its third view is Code (what each combination changed), the Tries
    plot's line is today's code, and the hand-over shows the change against today's code."""
    out = _node("""process.stdout.write(JSON.stringify({
      code: String(EK.viewTabs("code", {tries: 3, cases: 5, code: 2}, ["tries", "cases", "code"])),
      plain: String(EK.viewTabs("tries", null))}));""")
    assert ">Code<" in out["code"] and "Settings" not in out["code"] and 'data-arg="code"' in out["code"]
    assert ">Settings<" in out["plain"], "a settings study keeps its three views"
    assert "function viewKeys" in STUDY_PAGE and "EK.viewTabs(st.view, phone ? null : counts, viewKeys())" in STUDY_PAGE
    view = _page_function(STUDY_PAGE, "codeView")
    assert "diffWords(c.diff_stat)" in view and 'act: "details"' in view and "EK.better" not in view and ".pct" not in view, \
        "the Code view lists the changes; the figures stay in Tries, on one base"
    assert 'whatOf() === "code" ? "today’s code"' in STUDY_PAGE, "the Tries plot's zero line"
    hand = _page_function(STUDY_PAGE, "codeChange")
    assert "Today’s code" in hand and "The change" in hand and "<th>Setting</th>" not in hand
    assert "codeChange(" in _page_function(STUDY_PAGE, "changedTable")
    busy = STUDY_PAGE[STUDY_PAGE.index("busy: function"):STUDY_PAGE.index("latest: function")]
    assert "today’s settings and the new ones" not in busy
    assert 'what: "settings"' not in COMPARE_PAGE and "s.what" in COMPARE_PAGE, "two runs compared: each in its own words"
    details = _page_function(STUDY_PAGE, "details")
    assert "r.how_it_ran" in details, "how much of the plan ran, where the page says how the run went"
    assert "Math.max(0, c.tried - 1)" in details and "plus " in details, "the starting point is no combination tried"
    assert 'whatOf() === "code" ? "today’s code"' in details and "today’s settings" in details


def test_no_difference_in_any_run_shows_its_line_and_no_band():
    """Persona round 1, B2: a change whose every run equals today's shows the verdict's line and no
    band or range -- the strip, Compare branches and two runs compared."""
    body = _strip_branches(STUDY_PAGE)
    keep = body[body.index('d === "keep"'):body.index('d === "worse"')]
    assert 'f.state === "same"' in keep
    lines = _page_function(STUDY_PAGE, "branchLines")
    assert "b.line" in lines and "intervalStrip" not in lines and "branchLines(acc)" in _page_function(STUDY_PAGE, "statusStrip"), \
        "a finished bake-off's strip says each branch's own line, with no band"
    out = _node(_page_function(COMPARE_PAGE, "caseName"), _page_function(COMPARE_PAGE, "caseFigures"),
                _page_function(COMPARE_PAGE, "candidatesView"), """
      var line = "No difference in any run: every run of this change gave exactly today's result.";
      var d = {held_back: {cases: 2, runs_per_case: 12}, candidates: [
        {ref: "nearest-first", commit: "95dc9c5aa", decision: "keep", same: true, line: line},
        {ref: "restart-sooner", commit: "1234567aa", decision: "keep", line: "Not proven", mean_pct: 0.4, ci95_pct: [-1, 2]}]};
      var cols = String(candidatesView(d, 260)).split('<section class="cand-col">');
      process.stdout.write(JSON.stringify({same: cols[2], other: cols[3]}));""")
    assert "No difference in any run" in out["same"] and "interval-strip" not in out["same"]
    assert "interval-strip" in out["other"]
    answer = _page_function(COMPARE_PAGE, "answer")
    assert 'f.state === "same"' in answer and "f.line" in answer
    assert 'f.state === "same"' in _page_function(COMPARE_PAGE, "rangeOf")


def test_the_strip_and_the_drawer_never_push_the_page_sideways():
    """Persona round 1, F8: the verdict strip and its drawer were cut off at the right in a narrower
    window. The strip's columns and words may shrink and wrap; the drawer's entrance never places it
    past the right edge (an entrance that does not run leaves it where it ends)."""
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    start = css.index("@keyframes slide-in")
    slide = css[start:css.index("\n", start)]
    assert "translate" not in slide, slide
    assert ".status-strip > *" in css and "min-width: 0" in css[css.index(".status-strip > *"):][:80]
    words = css[css.index(".strip-words .type-display"):][:120]
    assert "overflow-wrap: anywhere" in words


# ---------------------------------------------------------------------------
# persona round 2 (code studies): the AI's spend and pace while it searches, the
# AI's writing, counts that follow the plot, a chip and Details that end with the
# verdict, each branch case by case, the thin-check note, the runs poll's pace
# ---------------------------------------------------------------------------

SETUP_PAGE = (STATIC / "js" / "pages" / "setup.js").read_text(encoding="utf-8")


def test_the_strip_says_the_ai_spend_and_the_pace_while_a_run_searches():
    """Persona round 2, D3 and D1: the AI's spend against its cap was shown nowhere, and the plan's 11
    rounds became 2 with no word until the end. While a run searches the strip says the status
    document's `spent.line` and `pace.line` as they are (facts: no gain claimed); Details and the
    finished verdict's facts carry the spend."""
    out = _node(_page_function(STUDY_PAGE, "liveFacts"), """
      var spent = {usd: 0.42, cap_usd: 1, line: "AI: $0.42 of $1.00"};
      var pace = {rounds: 2, planned: 11, line: "At this pace about 2 of the 11 planned rounds will run."};
      process.stdout.write(JSON.stringify({
        search: liveFacts({phase: "search", spent: spent, pace: pace}),
        check: liveFacts({phase: "check", spent: spent, pace: null}),
        noai: liveFacts({phase: "search", spent: null, pace: null}),
        done: liveFacts({phase: "done", spent: spent, pace: pace})}));""")
    assert out == {"search": ["AI: $0.42 of $1.00", "At this pace about 2 of the 11 planned rounds will run."],
                   "check": ["AI: $0.42 of $1.00"], "noai": [], "done": []}, out
    strip = _page_function(STUDY_PAGE, "statusStrip")
    assert "liveFacts(st.status)" in strip and "strip-facts" in strip
    spent = _page_function(STUDY_PAGE, "spentNow")
    assert "r.spent" in spent and "s.spent" in spent, "the verdict's spend once finished, the live one before"
    details = _page_function(STUDY_PAGE, "details")
    assert "spentNow()" in details and "AI spend" in details, "Details' facts carry the spend"
    assert "details-spent" in _page_function(STUDY_PAGE, "update"), "an open Details follows the live spend"
    for word in ("better", "gain"):
        assert word not in _page_function(STUDY_PAGE, "liveFacts")


def test_while_the_ai_writes_a_rounds_changes_the_strip_says_so():
    """Persona round 2, D4: for 3.5 minutes the strip said "Getting ready: preparing the runs" while the AI
    wrote 12 changes. While `writing` is set the strip's sentence is the status document's `now`
    ("Writing changes: 7 of 12."), even when a best so far was found in an earlier round."""
    out = _node(_page_function(STUDY_PAGE, "searchSub"), """
      var w = {phase: "search", writing: {done: 7, planned: 12}, now: "Writing changes: 7 of 12.", sentence: "Searching for better code."};
      var r = {phase: "search", writing: null, now: "Round 2: 12 combinations on 5 cases, 3 of 60 runs done.", sentence: "Searching for better code."};
      process.stdout.write(JSON.stringify({
        writing: searchSub(w, "Found 3 min ago, on the 5 study cases."), first: searchSub(w, ""),
        runs: searchSub(r, "Found 3 min ago, on the 5 study cases."), nothing: searchSub(r, "")}));""")
    assert out == {"writing": "Writing changes: 7 of 12.", "first": "Writing changes: 7 of 12.",
                   "runs": "Found 3 min ago, on the 5 study cases.",
                   "nothing": "Round 2: 12 combinations on 5 cases, 3 of 60 runs done."}, out
    assert "searchSub(s, foundWords())" in _strip_branches(STUDY_PAGE)


def test_the_tab_counts_follow_the_plot_and_the_chip_ends_with_the_verdict():
    """Persona round 2, D5: the Tries and Code tabs said "0" while dots and a best so far were on the plot;
    after the verdict the chip still read "7.4 % lower on the cases it learned from, not checked". The
    counts are redrawn with the plot; once the final check has decided, the chip says the decision on the
    checked combination and no figure on any."""
    out = _node(_page_function(STUDY_PAGE, "tabCounts"), _page_function(STUDY_PAGE, "chipAfter"), """
      var t = {available: true, cases: ["a", "b"], parameters: [], combinations: [
        {id: "seed", base: true}, {id: "c1", diff_stat: {added: 1}}, {id: "c2", diff_stat: {added: 2}}, {id: "c3"}]};
      var c = {id: "c1", n: 17, pct: 7.4};
      process.stdout.write(JSON.stringify({
        counts: tabCounts(t), none: tabCounts({available: false}), empty: tabCounts({available: true, cases: [], combinations: [{id: "s", base: true}]}),
        live: chipAfter(null, "c1", c), checked: chipAfter("keep", "c1", c), use: chipAfter("use", "c1", c), other: chipAfter("keep", "c9", c),
        stopped: chipAfter("stopped", "c1", c), skipped: chipAfter("skipped", "c1", c), missing: chipAfter("missing", "c1", c),
        failed: chipAfter(null, "c1", c, true), liveSearch: chipAfter(null, "c1", c, false),
        chipLive: String(EK.chosenChip(c, {pct: 7.4, direction: "lower"})),
        chipDone: String(EK.chosenChip(c, {after: chipAfter("keep", "c1", c)})),
        chipOther: String(EK.chosenChip(c, {after: chipAfter("keep", "c9", c)}))}));""")
    assert out["counts"] == {"tries": 3, "cases": 2, "settings": 0, "code": 2}, out["counts"]
    assert out["none"] is None and out["empty"] == {"tries": 0, "cases": 0, "settings": 0, "code": 0}
    assert out["live"] is None and out["liveSearch"] is None, "no decision: the figure, labelled as not checked"
    assert out["stopped"] == out["skipped"] == out["missing"] == out["failed"] == "", "no check, no figure either"
    assert out["checked"] == "final check: keep" and out["use"] == "final check: use it" and out["other"] == ""
    assert "7.4 % lower" in out["chipLive"] and "not checked" in out["chipLive"]
    for done in (out["chipDone"], out["chipOther"]):
        assert "not checked" not in done and "%" not in done and "17" in done, done
    assert "final check: keep" in out["chipDone"]
    draw_view = _page_function(STUDY_PAGE, "drawView")
    assert "syncCounts()" in draw_view, "the counts are redrawn with the plot"
    assert "tabCounts(" in _page_function(STUDY_PAGE, "syncCounts") and "tabCounts(" in _page_function(STUDY_PAGE, "draw")
    assert "chipAfter(" in _page_function(STUDY_PAGE, "chipFigure")


def test_compare_branches_shows_each_branchs_held_back_cases():
    """Persona round 2, D6: Compare branches gave each branch's line and band, not its figures on
    b-60 and e-150. Under each branch's line: each held-back case's change (`by_case`), in the goal's
    direction as the report says it; a branch at the starting commit has none."""
    out = _node(_page_function(COMPARE_PAGE, "caseName"), _page_function(COMPARE_PAGE, "caseFigures"),
                _page_function(COMPARE_PAGE, "candidatesView"), """
      var d = {held_back: {cases: 2, runs_per_case: 12}, measure: {direction: "lower"}, candidates: [
        {ref: "nearest-first", commit: "46a98f6aa", decision: "keep", line: "Not proven", mean_pct: 4.5, ci95_pct: [-2.6, 12.1],
         by_case: [{name: "cases/b-60.json", change_pct: 4.1, outcome: "better"}, {name: "cases/e-150.json", change_pct: 5.3, outcome: "better"}]},
        {ref: "restart-sooner", commit: "61782a1aa", decision: "keep", same: true, line: "No difference in any run",
         by_case: [{name: "cases/b-60.json", change_pct: 0, outcome: "same"}, {name: "cases/e-150.json", change_pct: 0, outcome: "same"}]},
        {ref: "main", commit: "0629c1faa", decision: "keep", today: true, line: "main is today's commit"}]};
      var cols = String(candidatesView(d, 260)).split('<section class="cand-col">');
      process.stdout.write(JSON.stringify({near: cols[2], same: cols[3], today: cols[4]}));""")
    near, same, today = out["near"], out["same"], out["today"]
    assert "b-60" in near and "4.1 % lower" in near and "e-150" in near and "5.3 % lower" in near, near
    assert near.index("Not proven") < near.index("b-60"), "under its line"
    assert "cases/" not in near and ".json" not in near
    assert same.count("the same") == 2, same
    assert "b-60" not in today, today
    table = near[near.index("cand-cases"):near.index("</table>")]
    assert "better" not in table, "a figure says lower or higher, never better"


def test_the_thin_check_note_shows_before_and_during_the_run():
    """Persona round 2, D2: nobody said that 2 held-back cases can hardly reach "use" until the money was
    spent. The plan's `notes` show on Review and start, with the AI's assumed time; the status
    document's `notes` show on the Study page while the run waits, starts, searches or checks."""
    plan = _page_function(SETUP_PAGE, "planBlock")
    assert "p.notes" in plan and 'EK.notice("info"' in plan[plan.index("p.notes") - 200:], plan
    assert "p.ai_change_s" in plan, "the plan says what it assumes of the AI"
    notes = _page_function(STUDY_PAGE, "notices")
    assert "s.notes" in notes and "isLive()" in notes[notes.index("s.notes") - 80:notes.index("s.notes") + 80], notes


def test_the_runs_poll_asks_as_often_as_the_route_says():
    """Persona round 2, I1: the runs route is asked every `poll_s` seconds it answers with (5 while a run
    is live, 10 when none is), and at the page's own guess of that before its first answer."""
    out = _node(_page_function(STUDY_PAGE, "runsDelay"), """
      process.stdout.write(JSON.stringify([runsDelay({poll_s: 5}, false), runsDelay({poll_s: 10}, true),
        runsDelay(null, true), runsDelay(null, false), runsDelay({}, false)]));""")
    assert out == [5000, 10000, 5000, 10000, 10000], out
    watch = _page_function(STUDY_PAGE, "watchRuns")
    assert "d.poll_s" in watch or "runsDelay(d" in watch, watch
    assert "tick % 2" not in STUDY_PAGE, "no fixed 5 s beat any more"


def test_a_code_studys_header_counts_no_settings():
    """Persona round 2, M1: a code study's header said "N settings"."""
    head = _page_function(STUDY_PAGE, "header")
    assert "isCode()" in head and 'EK.plural(tuned, "setting")' in head, head


TRIES_AXIS = "% change vs today, on the cases it learned from (not checked)"


def test_the_tries_plots_axis_says_its_points_are_not_checked():
    """Persona round 3, E5: the y-axis read "% BETTER THAN TODAY" over points no final check had seen."""
    plots = (STATIC / "js" / "plots.js").read_text(encoding="utf-8")
    tries = plots[plots.index("EK.triesPlot = function"):plots.index("EK.triesLegend = function")]
    assert TRIES_AXIS in tries, "the axis label, as it is (not upper-cased)"
    assert "better" not in tries.lower(), "never 'better' over unchecked points, the axis and its aria label included"


def test_the_cases_view_never_says_better_over_search_figures():
    """Persona round 3, E5: the Cases view and the try cards said "better" over points no final check had seen."""
    plots = (STATIC / "js" / "plots.js").read_text(encoding="utf-8")
    cases = plots[plots.index("EK.casesView = function"):plots.index("function valueText")]
    assert "better" not in cases.lower(), "the Cases plot's labels and aria text"
    study = STUDY_PAGE
    assert "ahead of today in ${weakest[2][0]} of ${weakest[2][1]} full looks (not checked)" in study
    assert "right column: tries ahead of today on that case (not checked)" in study
    card = _page_function(STUDY_PAGE, "cardLines")
    assert "better" not in card and "searchChange(" in card, card


APP_JS = (STATIC / "js" / "app.js").read_text(encoding="utf-8")


def test_a_compare_picker_that_was_left_does_not_draw_over_the_next_page():
    """Persona round 4, F3: the Compare picker asks twice (the home, then the study) and checked
    that its page was still the one on screen after the first answer only. Leaving it by the hash link
    between the two answers drew the picker over the page that had been routed to."""
    out = _node(_page_function(COMPARE_PAGE, "picker"), """
      var pending = {}, current = 1, mounted = 0;
      EK.get = function (path) { return new Promise(function (resolve) { pending[path.split("?")[0]] = resolve; }); };
      EK.isCurrent = function (gen) { return gen === current; };
      EK.mount = function () { mounted++; };
      EK.companion = {show: function () {}};
      var done = picker({}, "depot/run-1", 1);
      pending["/api/home"]({studies: []});
      Promise.resolve().then(function () { return Promise.resolve(); }).then(function () {
        current = 2;  // the hash link moved on while the study was still being read
        pending["/api/studies/depot"]({runs: [], study: {name: "Depot"}});
        return done;
      }).then(function () { process.stdout.write(JSON.stringify({mounted: mounted})); });""")
    assert out == {"mounted": 0}


def test_every_page_starts_with_the_plain_title():
    """Persona round 4, F3: the Study page names the tab ("Done: ... · evolvekit"); the title stayed
    on the Compare page and Home after the hash link. The router resets it before each page draws."""
    route = _page_function(APP_JS, "route")
    assert 'document.title = "evolvekit"' in route
    assert route.index('document.title = "evolvekit"') < route.index("handler.render("), route


# ---------------------------------------------------------------------------
# campaigns: Home between windows, When it runs in Set up, the Study page's notice
# ---------------------------------------------------------------------------

HOME_PAGE = (STATIC / "js" / "pages" / "home.js").read_text(encoding="utf-8")


def test_home_polls_while_a_campaign_is_armed():
    poll = _page_function(HOME_PAGE, "poll")
    assert 's.kind === "campaign"' in poll and "30000" in poll


def test_a_finished_campaign_card_keeps_the_verdict():
    out = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "t", name: "T", kind: "finished", decision: "use",
        line: "finished · probably better: 3.1 % lower tour length", campaign: {line: "Campaign · next window Sat 00:00"}})));""")
    assert out["sub"].endswith("Campaign · next window Sat 00:00") and "Campaign" not in out["head"]


def test_the_campaign_card_speaks_the_campaigns_words():
    out = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "t", name: "T", kind: "campaign",
        line: "campaign · next window Sat 00:00", campaign: {line: "Campaign · next window Sat 00:00"}, rounds: {done: 2, planned: 6}})));""")
    assert out["kicker"] == "Campaign · next window Sat 00:00" and out["head"] == "Between windows"
    assert out["sub"] == "2 of 6 rounds done · not checked yet"


def test_a_campaign_before_its_first_window_waits_for_it():
    out = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "t", name: "T", kind: "campaign", first: true,
        line: "campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00",
        campaign: {line: "Campaign · the window of Sat 00:00 was missed: the app was not running · next window Mon 01:00", tone: "warn"}})));""")
    assert out["head"] == "Waits for its first window" and out["kickerTone"] == "warning" and out["pepper"] == "egg"


def test_setup_offers_when_it_runs():
    drawer = _page_function(SETUP_PAGE, "whenDrawer")
    assert "schedule_presets" in drawer and '{ schedule: null }' in drawer
    assert "Schedule it" in _page_function(SETUP_PAGE, "draw")


def test_setup_says_when_it_runs():
    summary = "\n".join(_page_function(SETUP_PAGE, name) for name in ("presetOf", "untilWords", "whenSummary"))
    out = _node(summary, """
      var st = {doc: {study: {}, schedule_presets: {weekends: {label: "Weekends, all day",
        schedule: {windows: [{days: ["sat", "sun"], from: "00:00", to: "23:59"}]}}}}};
      function study() { return st.doc.study; }
      var none = whenSummary();
      st.doc.study.schedule = {windows: [{days: ["sat", "sun"], from: "00:00", to: "23:59"}], until: "2026-12-31"};
      process.stdout.write(JSON.stringify({none: none, weekends: whenSummary()}));""")
    assert out["none"]["value"] == "As soon as you start it"
    assert out["weekends"]["value"] == "Weekends, all day, until 31 Dec"
    assert "only while the app is running then" in out["weekends"]["sub"]


def test_when_it_runs_keeps_a_hand_written_keep_awake():
    """Saving the drawer (a new Until, say) keeps `keep_awake: false` from study.yaml."""
    assert "scheduleOf(s, windows, until)" in _page_function(SETUP_PAGE, "whenDrawer")
    out = _node(_page_function(SETUP_PAGE, "scheduleOf"), """
      var w = [{days: ["sat"], from: "00:00", to: "06:00"}];
      process.stdout.write(JSON.stringify([
        scheduleOf({windows: w, until: "2026-12-31", keep_awake: false}, w, "2027-01-31"),
        scheduleOf({windows: w, until: "2026-12-31", keep_awake: false}, w, ""),
        scheduleOf(null, w, "")]));""")
    w = [{"days": ["sat"], "from": "00:00", "to": "06:00"}]
    assert out == [{"windows": w, "until": "2027-01-31", "keep_awake": False}, {"windows": w, "keep_awake": False},
                   {"windows": w}]


def test_the_study_page_says_the_campaign():
    notices = _page_function(STUDY_PAGE, "notices")
    assert "campaign" in notices and "camp.line" in notices
    assert '"Resume the campaign"' in notices and 'camp.state === "paused"' in notices
    assert '"resume-campaign": function' in STUDY_PAGE


def test_a_scheduled_start_says_when_in_the_servers_words():
    out = _node("""process.stdout.write(JSON.stringify([
      EK.scheduledWords({scheduled: true, words: "next window Sat 00:00", renewed: null}),
      EK.scheduledWords({scheduled: true, words: "its window is open now, until Sun 23:59"}),
      EK.scheduledWords({scheduled: true, words: "next window Sat 00:00", renewed: "finished"})]));""")
    assert out == ["Scheduled: next window Sat 00:00", "Scheduled: its window is open now, until Sun 23:59",
                   "Scheduled a new campaign: the last one finished · next window Sat 00:00"]
    for page in (SETUP_PAGE, STUDY_PAGE):
        assert "EK.scheduledWords(res)" in page and "Campaign · " not in page


def test_home_chooses_its_poll_again_after_every_answer():
    poll = _page_function(HOME_PAGE, "poll")
    assert "EK.later(live ? 10000 : 30000" in poll and "poll(main, query, gen)" in poll.split("EK.later", 1)[1]


def test_home_shows_the_rehearsal_clock():
    draw = _page_function(HOME_PAGE, "draw")
    assert "data.clock" in draw and 'EK.notice("warning", EK.rehearsalWords(data.clock))' in draw
    assert draw.index("data.clock") < draw.index("cards(active)"), "above the cards"


# ---------------------------------------------------------------------------
# persona p4 round 1 (wave H-app): a weekend study's plan, its Monday answer, more time for it
# ---------------------------------------------------------------------------

REHEARSAL_TIMES = "Times of the windows follow this clock; other dates are the real time."


def test_the_rehearsal_notice_says_which_times_follow_it():
    """H3: Home's runs said "today 17:19" (the real clock) beside a rehearsal dated 16 Nov."""
    draw = _page_function(HOME_PAGE, "draw")
    assert "EK.rehearsalWords(data.clock)" in draw
    out = _node("""process.stdout.write(JSON.stringify(EK.rehearsalWords({line: "rehearsal clock: Mon 16 Nov 08:39, 120 × real time"})));""")
    assert out == "Rehearsal clock: Mon 16 Nov 08:39, 120 × real time. " + REHEARSAL_TIMES


def test_the_study_page_shows_the_rehearsal_notice_too():
    """Persona p4 round 2, I5: under a rehearsal clock only Home said which times follow it; the Study
    page's header said "today 19:41" beside a Monday rehearsal. Its notices begin with the same line."""
    out = _node(_page_function(STUDY_PAGE, "isLive"), _page_function(STUDY_PAGE, "notices"), """
      var st = {status: {phase: "done"}, run: "r1", doc: {runs: [{id: "r1"}], state: {},
        clock: {line: "rehearsal clock: Mon 16 Nov 08:39, 120 × real time", speed: 120}}};
      var shown = String(notices());
      st.doc.clock = undefined;
      process.stdout.write(JSON.stringify({shown: shown, plain: String(notices())}));""")
    assert "Rehearsal clock: Mon 16 Nov 08:39, 120 × real time. " + REHEARSAL_TIMES in out["shown"], out
    assert "Rehearsal clock" not in out["plain"]


def test_a_draft_card_names_what_is_missing():
    """H6: "Set up not finished" did not say what; the first missing row's name does, and a draft with
    nothing missing is ready, only not started."""
    out = _node("""function card(m, step) { return EK.cardOf({slug: "d", name: "D", kind: "draft", step: step || 7, missing: m}).head; }
      process.stdout.write(JSON.stringify([card(null), card({step: 5, key: "goal.levels"}), card({step: 6, key: "budget.hours"}),
        card({step: 4, key: "vary.settings"}), card({step: 4, key: "vary.code.brief"}), card({step: 4, key: "vary.data"}),
        card({step: 7, key: "per_group", text: "a campaign runs one search; leave out schedule, or per_group, for now"}),
        card({step: 3, key: "cases"}, 2)]));""")
    assert out == ["Ready to start: not started yet", "Set up not finished: Goal", "Set up not finished: Time",
                   "Set up not finished: What to tune", "Set up not finished: What may change: code",
                   "Set up not finished: Data changes", "Set up not finished: When it runs", "Cases still to add"]


WHY_KEEP = "Every held-back case was lower (3.1 % and 8.2 %), but with only 2 cases the range is too wide to rule out a loss."


def test_a_keep_card_says_why_under_its_line():
    """H2: under a keep whose held-back cases all improved, the card says why it is still keep, right
    under the verdict's line and before the advice."""
    out = _node(f"""var c = EK.cardOf({{slug: "w", name: "W", kind: "finished", decision: "keep",
        verdict: {{says: "keep", line: "Not proven on 2 held-back cases", advice: "Keep today's settings for now.", why_keep: {json.dumps(WHY_KEEP)}}}}});
      c.pepper = null;
      process.stdout.write(JSON.stringify({{why: c.why, card: String(EK.studyCard(c))}}));""")
    card = out["card"]
    assert out["why"] == WHY_KEEP
    assert card.index("Not proven") < card.index("Every held-back case") < card.index("Keep today")
    plain = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "w", name: "W", kind: "finished", decision: "keep",
        verdict: {says: "keep", line: "Not proven", advice: "Keep."}}).why || null));""")
    assert plain is None


def test_the_study_strip_says_why_under_the_line():
    strip = _page_function(STUDY_PAGE, "strip")
    assert "why_keep" in strip
    shown = _page_function(STUDY_PAGE, "statusStrip")
    assert "x.why" in shown and shown.index("x.head") < shown.index("x.why") < shown.index("x.sub"), shown


def test_the_study_page_says_when_a_campaigns_run_ran():
    """H3: on Monday the page did not say when in the weekend the run ran; `history_line` (the
    campaign's) follows its notice, the run's own (`history_by_run`) is among the strip's facts and in
    Past runs."""
    notices = _page_function(STUDY_PAGE, "notices")
    assert "camp.history_line" in notices
    assert "history_by_run" in _page_function(STUDY_PAGE, "runFacts")
    assert "runFacts()" in _page_function(STUDY_PAGE, "statusStrip")
    assert "history_by_run" in _page_function(STUDY_PAGE, "runsBody")
    out = _node("""process.stdout.write(JSON.stringify(String(EK.runList([{id: "r1", started_at: "2026-11-08T17:19:00",
        line: "finished · not proven", history: "Ran in its windows: Sat 00:04 – Sun 09:27"}], "r1", function () { return "#"; }))));""")
    assert "Ran in its windows: Sat 00:04 – Sun 09:27" in out


def test_more_time_for_a_scheduled_study_says_when():
    """H5: "Continue for 18 min more" did not say whether it would run now or next weekend. On a study
    with a schedule it says the next window and asks the engine to wait for it; "Search now instead"
    is a second, smaller act. Neither is offered while a renewed campaign waits to start a fresh run,
    and both are disabled from the first click until the answer."""
    words = _node(_page_function(STUDY_PAGE, "continueWords"), """
      process.stdout.write(JSON.stringify([continueWords(0.3, {at: "2026-11-21T00:00:00+01:00", edge: "start", words: "Sat 00:00"}),
        continueWords(0.5, {at: "2026-11-15T23:59:00+01:00", edge: "end", words: "Sun 23:59"}), continueWords(0.3, null)]));""")
    assert words == ["Search 18 min more in the next window (Sat 00:00)",
                     "Search 30 min more in this window (open until Sun 23:59)",
                     "Search 18 min more in the next window"]
    acts = _page_function(STUDY_PAGE, "continueActs")
    out = _node(*(_page_function(STUDY_PAGE, n) for n in ("continueHours", "continueWords", "continueActs")), """
      var st = {run: "r1", status: {can_continue: true}, doc: {runs: [{id: "r1"}], study: {budget: {hours: 0.3}, schedule: {windows: []}},
        next_window: {at: "2026-11-21T00:00:00+01:00", edge: "start", words: "Sat 00:00"}, campaign: {state: "complete"}}};
      function texts() { return continueActs("Continue for 18 min more").map(function (a) { return a.text + (a.disabled ? " [off]" : ""); }); }
      var out = {done: texts()};
      st.continuing = true; out.busy = texts(); st.continuing = false;
      st.doc.new_run_waits = true; out.waits = texts(); st.doc.new_run_waits = false;
      st.doc.campaign.state = "armed"; out.armed = texts(); st.doc.campaign.state = "complete";
      st.status.can_continue = false; out.cannot = texts(); st.status.can_continue = true;
      st.doc.runs.push({id: "r2"}); out.older = texts(); st.doc.runs.pop();
      delete st.doc.study.schedule; out.plain = texts();
      process.stdout.write(JSON.stringify(out));""")
    assert out == {"done": ["Search 18 min more in the next window (Sat 00:00)", "Search now instead"],
                   "busy": ["Search 18 min more in the next window (Sat 00:00) [off]", "Search now instead [off]"],
                   "waits": [], "armed": [], "cannot": [], "older": [], "plain": ["Continue for 18 min more"]}, out
    assert '"Search now instead"' in acts and 'kind: "quiet"' in acts and "new_run_waits" in acts
    assert "st.continuing" in acts, "drawn disabled while a request is out"
    run = _page_function(STUDY_PAGE, "continueRun")
    assert "body.now = true" in run and "res.scheduled" in run and "EK.toast(\"done\", res.words)" in run
    assert "st.continuing = true" in run and run.index("if (st.continuing) return") < run.index("EK.post(")
    assert '"continue-now": function' in STUDY_PAGE
    assert "continueActs(" in _page_function(STUDY_PAGE, "nextSteps") and "continueActs(" in _page_function(STUDY_PAGE, "strip")


def test_setup_makes_room_for_its_drawer_and_shows_a_click_at_once():
    """H8: at 800 px the When it runs drawer covered the plan's right edge (the Final check label, the
    note), and a first click on Schedule it seemed lost. Set up's page moves aside for a drawer on a
    wide screen (a wide drawer overlays below 1100 px, where too little would be left); a save draws the
    page once, not twice; Start says it is at work and stays disabled until the answer."""
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    room = css[css.index("body.drawer-open .page.beside-drawer"):]
    assert "420px" in room.split("}")[0] and "@media (min-width: 760px)" in css[:css.index("body.drawer-open .page.beside-drawer")][-200:]
    assert "body.drawer-open.drawer-wide .page.beside-drawer" in css
    assert "beside-drawer" in _page_function(SETUP_PAGE, "draw")
    drawer = (STATIC / "js" / "drawer.js").read_text(encoding="utf-8")
    assert 'classList.toggle("drawer-wide", !!o.wide)' in drawer and '"drawer-open", "drawer-dims", "drawer-wide"' in drawer
    save = _page_function(SETUP_PAGE, "save")
    assert save.count("draw(main)") == 1, save
    start = SETUP_PAGE[SETUP_PAGE.index("      start: function (el) {"):SETUP_PAGE.index("      later: function")]
    assert '"Scheduling…"' in start and "return EK.post(" in start, start


def _plan_block(doc: dict, plan: dict) -> str:
    out = _node(_page_function(SETUP_PAGE, "wholeWindow"), _page_function(SETUP_PAGE, "planBlock"), f"""
      EK.isPhone = function () {{ return false; }};
      var st = {{doc: {json.dumps(doc)}, plan: {json.dumps(plan)}}};
      function study() {{ return st.doc.study; }}
      process.stdout.write(JSON.stringify(String(planBlock())));""")
    out = re.sub(r'<span class="fig">([^<]*)</span>', r"\1", out)  # the figures of a heading, as read
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", out))


PLAN = {"hours": 0.3, "search_s": 1000, "final_check_s": 80, "rounds": 17, "combinations": 204, "tunables": 4,
        "test_cases": 2, "window": {"line": "starts Sat 00:00; about 18 min of search; done around Sun 12:00",
                                    "starts": "2026-11-14T00:00:00+01:00", "done": "2026-11-15T12:00:00+01:00"}}
WEEKENDS = {"windows": [{"days": ["sat", "sun"], "from": "00:00", "to": "23:59"}]}


def test_a_scheduled_plan_says_its_windows_and_offers_the_whole_window():
    """H1: after choosing weekends the plan bar still said "18 min in total · done around 17:34"; and
    nothing offered the 48 h the weekend gives. The bar says the engine's window line; the offer sets
    the study's hours to a week of its windows (in real hours: on a rehearsal, both clocks).
    Round 2, I1: the offer is the plan's `whole_window` -- the week's hours less the final check, so it
    ends inside the windows -- and it says when it would be done."""
    rehearsal_plan = {**PLAN, "whole_window": {"hours": 0.3909, "done": "2026-11-15T22:54:29+01:00", "done_words": "Sun 22:54"}}
    rehearsal = _plan_block({"study": {"schedule": WEEKENDS, "budget": {"hours": 0.3}}, "schedule_hours": 0.3999,
                             "clock": {"speed": 120.0, "line": "rehearsal clock: Fri 13 Nov 17:38, 120 × real time"}},
                            rehearsal_plan)
    assert "Starts Sat 00:00; about 18 min of search; done around Sun 12:00" in rehearsal
    assert "done around 1" not in rehearsal.replace("done around Sun 12:00", ""), rehearsal
    assert ("Search through the whole window (about 47 h on the rehearsal clock, 23 min of real time; "
            "done around Sun 22:54)") in rehearsal, rehearsal
    assert ("It searches for as long as the windows give in one week, less the time its final check needs, "
            "so the final check runs at the end, inside the windows.") in rehearsal
    real_plan = {**PLAN, "whole_window": {"hours": 46.5, "done": "2026-11-15T22:30:00+01:00", "done_words": "Sun 22:30"}}
    real = _plan_block({"study": {"schedule": WEEKENDS, "budget": {"hours": 0.3}}, "schedule_hours": 47.9833}, real_plan)
    assert "Search through the whole window (about 47 h; done around Sun 22:30)" in real, real
    whole = _plan_block({"study": {"schedule": WEEKENDS, "budget": {"hours": 46.5}}, "schedule_hours": 47.9833}, PLAN)
    assert "whole window" not in whole, "already the whole window: the plan offers none"
    unscheduled = {k: v for k, v in PLAN.items() if k != "window"}
    now = _plan_block({"study": {"budget": {"hours": 0.3}}}, unscheduled)
    assert "18 min in total · done around" in now and "whole window" not in now
    setup = _page_function(SETUP_PAGE, "acts")
    assert '"whole-window": function' in setup and "st.plan.whole_window" in setup
    assert "schedule_hours" not in setup[setup.index('"whole-window"'):setup.index("test: function")], \
        "the offer's hours, never the whole week's"
    assert "st.plan.window" in _page_function(SETUP_PAGE, "timeSummary"), "the Time row never says done around 17:34 either"


def test_saving_when_it_runs_confirms_at_once_and_only_once():
    """Persona p4 round 2, I2: after Save in When it runs the summary still said "As soon as you start
    it" for seconds and nothing confirmed it, so the planner clicked Save twice. On the answer the
    drawer closes, the page is drawn with the new windows and a toast names them (the preset's label,
    or "your own windows"); the plan follows. Save is disabled while saving: two clicks, one save."""
    out = _node(*(_page_function(SETUP_PAGE, name) for name in ("presetOf", "untilWords", "scheduleOf", "whenSaved", "whenDrawer")), """
      var weekends = {windows: [{days: ["sat", "sun"], from: "00:00", to: "23:59"}]};
      var st = {slug: "weekend-depot", doc: {study: {schedule: null}, schedule_presets: {weekends: {label: "Weekends, all day", schedule: weekends}}}};
      function study() { return st.doc.study; }
      var log = [], patches = 0, answer = null, planned = null, drawer = null, picked = "weekends";
      function draw() { log.push("draw:" + (study().schedule ? "scheduled" : "now") + (st.planPending ? ":plan-pending" : "")); }
      function loadPlan() { return new Promise(function (r) { planned = r; }); }
      var redrawDrawer = null;
      EK.openDrawer = function (o) { drawer = o; };
      EK.closeDrawer = function () { log.push("close"); };
      EK.toast = function (kind, text) { log.push("toast:" + text); };
      EK.fail = function (err) { log.push("fail:" + err.message); };
      EK.patch = function (path, change) { patches += 1; return new Promise(function (r) { answer = function () { r({study: {schedule: change.schedule}, schedule_presets: st.doc.schedule_presets}); }; }); };
      document.querySelector = function () { return {value: picked}; };
      document.getElementById = function () { return {value: ""}; };
      var button = {disabled: false, attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; }, removeAttribute: function (k) { delete this.attrs[k]; }};
      var form = {querySelector: function () { return button; }};
      whenDrawer(null);
      drawer.acts["save-when"](form); drawer.acts["save-when"](form);
      var busy = {patches: patches, disabled: button.disabled, busy: button.attrs["aria-busy"]};
      answer();
      setTimeout(function () {
        var before = log.slice();
        planned();
        setTimeout(function () {
          var own = whenSaved({windows: [{days: ["mon"], from: "02:00", to: "04:00"}]});
          var until = whenSaved({windows: weekends.windows, until: "2026-12-31"});
          process.stdout.write(JSON.stringify({busy: busy, before: before, after: log, own: own, until: until, saving: !!st.savingWhen}));
        }, 0);
      }, 0);""")
    assert out["busy"] == {"patches": 1, "disabled": True, "busy": "true"}, out
    assert out["before"] == ["draw:scheduled:plan-pending", "close", "toast:Saved: Weekends, all day"], out["before"]
    assert out["after"][-1] == "draw:scheduled", "drawn again with its plan"
    assert out["own"] == "your own windows" and out["until"] == "Weekends, all day, until 31 Dec"
    assert out["saving"] is False
    plan = _page_function(SETUP_PAGE, "planBlock")
    assert "st.planPending" in plan and "st.planPending" in _page_function(SETUP_PAGE, "draw"), \
        "no stale plan and no Start while the plan is worked out"


def test_when_it_runs_fits_beside_its_page_and_each_row_is_a_target():
    """Persona p4 round 2, I3/I4: at 1024 px the long whole-window button ran under the drawer and
    widened the page; a click on Weekends picked the row below, a squeezed radio in a tight list.
    A long button wraps (and every button of Set up beside its drawer may); each choice is a row whose
    label wraps its radio, fills the row and stands apart; the radio never shrinks."""
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert ".btn.btn-wraps { white-space: normal; max-width: 100%;" in css
    assert "body.drawer-open .page.beside-drawer .btn { white-space: normal; max-width: 100%; }" in css
    assert ".check-line input { flex: none;" in css
    assert ".choice-lines { display: flex; flex-direction: column; gap: 8px; }" in css and ".choice-line { width: 100%;" in css
    assert 'cls: "btn-wraps"' in _page_function(SETUP_PAGE, "wholeWindow")
    when = _page_function(SETUP_PAGE, "whenDrawer")
    assert '<div class="choice-lines">' in when and '<label class="check-line choice-line"><input type="radio"' in when


START_PAGE = (STATIC / "js" / "pages" / "start.js").read_text(encoding="utf-8")


def test_the_cases_drawer_edits_a_tag():
    drawer = _page_function(START_PAGE, "casesDrawer")
    assert "<th>Kind</th>" in drawer and 'act: "tag"' in drawer
    assert 'EK.put(EK.studyPath(st.slug, "/cases/" + EK.q(name) + "/tag")' in drawer
    assert "c.group" in drawer and "c.tag" in drawer, "the tag in its field, the group of groups.json beside it"
    failed = drawer[drawer.index(".catch(function (err)"):]
    assert "el.value = kept && kept.tag" in failed and "EK.fail(err)" in failed, "a refused tag puts the old one back"


def test_cases_view_badges():
    out = _node("""var t = {by_case: [{name: "a.json", group: "small"}, {name: "b.json", group: "large"}]};
      process.stdout.write(JSON.stringify(String(EK.casesBadges(t))));""")   # the part of casesView that draws them
    assert "small" in out and "large" in out


def test_study_page_lists_each_groups_verdict():
    view = _page_function(STUDY_PAGE, "groupVerdicts")
    assert "Per kind of case" in view and "EK.markOf" in view and ".line" in view


def test_study_page_picks_a_group():
    chips = _page_function(STUDY_PAGE, "groupChips")
    assert 'act: "group"' in chips
    assert "group=" in _page_function(STUDY_PAGE, "runQ"), "status, tries and results of the group picked"
    assert "EK.casesBadges" in (STATIC / "js" / "plots.js").read_text(encoding="utf-8")


def test_a_per_group_run_is_offered_no_compare_and_starts_again_when_stopped():
    """Fix round 1 of task 9: a run per kind of case is never one side of a compare (the Study page's
    Compare with... and Compare two runs, Home's menu and its compare drawer), and stopped, it says it
    cannot carry on and offers Start it again."""
    steps = _page_function(STUDY_PAGE, "nextSteps")
    assert 'if (!s.group) out.push(EK.link("Compare with' in steps
    body = _page_function(STUDY_PAGE, "runsBody")
    assert "!r.per_group" in body and "pair[0].id" in body and "runs[0].id" not in body
    menu = _page_function(HOME_PAGE, "studyMenu")
    assert "!s.per_group" in menu.split("Compare with")[0]
    assert "!s.per_group" in _page_function(HOME_PAGE, "compareDrawer")
    strip = _page_function(STUDY_PAGE, "strip")
    assert 'if (s.group && s.phase === "stopped")' in strip and 'act: "restart"' in strip.split('if (s.group && s.phase === "stopped")')[1]
    assert 'var PER_GROUP_STOPPED = "A run per kind of case cannot carry on yet: start the study again.";' in STUDY_PAGE


def test_the_study_page_follows_its_campaign_on_each_runs_poll():
    """Fix round 1: a campaign that finished while the Study page was open said so only after a
    reload. Each runs poll brings the campaign (its line, when its runs ran); the notice and the
    strip are drawn again when it changed, even with the runs the same."""
    out = _node(*(_page_function(STUDY_PAGE, n) for n in ("newerRun", "runsKey", "runsDelay", "watchRuns")), """
      var drawn = {notices: 0, strip: 0}, els = {"study-notices": {innerHTML: ""}, "strip-slot": {innerHTML: ""}};
      document.getElementById = function (id) { return els[id] || null; };
      function isLive() { return false; }
      function header() { return "head"; }
      function notices() { drawn.notices += 1; return "notice:" + (st.doc.state.campaign || {}).line; }
      function statusStrip() { drawn.strip += 1; return "strip:" + ((st.doc.campaign || {}).history_by_run || {}).r1; }
      function runsBody() { return ""; }
      function follow() {}
      var runs = [{id: "r1", kind: "finished", line: "finished · not proven"}];
      var answer = {runs: runs, poll_s: 10, earlier: null, campaign: {state: "complete", line: "Campaign finished",
        history_line: "Ran in its windows: Sat 00:04 – Sun 09:27", history_by_run: {r1: "Ran in its windows: Sat 00:04 – Sun 09:27"}}};
      EK.get = function () { return Promise.resolve(answer); };
      EK.isCurrent = function () { return true; };
      var st = {gen: 1, slug: "w", run: "r1", status: {phase: "done"},
        doc: {runs: runs, state: {campaign: {state: "armed", line: "Campaign · next window Sat 00:00"}},
              campaign: {state: "armed", line: "Campaign · next window Sat 00:00"}}};
      watchRuns().then(function () {
        var first = {notice: els["study-notices"].innerHTML, strip: els["strip-slot"].innerHTML, drawn: Object.assign({}, drawn)};
        return watchRuns().then(function () {
          process.stdout.write(JSON.stringify({first: first, again: drawn, campaign: st.doc.campaign.state}));
        });
      });""")
    assert out["first"]["notice"] == "notice:Campaign finished", out
    assert out["first"]["strip"] == "strip:Ran in its windows: Sat 00:04 – Sun 09:27", out
    assert out["campaign"] == "complete"
    assert out["again"] == out["first"]["drawn"], "the same campaign again: nothing drawn again"


def test_a_scheduled_continue_never_enables_its_button_again_before_the_redraw():
    """Fix round 1 (app review, Minor 1): on a scheduled answer the old Continue button stayed live
    during the refetch, so a second click could add the hours twice."""
    out = _node(_page_function(STUDY_PAGE, "continueRun"), """
      var buttons = [{disabled: false, attrs: {}}, {disabled: false, attrs: {}}];
      buttons.forEach(function (b) { b.setAttribute = function (k, v) { b.attrs[k] = v; }; b.removeAttribute = function (k) { delete b.attrs[k]; }; });
      EK.$$ = function () { return buttons; };
      var posted = 0, rerendered = 0, toasts = [];
      EK.post = function () { posted += 1; return Promise.resolve({scheduled: true, words: "Scheduled 18 min more for the next window (Sat 00:00)"}); };
      EK.rerender = function () { rerendered += 1; };
      EK.toast = function (kind, words) { toasts.push(words); };
      EK.studyPath = function (slug, path) { return path; };
      function continueHours() { return 0.3; }
      var st = {slug: "w", run: "r1"};
      var first = continueRun(false);
      continueRun(false);  // a second click while the first is out: nothing
      first.then(function () {
        continueRun(false);  // and none after the answer, before the redraw
        process.stdout.write(JSON.stringify({posted: posted, rerendered: rerendered, toasts: toasts, continuing: st.continuing,
          disabled: buttons.map(function (b) { return b.disabled; })}));
      });""")
    assert out == {"posted": 1, "rerendered": 1, "toasts": ["Scheduled 18 min more for the next window (Sat 00:00)"],
                   "continuing": True, "disabled": [True, True]}, out
    acts = STUDY_PAGE[STUDY_PAGE.index('"continue": function'):]
    assert acts.startswith('"continue": function () { continueRun(false); }'), "no promise: the act wrapper never enables it"
    assert '"continue-now": function () { continueRun(true); }' in STUDY_PAGE

SETTINGS = (STATIC / "js" / "pages" / "settings.js").read_text(encoding="utf-8")


def test_settings_offers_stop_the_app():
    assert '"Stop the app"' in SETTINGS and "/api/shutdown" in SETTINGS
    assert "Studies that are running carry on." in SETTINGS
    assert "stoppedWords(st.saved.frozen)" in SETTINGS


def test_the_stop_notice_speaks_of_how_the_app_was_started():
    """The final review, minor 6 and Important 2: an installer user has no `evolvekit` command, a pip
    user no evolvekit-app.exe; and extracting over a folder whose files a running study holds fails."""
    out = _node(_page_function(SETTINGS, "stoppedWords"),
                "process.stdout.write(JSON.stringify([stoppedWords(true), stoppedWords(false)]));")
    assert out == ["The app has stopped. Studies that are running carry on. To open it again, double-click "
                   "evolvekit-app.exe. To update the app, extract the new version over its folder only when no "
                   "study is running (Home shows them); while one runs, extract it into a new folder and start "
                   "it from there.",
                   "The app has stopped. Studies that are running carry on. To open it again, run evolvekit app."]


# -- persona p5, round 1 (J1, J2, J3, J4, J6, J7) ------------------------------------

SETUP_PAGE = (STATIC / "js" / "pages" / "setup.js").read_text(encoding="utf-8")
START_PAGE = (STATIC / "js" / "pages" / "start.js").read_text(encoding="utf-8")


def test_a_search_that_did_not_run_says_so_on_home_and_offers_continue():
    out = _node("""process.stdout.write(JSON.stringify(EK.cardOf({slug: "t", name: "T", kind: "finished", decision: "skipped",
        not_run: true, can_continue: true, continue_h: 0.167, line: "finished · the search did not run",
        verdict: {says: "the search did not run", decision: "skipped",
                  line: "The search did not run: there was not enough time left for its first round.",
                  advice: "Keep today's settings for now: nothing was searched yet. Continue the run to give the search its time."}})));""")
    assert out["kicker"] == "The search did not run" and out["head"].startswith("The search did not run: ")
    assert out["actions"] == [{"text": "Continue for 10 min more", "act": "continue-run", "arg": "t", "kind": "primary"}]
    act = HOME_PAGE[HOME_PAGE.index('"continue-run": function'):]
    assert 'EK.post(EK.studyPath(s.slug, "/continue"), { run: s.run })' in act[:800], "the Study page's request (M4)"
    words = _node("""process.stdout.write(JSON.stringify([EK.answerWords({decision: "skipped", not_run: true}).head,
        EK.answerWords({decision: "skipped"}).head]));""")
    assert words == ["The search did not run", "Nothing beat the starting point"]
    strip = _strip_branches(STUDY_PAGE)
    assert 'd === "skipped" && f.not_run' in strip and '"The search did not run"' in strip
    table = _page_function(STUDY_PAGE, "changedTable")
    assert '"Nothing: the search did not run."' in table, "never 'the starting point stayed the best' for it"
    code = _page_function(STUDY_PAGE, "codeChange")
    assert code.index("(r.final || {}).not_run") < code.index("today’s code stayed the best"), "a code study too (I1)"


def test_a_continuation_says_how_much_more_and_a_restart_says_restart():
    out = _node("""function head(s) { return EK.cardOf(Object.assign({slug: "t", name: "T", kind: "running", phase: "search",
        continued: true, rounds: {done: 0, planned: 4}}, s)).head; }
      process.stdout.write(JSON.stringify([head({added_h: 0.167}), head({restarted: true}), head({})]));""")
    assert out == ["Continued: 10 min more", "Continued after a restart", "Continued"]


def test_the_install_confirms_by_name_and_the_box_takes_a_zip():
    assert 'named + " installed"' in HOME_PAGE and '"Or a harness folder or zip on this computer"' in HOME_PAGE


def test_home_says_loading_not_no_studies_until_its_first_answer():
    render = _page_function(HOME_PAGE, "render")
    assert "Loading…" in render and "(remembered.studies || []).length" in render


def test_a_blocked_plans_fixes_are_buttons_and_its_reason_is_said_once():
    block = _page_function(SETUP_PAGE, "planBlock")
    assert '"way-fix"' in block and "p.blocked_reason" in block and "barText: p.blocked" not in block
    assert '"way-fix": function' in SETUP_PAGE and "time_per_case_s: f.seconds" in SETUP_PAGE


def test_the_finder_names_another_version_and_never_says_not_found_for_it():
    block = _page_function(START_PAGE, "appBlock")
    assert '"Another version of " + title + " found"' in block and '"Point to the Python that has it."' in block


def test_the_settings_legend_says_the_goals_direction_and_not_checked():
    assert "The band: values in tries over 1 % better" not in STUDY_PAGE
    assert '"values in tries over 1 % " + (g.direction === "higher" ? "higher" : "lower")' in STUDY_PAGE
    assert '" (not checked)"' in STUDY_PAGE


def test_the_pages_chance_words_never_read_like_worse_on_a_keep():
    """Decision 14b in the page's own words: worse is at 1 in 20, so 0.06 says "over 1 in 20"."""
    out = _node("""process.stdout.write(JSON.stringify([EK.chanceWords(0.06), EK.chanceWords(0.05),
        EK.chanceWords(0.04), EK.chanceWords(0.06, false)]));""")
    assert out == ["chance over 1 in 20", "chance about 1 in 20", "chance less than 1 in 20", "chance about 1 in 20"]
