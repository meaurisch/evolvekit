/* evolvekit app: the Study page — searching, then the verdict.
   A status strip (a live region) says the study's one answer right now and
   turns into the verdict once the final check decides. Below it three linked
   views, Tries · Cases · Settings, with hover cards and one chosen
   combination carried across them. Then: hand it over, what next, the
   details, and the study's runs one click away. Nothing says "use it" before
   the final check's decision is "use". */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  var st = null;

  function render(main, slug, query, gen) {
    st = { slug: slug, gen: gen, run: query.run || "", view: ["tries", "cases", "settings", "code"].indexOf(query.view) >= 0 ? query.view : "tries",
      chosen: null, chosenSet: false, doc: null, h: null, status: null, results: null, tries: null, hover: null, lastStrip: "", lastPlot: "", phase: null,
      showAllSettings: false, openRuns: query.runs === "1" };
    // Everything at once: the study, its harness and the run (status, tries, results) in one round.
    return Promise.all([EK.get(EK.studyPath(slug)), EK.get(EK.studyPath(slug, "/harness")), load(true).catch(function () { return "no run"; })]).then(function (r) {
      if (!EK.isCurrent(gen)) return;
      st.doc = r[0]; st.h = r[1];
      if (r[2] === "no run" || !st.doc.runs || !st.doc.runs.length) { location.replace(((st.doc.study.step || 1) >= 4 ? "#/setup/" : "#/start/") + EK.q(slug)); return; }
      if (viewKeys().indexOf(st.view) < 0) st.view = st.view === "settings" || st.view === "code" ? viewKeys()[2] : "tries";
      return Promise.resolve().then(function () {
        if (!EK.isCurrent(gen)) return;
        EK.hoverEscape = hoverEscape;
        EK.onStudyChanged = function () { load().then(function () { if (EK.isCurrent(gen)) update(main); }); };
        draw(main);
        if (st.openRuns) runsDrawer();
        // A live run's progress every 2.5 s; live or not, the study's runs as often as the runs route
        // says (`poll_s`: 5 s while a run is live, 10 s when none is): a run started elsewhere (a
        // bake-off from the terminal) or a run that ended shows here without a reload.
        st.runsAt = Date.now() + runsDelay(null, isLive());
        EK.every(2500, function () {
          if (!st.status) return;
          if (isLive()) load().then(function () { if (EK.isCurrent(gen)) update(main); }).catch(function () { /* the next poll tries again */ });
          if (Date.now() + 1250 >= st.runsAt) watchRuns();  // the beat nearest the time the route asked for
        });
        window.addEventListener("resize", onResize);
      });
    });
  }
  var resizeTimer = null;
  function onResize() {
    if (!st || !EK.isCurrent(st.gen)) { window.removeEventListener("resize", onResize); return; }
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { st.lastPlot = ""; drawView(); }, 150);
  }
  function isLive() { var p = st.status.phase; return p === "queued" || p === "starting" || p === "search" || p === "check"; }
  /** The phases in which no run is live, so "Compare branches" is offered on a code study. */
  var BRANCHES_WHEN = ["done", "stopped", "failed"];
  function runQ() { return st.run ? "?run=" + EK.q(st.run) : ""; }
  /** The three views: a code study's third is Code (what each combination changed), else Settings. */
  function viewKeys() { return ["tries", "cases", isCode() ? "code" : "settings"]; }

  // ------------------------------------------------------------------ the study's runs, while the page is open
  /** The run to move to: the newest, when it is new and the page showed the newest before it. */
  function newerRun(old, fresh, shown) {
    var was = (old[old.length - 1] || {}).id, now = (fresh[fresh.length - 1] || {}).id;
    return now && was && now !== was && shown === was ? now : null;
  }
  /** What the header, the notices and the runs drawer show of the runs: when it changes, they follow. */
  function runsKey(runs) {
    return runs.map(function (r) { return r.id + "\u0001" + r.kind + "\u0001" + r.line + "\u0001" + ((r.branch || {}).line || ""); }).join("\u0002");
  }
  /** Ask for the study's runs as they are now; follow a new run, or bring the header, the notices and
      an open runs drawer up to date (a run that ended, a run that started while another was shown). */
  function watchRuns() {
    var gen = st.gen, asked = Date.now();
    st.runsAt = asked + runsDelay(null, !!st.status && isLive());  // asked: not again before the next beat
    return EK.get(EK.studyPath(st.slug, "/runs")).then(function (d) {
      if (!EK.isCurrent(gen)) return;
      st.runsAt = asked + runsDelay(d, !!st.status && isLive());
      var old = st.doc.runs || [], fresh = d.runs || [];
      var to = newerRun(old, fresh, st.run);
      var said = JSON.stringify((st.doc.state || {}).earlier || null), says = JSON.stringify(d.earlier || null);
      st.doc.runs = fresh;
      if ("earlier" in d) st.doc.state = Object.assign({}, st.doc.state, { earlier: d.earlier || null });
      if (to) { follow(to); return; }
      if (runsKey(old) === runsKey(fresh) && said === says) return;
      var head = document.getElementById("study-head");
      if (head) head.innerHTML = String(header());
      var n = document.getElementById("study-notices");
      if (n) { var nh = String(notices()); n.innerHTML = nh; st.lastNotices = nh; }
      if (EK.drawerKind() === "runs") EK.refreshDrawer({ body: runsBody() });
    }).catch(function () { /* the next poll tries again */ });
  }
  /** How long until the runs route is asked again, in ms: its own `poll_s` (5 while a run of the study
      is live, 10 when none is); before its first answer, the page's guess from the run it shows. */
  function runsDelay(d, live) {
    var s = d && Number(d.poll_s);
    return (s > 0 ? s : live ? 5 : 10) * 1000;
  }
  /** Show a run that started while the page was open, in place of the one that was the newest. */
  function follow(run) {
    EK._moveFocus = false;
    EK.toast("done", "A new run of this study started: showing it");
    location.replace(location.pathname + location.search + EK.studyHref(st.slug, run));
  }

  /** The run's status, tries and results, asked for together. While the search runs, the results wait. */
  function load(first) {
    var wantResults = first || !st.status || ["done", "stopped", "failed", "check"].indexOf(st.status.phase) >= 0;
    return Promise.all([
      EK.get(EK.studyPath(st.slug, "/status" + runQ())),
      EK.get(EK.studyPath(st.slug, "/tries" + runQ())).catch(function () { return null; }),
      wantResults ? EK.get(EK.studyPath(st.slug, "/results" + runQ())).catch(function () { return null; }) : Promise.resolve(st.results)
    ]).then(function (r) {
      st.status = r[0];
      if (!st.run) st.run = r[0].run;
      st.tries = r[1];
      if (st.tries && st.tries.cases) EK.caseNames(st.tries.cases);
      st.results = r[2];
      if (!st.chosenSet && st.results && st.results.best_id && st.status.phase !== "search") { st.chosen = st.results.best_id; }
    });
  }

  // ------------------------------------------------------------------ what the strip says
  function decisionOf() {
    var f = (st.results && st.results.final) || {};
    if (st.status.phase !== "done") return null;
    if (f.state === "skipped") return "skipped";
    if (f.state === "stopped") return "stopped";
    if (f.state === "missing") return "missing";
    return f.decision || (f.state === "confirmed" ? "use" : f.state === "worse" ? "worse" : f.state === "single" ? "single" : "keep");
  }
  function cardState() {
    var s = st.status, d = decisionOf(), f = (st.results && st.results.final) || {};
    var kind = s.phase === "done" ? "finished" : s.phase === "queued" ? "queued" : s.phase === "failed" ? "failed" : s.phase === "stopped" ? "stopped" : "running";
    return { kind: kind, phase: s.phase, decision: d, rounds: s.rounds, continued: /again|on, with more time/.test(s.sentence || ""),
      check_stopped: d === "stopped" || (s.phase === "stopped" && f.state === "stopped"), what: whatOf() };
  }
  function whatOf() {
    if (isCode()) return "code";
    return Object.keys((st.doc.study.vary || {}).data || {}).length && !Object.keys((st.doc.study.vary || {}).settings || {}).length ? "data" : "settings";
  }
  /** A code study (vary.code): its runs can compare branches; a bake-off run has its branches side by side. */
  function isCode() { return !!(st.doc.study.vary || {}).code; }
  function isBakeoff() { return !!(st.status && st.status.bakeoff_refs && st.status.bakeoff_refs.length); }
  function branchCount() { return EK.plural(st.status.bakeoff_refs.length, "branch", "branches"); }
  function heldCount() { return ((st.doc.study.cases || {}).test || []).length; }
  function roundWord() {
    var r = st.status.rounds || {}, now = Math.min((r.done || 0) + 1, r.planned || 99);
    return r.planned ? "round " + now + " of " + r.planned : "round " + now;
  }
  function timeline() {
    var r = st.status.rounds || {}, s = st.status, ch = check();
    var o = { rounds: Math.max(1, r.planned || (r.done || 0) + 1), done: Math.min(r.done || 0, r.planned || 999) };
    if (s.phase === "search" || s.phase === "starting") o.running = (r.done || 0) + 1;
    if (s.phase === "queued") o.queued = true;
    if (s.phase === "failed") { o.running = (r.done || 0) + 1; o.failed = true; }
    if (s.phase === "stopped" && !ch) o.stoppedAt = (r.done || 0) + 1;
    if (s.phase === "check" || (ch && ch.pairs)) { o.done = o.rounds; if (ch) o.check = [ch.pairs, ch.planned || ch.pairs]; }
    return o;
  }
  function check() {
    var c = st.tries && st.tries.check;
    if (!c) return null;
    var planned = c.runs_per_case && heldCount() ? c.runs_per_case * heldCount() : null;
    return Object.assign({}, c, { planned: planned });
  }
  /** The best so far: one figure, the status document's (`verdict.best_so_far`), the very number
      `study status` prints; positive is better, and nothing is shown until it beats today. */
  function bestPct() {
    var b = (st.status.best || {}).pct;
    return b != null && b > 0 ? b : null;
  }
  /** That figure in `study status`'s words ("6.1 % lower tour length", the server's `says`), labelled as the chip labels it. */
  function bestSays() {
    var b = st.status.best || {}, goal = goalOf() || {};
    var says = b.says || EK.change(b.pct, goal.direction) + (goal.says ? " " + goal.says : "");
    return says + " on the cases it learned from";
  }

  function strip() {
    var s = st.status, d = decisionOf(), f = (st.results && st.results.final) || {}, keep = EK.keepWords({ what: whatOf() });
    var x = { tone: "panel", mark: "open", kickerTone: "accent", tag: "", right: "timeline", range: null, caption: "", acts: [], rule: false };
    var ch = check();
    var left = s.time && s.time.left ? s.time.left + " left" : "";
    if (s.phase === "queued") {
      Object.assign(x, { kicker: "Waiting in the queue", head: s.sentence ? s.sentence.replace(/^Waiting for '(.+)' to finish\.$/, "Starts after $1") : "Waits for its turn",
        sub: "It starts by itself. You can close this page.", caption: s.time && s.time.hours ? "About " + EK.hours(s.time.hours) + " once it starts" : "",
        acts: [{ text: "Remove from queue", kind: "secondary", act: "stop" }] });
    } else if (s.phase === "starting") {
      Object.assign(x, { kicker: "Starting", head: "Measuring " + keep.replace(/^Keep /, "").replace("today’s", "today’s"), sub: s.now || "Getting ready.",
        caption: isBakeoff() ? "Then " + branchCount() + " against it on the held-back cases, with no search"
          : (s.rounds || {}).planned ? "Then " + EK.plural(s.rounds.planned, "round") + " and the final check" : "Then the search and the final check", acts: [{ text: "Stop", kind: "secondary", act: "stop" }] });
    } else if (s.phase === "search") {
      var b = bestPct();
      Object.assign(x, { kicker: (cardState().continued ? "Searching again · " : "Searching · ") + roundWord(), head: b ? "Best so far: " + bestSays() : "Nothing better yet",
        tag: b ? "Not checked yet" : "", sub: searchSub(s, foundWords()),
        caption: EK.capital(roundWord()) + (s.time && s.time.finish_at ? " · done around " + s.time.finish_at : left ? " · " + left : ""),
        acts: [{ text: "Stop", kind: "secondary", act: "stop" }] });
    } else if (s.phase === "check") {
      Object.assign(x, { kicker: (isBakeoff() ? "Comparing branches" : "Final check") + (ch && ch.planned ? " · " + ch.pairs + " of " + ch.planned + " runs" : ""), head: "No answer yet",
        sub: isBakeoff() ? "Running the starting point and " + branchCount() + " on " + EK.plural(heldCount(), "held-back case") + "."
          : "Adding runs on " + EK.plural(heldCount(), "held-back case") + " while time remains.", right: ch && ch.mean != null ? "both" : "timeline",
        range: ch && ch.mean != null ? [ch.mean, ch.ci95 && ch.ci95[0], ch.ci95 && ch.ci95[1]] : null, caption: "So far, on held-back cases",
        acts: [{ text: "Stop the check", kind: "quiet", act: "stop" }] });
    } else if (s.phase === "failed") {
      Object.assign(x, { mark: "error", kickerTone: "critical", kicker: "Stopped by an error",
        head: isBakeoff() ? "The branches were not compared" : "The study stopped in " + roundWord().replace(/ of \d+$/, ""),
        sub: String(s.error || "No reason recorded.").split("\n")[0].slice(0, 140),
        caption: isBakeoff() ? (s.time && s.time.used ? "Stopped at " + s.time.used : "") : EK.capital(roundWord()) + (s.time && s.time.used ? " · stopped at " + s.time.used : ""),
        acts: [s.can_resume ? { text: "Continue where it stopped", act: "resume" } : null, { text: "Start it again", kind: "secondary", act: "restart" }].filter(Boolean) });
    } else if (s.phase === "stopped" && !(f.state === "stopped")) {
      var b2 = bestPct();
      Object.assign(x, { kickerTone: "", kicker: "Stopped by you · " + roundWord().replace(/ of \d+$/, ""), head: b2 ? "Best so far: " + bestSays() : "Stopped before a better one",
        tag: b2 ? "Not checked yet" : "", sub: s.can_check ? "Check it, or carry on searching." : s.can_resume ? "Carry on searching when you like." : "Tries up to the stop are kept.",
        caption: EK.capital(roundWord()) + (s.time && s.time.used ? " · stopped at " + s.time.used : ""), stoppedAt: true,
        acts: [s.can_check ? { text: "Run the final check", act: "check" } : null, s.can_resume ? { text: "Carry on", kind: s.can_check ? "secondary" : "primary", act: "resume" } : null].filter(Boolean) });
    } else if (d === "use") {
      Object.assign(x, { tone: "accent", mark: "use", kickerTone: "", kicker: "Use it · final check on " + EK.plural(f.n || heldCount(), "case"), head: verdictHead(f),
        sub: verdictSub(f), right: "range",
        range: [f.mean, f.ci95 && f.ci95[0], f.ci95 && f.ci95[1]], caption: "Likely range, on the final check", rule: true,
        acts: [hasExport("settings_json") ? { text: "Save settings file", act: "save-settings" } : null, { text: "Copy", kind: "secondary", act: "copy-summary", label: "Copy a summary" }].filter(Boolean) });
    } else if (d === "keep") {
      // "No difference in any run" (`state: same`): the verdict's line, and no band, range or chance.
      var same = f.state === "same";
      Object.assign(x, { mark: "keep", kickerTone: "", kicker: "Keep · final check on " + EK.plural(f.n || heldCount(), "held-back case"), head: verdictHead(f),
        sub: verdictSub(f), right: "range",
        range: !same && f.ci95 ? [f.mean, f.ci95[0], f.ci95[1]] : null,
        caption: same ? "" : isBakeoff() && !f.ci95 ? "Each branch’s answer is on the page that shows them side by side" : EK.rangeCaption({ decision: "keep", chance: f.chance, ci95: f.ci95 }), rule: true,
        acts: [f.recheck ? { text: "Check with more runs", kind: "secondary", act: "recheck" } : null].filter(Boolean) });
    } else if (d === "worse") {
      Object.assign(x, { mark: "worse", kickerTone: "", kicker: "Worse · final check on " + EK.plural(f.n || heldCount(), "case"), head: verdictHead(f),
        sub: verdictSub(f), right: "range", range: f.ci95 ? [f.mean, f.ci95[0], f.ci95[1]] : null, caption: EK.rangeCaption({ decision: "worse", chance: f.chance, ci95: f.ci95 }), rule: true,
        acts: [{ text: "Start a new study", kind: "secondary", act: "next" }] });
    } else if (d === "single") {
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Final check done · 1 held-back case", head: verdictHead(f),
        sub: verdictSub(f), right: "range", range: [f.mean, null, null], caption: "One case: a number, no range",
        acts: [{ text: "Hold back more cases", kind: "secondary", act: "next" }] });
    } else if (d === "skipped") {
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Search done" + (s.time && s.time.used ? " · " + s.time.used : ""),
        head: verdictHead(f), sub: verdictSub(f),
        caption: EK.capital(roundWord()) + " · no final check", stoppedAt: true,
        acts: [s.can_continue ? { text: "Search for longer", kind: "secondary", act: "continue" } : null].filter(Boolean) });
    } else if (d === "stopped") {
      Object.assign(x, { kickerTone: "", kicker: "Final check stopped" + (ch && ch.planned ? " · " + ch.pairs + " of " + ch.planned + " runs" : ""), head: verdictHead(f),
        sub: verdictSub(f), right: ch && ch.mean != null ? "both" : "timeline",
        range: ch && ch.mean != null ? [ch.mean, ch.ci95 && ch.ci95[0], ch.ci95 && ch.ci95[1]] : null, caption: "So far, on the final check",
        acts: [{ text: "Continue the check", act: "check" }] });
    } else {
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Finished", head: verdictHead(f), sub: verdictSub(f), acts: [] });
    }
    if (isCode() && BRANCHES_WHEN.indexOf(s.phase) >= 0) {
      // A code study with no run live (done, stopped or failed): given branches can be checked
      // against its starting point (`jobs.start` refuses while a run is live, so only this gate moves).
      if (isBakeoff() && s.phase === "done") x.acts.push({ text: "See the branches side by side", kind: "secondary", act: "candidates" });
      x.acts.push({ text: "Compare branches", kind: "secondary", act: "branches" });
    }
    return x;
  }
  /** The searching strip's sentence: while the AI writes a round's changes, the status document's `now`
      ("Writing changes: 7 of 12."); else when the best so far was found, or what runs now. */
  function searchSub(s, found) {
    return s.writing ? s.now : found || s.now || s.sentence;
  }
  /** Facts said under a live run's strip, as the status document gives them: the AI's spend of its cap
      while it searches or checks, and while it searches, how many planned rounds its pace buys. They
      claim nothing about the result: that is the verdict's alone. */
  function liveFacts(s) {
    var out = [];
    if ((s.phase === "search" || s.phase === "check") && s.spent && s.spent.line) out.push(s.spent.line);
    if (s.phase === "search" && s.pace && s.pace.line) out.push(s.pace.line);
    return out;
  }
  /** The verdict in the results document's own words (design 3.4): its `line` is the headline and
      its `advice` the sentence under it, verbatim, so the page and the terminal say the same. */
  function verdictHead(f) { return f.line || "No answer yet"; }
  function verdictSub(f) { return f.advice || ""; }
  /** "Found 14 min ago, on the 10 study cases." for the best so far, from the tries. */
  function foundWords() {
    var t = st.tries, best = t && (t.best || []).slice(-1)[0];
    if (!best || best.id === t.base) return "";
    var ago = Math.max(0, t.now_h - best.h);
    return "Found " + (ago < 1 ? Math.max(1, Math.round(ago * 60)) + " min" : EK.hours(ago)) + " ago, on the " + EK.plural(t.cases.length, "study case") + ".";
  }
  function settingsText() {
    return fetch(EK.studyPath(st.slug, "/download/settings_json?run=" + EK.q(st.run)), { headers: { "X-Evolvekit-Token": EK._token } })
      .then(function (r) { if (!r.ok) throw new Error("The settings file could not be read"); return r.text(); });
  }
  function goalOf() { return (st.results && st.results.goal) || (st.status && st.status.goal) || null; }
  function hasExport(name) { return ((st.results && st.results.downloads) || []).some(function (d) { return d.name === name; }); }

  function statusStrip() {
    var x = strip(), acc = x.tone === "accent", phone = EK.isPhone();
    var tl = timeline();
    if (x.stoppedAt) tl.stoppedAt = (tl.done || 0) + 1;
    var w = phone ? Math.max(280, window.innerWidth - 64) : 440;
    var vis = x.right === "timeline" ? EK.runTimeline(Object.assign({ width: w }, tl))
      : x.right === "both" ? html`${EK.runTimeline(Object.assign({ width: w }, tl))}<div style="height:8px"></div>${EK.intervalStrip(x.range[0], x.range[1], x.range[2], { width: w })}`
      : x.range ? EK.intervalStrip(x.range[0], x.range[1], x.range[2], { width: w, onAccent: acc }) : "";
    var acts = x.acts.map(function (a) { return EK.button(a.text, Object.assign({ kind: "primary", onAccent: acc }, a)); });
    var rule = x.rule ? html`<p class="type-small strip-rule${acc ? "" : " ink-2"}">The rule: use it at 9 in 10 or better, worst within 1 %. ${EK.link("Why", { act: "why", cls: acc ? "on-accent-link" : "" })}</p>` : "";
    var facts = liveFacts(st.status);
    var factLine = facts.length ? html`<div class="strip-facts${acc ? "" : " ink-2"}">${facts.map(function (f) { return html`<span>${f}</span>`; })}</div>` : "";
    return html`<section class="status-strip${acc ? " is-accent" : ""}" aria-label="Status" aria-live="polite" aria-atomic="true">
      <div class="strip-main">${EK.verdictMark(x.mark, phone ? 36 : 44, acc)}<div class="strip-words">
        <span class="type-label${x.kickerTone === "accent" && !acc ? " accent-ink" : x.kickerTone === "critical" ? " tone-critical" : ""}">${x.kicker}</span>
        <p class="${acc ? "type-display" : "strip-head"}">${EK.figures(x.head)}${x.tag ? html` <span class="strip-tag">${x.tag}</span>` : ""}</p>
        <p class="type-body${acc ? "" : " ink-2"}">${x.sub || ""}</p>${factLine}${branchLines(acc)}</div></div>
      <div class="strip-side">${vis}${x.caption ? html`<span class="type-small${acc ? "" : " ink-2"}">${x.caption}</span>` : ""}
        ${acts.length ? html`<div class="actions">${acts}</div>` : ""}${rule}</div></section>`;
  }

  /** A finished bake-off: each branch's own line under the verdict, verbatim (as `study verdict` prints
      them), so a branch no run told from today's says so here too; its band, if any, is on Compare branches. */
  function branchLines(acc) {
    var c = (st.results && st.status.phase === "done" && st.results.candidates) || [];
    if (!isBakeoff() || !c.length) return "";
    return html`<ul class="strip-branches type-small${acc ? "" : " ink-2"}">${c.map(function (b) {
      return html`<li><span class="mono">${b.ref}</span>: ${EK.figures(b.line)}</li>`;
    })}</ul>`;
  }

  // ------------------------------------------------------------------ Pepper and the page title
  function companion() {
    var cs = cardState(), phase = EK.pepperPhase(cs), x = strip();
    var finished = cs.kind === "finished";
    var announce = null;
    var seenKey = "evolvekit.hatched." + st.slug + "/" + st.run, seen = true;
    try { seen = !!localStorage.getItem(seenKey); if (finished && !seen) localStorage.setItem(seenKey, "1"); } catch (e) { seen = true; }
    if (((st.phase && st.phase !== phase) || !seen) && finished) announce = phase === "butterfly" ? "I’m out! " + x.head.replace(/\.$/, "") + ", checked on " + EK.plural(((st.results || {}).final || {}).n || heldCount(), "case") + "." : "I’m out. " + x.head.replace(/\.$/, "") + ".";
    if (st.phase && st.phase !== phase && !finished && phase === "cocoon") announce = "Halfway. I’m in my cocoon now.";
    EK.companion.show({ phase: phase, slug: st.slug, say: x.head.replace(/\.$/, "") + ". " + (x.sub || ""), announce: announce, finished: finished });
    if (st.phase && st.phase !== phase && finished) notify(x.head);
    st.phase = phase;
    var name = st.doc.study.name;
    document.title = (isLive() ? "Running: " : finished ? "Done: " : "") + name + " · evolvekit";
  }
  function notify(text) {
    try {
      if ("Notification" in window && Notification.permission === "granted") new Notification(st.doc.study.name, { body: text });
    } catch (e) { /* no notifications here */ }
  }

  // ------------------------------------------------------------------ the views
  function combo(id) { return st.tries && st.tries.combinations.filter(function (c) { return c.id === id; })[0]; }
  function paramOf(name) { return (st.tries.parameters || []).filter(function (p) { return p.name === name; })[0]; }
  function labelOf(name) { var s = (st.h.settings || []).filter(function (x) { return x.name === name; })[0]; return s ? s.label || name : name; }
  function lookWords(c) {
    var t = st.tries;
    return c.full ? "Full look, " + EK.plural(t.cases.length, "case") : "Quick look, " + EK.plural(t.quick_cases || 0, "case");
  }
  /** What a code study's combination changes, from its `diff_stat`: "+3 −1 lines in solver.py". */
  function diffWords(stat) {
    var files = stat.files || [];
    if (!stat.added && !stat.removed && !files.length) return "No change: today’s code";
    var where = files.length === 1 ? " in " + String(files[0]).split("/").pop() : files.length ? " in " + EK.plural(files.length, "file") : "";
    return "+" + EK.int(stat.added || 0) + " −" + EK.int(stat.removed || 0) + " lines" + where;
  }
  /** A combination's diff as it is: monospace in its own scrolling box, added lines on
      the good tint, removed ones on the critical tint, hunk heads quiet, nothing else
      marked. Lines are read as `tree._lines` reads them: the hunk head's counts say
      where a hunk ends, so a removed "-- x" line is not taken for a file head. */
  function diffView(text) {
    var left = 0, right = 0, inHunk = false, rows = [];
    var lines = String(text).replace(/\r\n?/g, "\n").replace(/\n$/, "").split("\n");
    for (var i = 0; i < lines.length; i++) {
      var line = lines[i], head = /^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@/.exec(line), mark = line.charAt(0), kind = "";
      if (head) {
        left = head[1] == null ? 1 : Number(head[1]); right = head[2] == null ? 1 : Number(head[2]);
        inHunk = true; kind = "diff-hunk";
      } else if ((left > 0 || right > 0) && (mark === "+" || mark === "-" || mark === " " || !line)) {
        if (mark !== "+") left--;
        if (mark !== "-") right--;
        kind = mark === "+" ? "diff-add" : mark === "-" ? "diff-del" : "";
      } else if (inHunk && (mark === "+" || mark === "-") && !/^(---|\+\+\+) /.test(line)) {
        kind = mark === "+" ? "diff-add" : "diff-del";  // past a short count, still the hunk's
      } else if (mark !== "\\") {
        left = right = 0; inHunk = false;
      }
      rows.push(kind ? html`<span class="${kind}">${line}</span>` : html`<span>${line}</span>`);
    }
    return html`<pre class="diff mono" tabindex="0"><span class="diff-body">${rows}</span></pre>`;
  }
  function cardLines(p) {
    var c = combo(p.id);
    if (!c) return ["", ""];
    var head = "Combination " + c.n + " · round " + c.round;
    if (p.cs) {
      var per = (c.cases[st.tries.full_stage] || {})[p.cs];
      if (per == null) { var stages = Object.keys(c.cases); for (var i = stages.length - 1; i >= 0; i--) if (c.cases[stages[i]][p.cs] != null) { per = c.cases[stages[i]][p.cs]; break; } }
      return [head, EK.shortCase(p.cs) + ": " + EK.better(per), lookWords(c)];
    }
    if (p.setting) {
      var meta = paramOf(p.setting) || { type: "" };
      return [head, labelOf(p.setting) + ": " + EK.valueText(meta, c.params[p.setting]), "Today: " + EK.valueText(meta, meta.default)];
    }
    return [head, c.failed || c.pct == null ? "Did not finish" : EK.better(c.pct), c.diff_stat ? diffWords(c.diff_stat) : lookWords(c)];
  }

  function viewWidth() {
    var panel = document.getElementById("view-panel");
    return Math.max(300, Math.round((panel && panel.clientWidth) || Math.min(1216, window.innerWidth - 64)));
  }
  var plotPoints = [];
  function viewHtml() {
    var t = st.tries, phone = EK.isPhone(), W = viewWidth();
    if (!t || !t.available || !t.combinations.length) {
      var s = st.status, msg = s.phase === "queued" ? "Waiting for its turn." : s.phase === "starting" || s.phase === "search" ? "The first tries land here soon." : "No tries to show.";
      plotPoints = [];
      return html`<div class="plot-empty"><p class="type-body">${msg}</p></div>`;
    }
    var family = !!st.chosen && decisionOf() === "use";
    var keepWord = whatOf() === "code" ? "today’s code" : whatOf() === "data" ? "today’s data" : "today’s settings";
    if (st.view === "tries") {
      var say = sayLine(t, family);
      var planH = isLive() && st.status.time && st.status.time.hours ? st.status.time.hours * 0.85 : 0;
      var p = EK.triesPlot(t, { W: W, H: phone ? 250 : 380, phone: phone, chosen: st.chosen, family: family, keepWord: keepWord, planH: planH });
      plotPoints = [{ el: "tries", points: p.points }];
      return html`${say ? html`<p class="type-body view-say">${say}</p>` : ""}${EK.raw(p.svg)}${EK.triesLegend(t, { family: family })}`;
    }
    if (st.view === "cases") {
      var cv = EK.casesView(t, { W: W, phone: phone, chosen: st.chosen });
      plotPoints = [{ el: "cases", points: cv.points }];
      var weakest = null;
      Object.keys(cv.tallies).forEach(function (cs) { var v = cv.tallies[cs]; if (v[1] >= 6 && (!weakest || v[0] / v[1] < weakest[1])) weakest = [cs, v[0] / v[1], v]; });
      return html`<div class="cases-wrap">${EK.raw(cv.svg)}</div>
        <div class="plot-legend"><span><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.2" style="fill:var(--ink-1)"/></svg>full look</span>
        <span><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.3"/></svg>quick look</span>
        <span>right column: tries better than today on that case</span></div>
        ${weakest ? html`<p class="type-small ink-2">Hardest case: ${EK.shortCase(weakest[0])}, better in ${weakest[2][0]} of ${weakest[2][1]} full looks.</p>` : ""}`;
    }
    if (st.view === "code") { plotPoints = []; return codeView(t); }
    if (!st.paramOrder || st.paramOrder.length !== (t.parameters || []).length) {
      st.paramOrder = (t.parameters || []).slice().sort(function (a, b) { return EK.settingWeight(t, b) - EK.settingWeight(t, a); }).map(function (p) { return p.name; });
    }
    var params = st.paramOrder.map(function (n) { return (t.parameters || []).filter(function (p) { return p.name === n; })[0]; }).filter(Boolean);
    var shown = st.showAllSettings ? params : params.slice(0, 6);
    var cols = phone ? 1 : W > 1100 ? 3 : 2, pw = Math.floor((W - (cols - 1) * 16) / cols) - 28;
    plotPoints = [];
    var panels = shown.map(function (p) {
      var sp = EK.settingsPanel(t, p, { W: pw, H: 236, chosen: st.chosen, label: labelOf(p.name) });
      plotPoints.push({ el: "setting", setting: p.name, points: sp.points });
      return html`<div class="setting-panel"><h3>${labelOf(p.name)}</h3><span class="type-small ink-2">${sp.good || "Today: " + EK.valueText(p, p.default)}</span>${EK.raw(sp.svg)}</div>`;
    });
    return html`<p class="type-small ink-2">${EK.plural(params.length, "setting")} tuned, the ones that moved the result most first. The band: values in tries over 1 % better.</p>
      <div class="settings-grid">${panels}</div>
      ${params.length > 6 && !st.showAllSettings ? html`<p>${EK.link("Show all " + params.length, { act: "all-settings" })}</p>` : ""}`;
  }
  /** A code study's third view: what each combination changed in the source, the best first, then
      the newest. No figures here: they stay in Tries, on one base, so two views never disagree. */
  function codeView(t) {
    var r = st.results, done = decisionOf() && ["use", "keep", "worse", "single"].indexOf(decisionOf()) >= 0 && r && r.best_id;
    var bestId = done ? r.best_id : (st.status.best || {}).id;
    var all = t.combinations.filter(function (c) { return !c.base && c.diff_stat; })
      .sort(function (a, b) { return (b.id === bestId) - (a.id === bestId) || b.n - a.n; });
    if (!all.length) return html`<div class="plot-empty"><p class="type-body">No change to the code yet.</p></div>`;
    var shown = all.slice(0, 30);
    return html`<p class="type-small ink-2">${EK.plural(all.length, "combination")} tried, each a change to today’s code; its diff is one click away.</p>
      <div class="cases-wrap"><table class="changed-table"><thead><tr><th>Combination</th><th>What it changed</th><th><span class="sr-only">Details</span></th></tr></thead><tbody>
      ${shown.map(function (c) {
        var mark = c.id === bestId ? (done ? " · the one the final check checked" : " · best so far, not checked") : "";
        return html`<tr><td>No. ${c.n} · round ${c.round}${c.failed ? " · did not finish" : ""}${mark ? html`<br><span class="ink-2">${mark.slice(3)}</span>` : ""}</td>
          <td>${diffWords(c.diff_stat)}</td><td>${EK.button("Details", { kind: "quiet", size: "s", act: "details", arg: c.id, label: "Details of combination " + c.n })}</td></tr>`;
      })}</tbody></table></div>
      ${all.length > shown.length ? html`<p class="type-small ink-2">${all.length - shown.length} older not shown.</p>` : ""}`;
  }
  /** One sentence over the Tries plot: what is new, or what is marked. */
  function sayLine(t, family) {
    var d = decisionOf();
    if (family) return "The chosen combination and its family, back to today’s " + whatOf() + ".";
    if (d && st.chosen) return "The checked combination is marked.";
    var best = (t.best || []).slice(-1)[0], running = (t.running || []).length;
    if (!isLive() || !best) return "";
    var ago = Math.max(0, t.now_h - best.h);
    var when = best.id === t.base ? "" : ago < 1 ? "A new best " + Math.max(1, Math.round(ago * 60)) + " min ago." : "Nothing better for " + EK.hours(ago) + ".";
    return (when + (running ? " " + EK.plural(running, "try", "tries") + " running now." : "")).trim();
  }
  function drawView() {
    var panel = document.getElementById("view-panel");
    if (!panel) return;
    syncCounts();
    var markup = String(viewHtml());
    if (markup === st.lastPlot) return;
    st.lastPlot = markup;
    var had = document.activeElement && document.activeElement.closest && document.activeElement.closest("#view-panel svg");
    var which = had ? had.getAttribute("data-plot") + (had.getAttribute("data-setting") || "") : null;
    panel.innerHTML = markup;
    wirePlots(panel);
    if (which) {
      var again = EK.$$("svg[data-plot]", panel).filter(function (s) { return s.getAttribute("data-plot") + (s.getAttribute("data-setting") || "") === which; })[0];
      if (again) again.focus();
    }
    var chip = document.getElementById("chip-slot");
    if (chip) chip.innerHTML = String(chipHtml());
  }
  function chipHtml() {
    var c = chosenCombo();
    return html`${EK.chosenChip(c, chipFigure(c))}${c ? EK.button("Details", { kind: "quiet", size: "s", act: "details", arg: c.id, label: "Details of combination " + c.n }) : ""}`;
  }
  /** The chip's figure: the verdict document's `learned_from` (`learned_pct`) for the best combination,
      the strip's best so far for it while the run searches, else its own. */
  function chipFigure(c) {
    var r = st.results, best = c && r && r.best_id === c.id && r.learned_pct != null;
    var now = c && st.status && isLive() && st.status.best && st.status.best.id === c.id && st.status.best.pct != null;
    return { pct: best ? r.learned_pct : now ? st.status.best.pct : null, direction: (goalOf() || {}).direction,
      after: c ? chipAfter(decisionOf(), r && r.best_id, c, ["done", "stopped", "failed"].indexOf(st.status.phase) >= 0) : null };
  }
  /** Once the final check has decided, the chip says no figure (a figure there reads as a live search):
      on the combination it checked, the decision ("final check: keep"); on any other, nothing. Null
      while a search is live, and the chip says the figure, not checked. A skipped, stopped or missing
      check, or a run that ended without one, says no figure either. */
  function chipAfter(decision, checkedId, c, finished) {
    var word = { use: "use it", keep: "keep", worse: "worse", single: "one case" }[decision];
    if (!word) return decision || finished ? "" : null;
    return c && c.id === checkedId ? "final check: " + word : "";
  }
  function chosenCombo() { var c = st.chosen && combo(st.chosen); return c && !c.base ? c : null; }

  // ------------------------------------------------------------------ hover cards, taps and the keyboard
  function wirePlots(panel) {
    EK.$$("svg[data-plot]", panel).forEach(function (svg) {
      var group = plotPoints.filter(function (g) { return g.el === svg.getAttribute("data-plot") && (!g.setting || g.setting === svg.getAttribute("data-setting")); })[0];
      if (!group) return;
      var pts = group.points.slice().sort(function (a, b) { return a.x - b.x || a.y - b.y; });
      var vb = svg.viewBox.baseVal, cursor = -1;
      function scale() { var r = svg.getBoundingClientRect(); return { r: r, s: r.width / vb.width }; }
      function nearest(ev) {
        var m = scale(), x = (ev.clientX - m.r.left) / m.s, y = (ev.clientY - m.r.top) / m.s, best = null, bd = (ev.pointerType === "touch" ? 22 : 14) / m.s;
        pts.forEach(function (p, i) { var dd = Math.hypot(p.x - x, p.y - y); if (dd < bd) { bd = dd; best = i; } });
        return best;
      }
      function show(i, pinned) {
        if (i == null || !pts[i]) { hide(); return; }
        var p = pts[i], m = scale(), host = document.getElementById("view-panel"), hr = host.getBoundingClientRect();
        var left = m.r.left - hr.left + p.x * m.s + 16, top = m.r.top - hr.top + p.y * m.s - 100;
        if (left + 260 > hr.width) left = Math.max(0, m.r.left - hr.left + p.x * m.s - 264);
        if (top < 0) top = m.r.top - hr.top + p.y * m.s + 16;
        var old = document.getElementById("hover-card");
        if (old) old.remove();
        var c = combo(p.id), chosen = st.chosen === p.id;
        host.insertAdjacentHTML("beforeend", String(EK.hoverCard(cardLines(p), { hint: chosen ? "Followed · Enter to stop" : (EK.isPhone() ? "Tap Follow to keep it" : "Click to follow it"), close: !!pinned })));
        var card = document.getElementById("hover-card");
        card.style.left = left + "px"; card.style.top = top + "px";
        if (pinned) {
          card.classList.add("is-pinned");
          card.insertAdjacentHTML("beforeend", String(html`<span class="actions">${EK.button(chosen ? "Stop following" : "Follow it", { kind: "secondary", size: "s", act: chosen ? "unchoose" : "choose", arg: p.id })}
            ${EK.button("Details", { kind: "quiet", size: "s", act: "details", arg: p.id })}</span>`));
        }
        svg.setAttribute("aria-describedby", "hover-card");
        st.hover = { svg: svg, i: i };
        void c;
      }
      function hide() {
        var old = document.getElementById("hover-card");
        if (old) old.remove();
        svg.removeAttribute("aria-describedby");
        st.hover = null;
      }
      svg.addEventListener("pointermove", function (ev) { if (ev.pointerType !== "touch") show(nearest(ev)); });
      svg.addEventListener("pointerleave", function (ev) { if (ev.pointerType !== "touch") hide(); });
      svg.addEventListener("click", function (ev) {
        var i = nearest(ev);
        if (i == null) { hide(); return; }
        if (EK.isPhone() || ev.pointerType === "touch") { show(i, true); return; }
        choose(pts[i].id);
      });
      svg.addEventListener("keydown", function (ev) {
        if (!pts.length) return;
        if (ev.key === "ArrowRight" || ev.key === "ArrowDown") { cursor = Math.min(pts.length - 1, cursor + 1); show(cursor); ev.preventDefault(); }
        else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") { cursor = Math.max(0, cursor < 0 ? 0 : cursor - 1); show(cursor); ev.preventDefault(); }
        else if (ev.key === "Home") { cursor = 0; show(cursor); ev.preventDefault(); }
        else if (ev.key === "End") { cursor = pts.length - 1; show(cursor); ev.preventDefault(); }
        else if ((ev.key === "Enter" || ev.key === " ") && cursor >= 0) { ev.preventDefault(); var id = pts[cursor].id; if (st.chosen === id) unchoose(); else choose(id); }
      });
      svg.addEventListener("blur", function () { setTimeout(function () { if (!document.activeElement || !document.activeElement.closest || !document.activeElement.closest("#hover-card")) hide(); }, 0); });
    });
  }
  function hoverEscape() {
    var card = document.getElementById("hover-card");
    if (!card) return false;
    card.remove();
    return true;
  }
  function choose(id) {
    st.chosen = id; st.chosenSet = true;
    st.lastPlot = "";
    drawView();
  }
  function unchoose() { st.chosen = null; st.chosenSet = true; st.lastPlot = ""; drawView(); }

  // ------------------------------------------------------------------ below the views
  function changedTable() {
    var ch = ((st.results || {}).changed || []).filter(function (c) { return c.changed; });
    if (isCode()) return codeChange(ch);
    if (!ch.length) return html`<p class="type-body ink-2">${(st.results || {}).is_seed ? "Nothing: the starting point stayed the best." : "No changes."}</p>`;
    return html`<div class="cases-wrap"><table class="changed-table"><thead><tr><th>Setting</th><th>Today</th><th>New</th></tr></thead><tbody>
      ${ch.map(function (c) { return html`<tr><td>${c.label || c.name}${c.help ? html`<br><span class="ink-2">${c.help}</span>` : ""}</td><td>${c.old_text}</td><td class="new">${c.new_text}</td></tr>`; })}</tbody></table></div>`;
  }
  /** A code study's hand-over: the change against today's code (its diff in lines, and Details for the
      diff itself); a bake-off's branches are on the page that shows them side by side. */
  function codeChange(ch) {
    var r = st.results || {};
    if (isBakeoff()) return html`<p class="type-body ink-2">Each branch’s own answer is on the page that shows them side by side.</p>`;
    if (!ch.length) return html`<p class="type-body ink-2">${r.is_seed ? "Nothing: today’s code stayed the best." : "No change to the code."}</p>`;
    var c = ch[0];
    return html`<div class="cases-wrap"><table class="changed-table"><thead><tr><th>Source</th><th>Today’s code</th><th>The change</th></tr></thead><tbody>
      <tr><td>${c.label || "The source"}</td><td class="mono">${c.old_text}</td><td class="new">${c.new_text}</td></tr></tbody></table></div>
      ${r.best_id && combo(r.best_id) ? html`<div class="actions">${EK.button("Show the change", { kind: "secondary", act: "details", arg: r.best_id })}</div>` : ""}`;
  }
  function handOver() {
    var r = st.results;
    if (!r || st.status.phase !== "done") return "";
    var d = decisionOf();
    var dl = (r.downloads || []).map(function (x) {
      return EK.link(x.label, { href: EK.withToken(EK.studyPath(st.slug, "/download/" + EK.q(x.name) + "?run=" + EK.q(st.run))), download: true, icon: EK.gl("file", 18) });
    });
    var keepIt = d !== "use", code = isCode();
    var title = code ? (keepIt ? "Code to keep" : "The code change to use") : (keepIt ? "Settings to keep" : "Settings to use");
    return html`<section class="section" aria-labelledby="hand-title"><h2 id="hand-title">${title}</h2>
      ${keepIt ? html`<p class="type-body strong">Nothing to change: ${EK.keepWords({ what: whatOf() }).replace(/^Keep /, "keep ")}${(r.context || {}).compared_with ? " (" + r.context.compared_with + ")" : ""}.</p>
        <p class="type-small ink-2">${code && isBakeoff() ? "Keep today’s code: no branch is shown to beat it."
          : code ? "Keep today’s code. Below it is the change that came closest: not proven on held-back cases, so not recommended."
          : "Hand over today’s settings. Below them is what the search found: not proven on held-back cases, so not recommended."}</p>` : ""}
      ${changedTable()}
      <div class="actions">${keepIt && hasExport("settings_json") ? EK.button("Show today’s settings", { act: "show-today" }) : ""}
        ${hasExport("settings_json") ? EK.button(keepIt ? "Settings found, not recommended" : "Show the settings file", { kind: "secondary", act: "show-settings" }) : ""}
        ${EK.button("Copy a summary", { kind: "secondary", act: "copy-summary" })}
        ${EK.link("Open the report", { href: EK.withToken(EK.studyPath(st.slug, "/download/report?run=" + EK.q(st.run) + "&view=1")), target: "_blank" })}</div>
      <div class="link-row">${dl}${EK.link("Save the study as zip", { href: EK.withToken(EK.studyPath(st.slug, "/download/study")), download: true })}</div></section>`;
  }
  function nextSteps() {
    var s = st.status, out = [];
    if (s.can_continue) out.push(EK.button("Continue for " + EK.hours((st.doc.study.budget || {}).hours || 1) + " more", { kind: "secondary", act: "continue" }));
    if (s.phase === "done" || s.phase === "stopped") out.push(EK.button("Start a new study from this", { kind: "secondary", act: "next" }));
    out.push(EK.link("Compare with…", { href: "#/compare?a=" + EK.q(st.slug + "/" + st.run) }));
    return html`<section class="section" aria-labelledby="next-title"><h2 id="next-title">What next</h2><div class="actions">${out}</div></section>`;
  }
  /** What the AI spent of its cap: the verdict's figure (`r.spent`) once there is one, the live one
      (`s.spent`) before it; null for a run without AI. A fact, not a claim. */
  function spentNow() {
    var r = st.results, s = st.status;
    return (r && r.spent) || (s.spent && s.spent.line ? s.spent : null);
  }
  function details() {
    var r = st.results, s = st.status;
    var parts = [];
    var spent = spentNow();
    if (!r && spent) parts.push(html`<p class="type-small" id="details-spent">${spent.line}</p>`);
    if (r) {
      var c = r.context || {};
      parts.push(html`<div class="facts-grid">
        <div class="fact"><span class="type-label">Time per case</span><span>${EK.duration(c.time_per_case_s)}</span></div>
        <div class="fact"><span class="type-label">Time in total</span><span>${EK.hours(c.hours)}${c.took ? " · took " + c.took : ""}</span></div>
        <div class="fact"><span class="type-label">Combinations</span><span>${c.tried != null ? EK.int(c.tried) + " tried" : ""}</span></div>
        <div class="fact"><span class="type-label">Starting point</span><span>${c.compared_with || ""}</span></div>
        ${spent ? html`<div class="fact"><span class="type-label">AI spend</span><span id="details-spent">${spent.line}</span></div>` : ""}</div>`);
      // How much of the plan ran, when less did: the verdict document's own sentence.
      if (r.how_it_ran) parts.push(html`<p class="type-small">${r.how_it_ran}</p>`);
      if (r.final && r.final.next) parts.push(html`<p class="type-small ink-2">${r.final.next}</p>`);
      if (r.final && r.final.recheck) parts.push(html`<div class="actions">${EK.button(r.final.recheck.label, { kind: r.final.recheck.over ? "quiet" : "secondary", act: "recheck" })}</div><p class="type-small ink-2">${r.final.recheck.why}</p>`);
      var dir = (goalOf() || {}).direction, measure = (goalOf() || {}).label || "the measure";
      var table = function (title, rows, change) {
        if (!rows || !rows.length) return "";
        return html`<div class="sub-section"><h3>${title}</h3><div class="cases-wrap"><table class="changed-table"><thead><tr><th>Case</th><th>Runs</th><th>Start</th><th>Best</th><th>${change}</th></tr></thead><tbody>
          ${rows.map(function (x) { return html`<tr><td class="mono">${x.case}</td><td>${x.runs}${x.failed ? " · " + x.failed + " failed" : ""}</td><td>${EK.num(x.start)}</td><td>${EK.num(x.best)}</td><td>${EK.change(x.change_pct, dir)}</td></tr>`; })}</tbody></table></div></div>`;
      };
      parts.push(table("Held-back cases", (r.by_case || {}).held, "Change in " + measure + " (mean of the paired runs)"),
        table("Cases it learned from", (r.by_case || {}).learned, "Change in " + measure + " (of the means)"));
      if ((r.kpis || []).length) {
        parts.push(html`<div class="sub-section"><h3>Every measure</h3><div class="cases-wrap"><table class="changed-table"><thead><tr><th>Measure</th><th>Study cases</th><th>Held-back cases</th></tr></thead><tbody>
          ${r.kpis.map(function (k) { return html`<tr><td>${k.says}${k.unit ? " (" + k.unit + ")" : ""}</td><td>${EK.num(k.training.start)} → ${EK.num(k.training.best)}</td><td>${k.test ? EK.num(k.test.start) + " → " + EK.num(k.test.best) : "–"}</td></tr>`; })}</tbody></table></div>
          ${(r.rules || []).length ? html`<p class="type-small ink-2">Rules: ${r.rules.join(" · ")}</p>` : ""}</div>`);
      }
    }
    if ((s.failures || []).length) {
      var failing = st.tries ? st.tries.combinations.filter(function (c) { return c.failed_runs; }).slice(-12).reverse() : [];
      parts.push(html`<div class="sub-section"><h3>What went wrong</h3>
        ${s.failures.map(function (fl) { return EK.notice("warning", fl.count + " × " + fl.message + (fl.todo ? " " + fl.todo : "")); })}
        ${failing.length ? html`<p class="type-small ink-2">Show the output of a combination with failed runs:</p><div class="actions">${failing.map(function (c) {
          return EK.button("No. " + c.n, { kind: "secondary", size: "s", act: "details", arg: c.id, label: "Show combination " + c.n + " and its failed runs" });
        })}</div>` : ""}</div>`);
    }
    if (s.gated_line) parts.push(html`<p class="type-small ink-2">${s.gated_line}</p>`);
    if (s.best && s.best.line && !isLive()) parts.push(html`<p class="type-small ink-2">${s.best.line}</p>`);
    return html`<details class="section-fold"><summary>Details</summary><div class="sub-section">${parts}</div></details>`;
  }

  // ------------------------------------------------------------------ the page
  function header() {
    var t = st.tries, tuned = ((t && t.parameters) || []).length, cases = ((st.doc.study.cases || {}).training || []).length;
    var runs = st.doc.runs || [];
    // A code study changes code: its header counts no settings (its fixed ones are not tuned).
    var sub = (tuned && !isCode() ? EK.plural(tuned, "setting") + " · " : "") + EK.plural(cases, "study case") + " · " + heldCount() + " held back";
    var lines = headLines(runs, st.run, (st.doc.state || {}).earlier || null);
    return html`${EK.titleBlock("Study · " + EK.when(runStartedAt()), st.doc.study.name, sub,
      html`<div class="actions">${EK.link("Past runs (" + runs.length + ")", { act: "runs" })}${EK.link("Set up", { href: "#/setup/" + EK.q(st.slug) })}</div>`)}${lines.map(function (line) {
        return html`<p class="type-small head-note">${EK.figures(line)}</p>`;
      })}`;
  }
  /** The header's lines under the title: the shown run's branch, when `study pr` made one for it
      ("Branch … pushed to origin"); and on the latest run's page, when it does not say use and an
      earlier run did, "An earlier run (<id>) says use: <its verdict line>" (`earlier`, the server's). */
  function headLines(runs, run, earlier) {
    var out = [], shown = runs.filter(function (r) { return r.id === run; })[0] || runs[runs.length - 1];
    if (shown && shown.branch && shown.branch.line) out.push(shown.branch.line);
    var latest = runs[runs.length - 1];
    if (earlier && earlier.says && shown && latest && shown.id === latest.id) out.push(earlier.says);
    return out;
  }
  function runStartedAt() { var r = (st.doc.runs || []).filter(function (x) { return x.id === st.run; })[0]; return r ? r.started_at : ""; }
  function notices() {
    var s = st.status, out = [];
    if (s.phase === "search" && /again/.test(s.sentence || "")) out.push(EK.notice("info", "Continued where it stopped, after a restart."));
    if (s.busy_line && isLive()) out.push(EK.notice("warning", "Your computer is busy. Tries may run slower.", { text: "Is it still fair?", act: "busy" }));
    // Before and while the run: what to hear besides the plan (too few held-back cases for a firm answer).
    if (isLive()) (s.notes || []).forEach(function (note) { out.push(EK.notice("info", note)); });
    var latest = (st.doc.runs || []).slice(-1)[0];
    if (latest && latest.id !== st.run) out.push(EK.notice("info", "An older run of this study.", { text: "See the latest", act: "latest" }));
    return EK.join(out);
  }
  /** The view tabs' counts, from the tries the plot draws: combinations tried, cases, settings tuned and
      combinations with a change to the code; null before there are tries. */
  function tabCounts(t) {
    if (!t || !t.available) return null;
    var tried = (t.combinations || []).filter(function (c) { return !c.base; });
    return { tries: tried.length, cases: (t.cases || []).length, settings: (t.parameters || []).length,
      code: tried.filter(function (c) { return c.diff_stat; }).length };
  }
  /** Bring the tabs' counts up to the plot (a live run redraws the plot, never the tabs). */
  function syncCounts() {
    var counts = EK.isPhone() ? null : tabCounts(st.tries);
    if (!counts) return;
    EK.$$(".view-head .tab[data-arg]").forEach(function (tab) {
      var n = counts[tab.getAttribute("data-arg")];
      if (n == null) return;
      var el = tab.querySelector(".tab-count");
      if (!el) { el = document.createElement("span"); el.className = "tab-count"; tab.appendChild(el); }
      if (el.textContent !== String(n)) el.textContent = String(n);
    });
  }
  function draw(main) {
    var phone = EK.isPhone();
    var counts = tabCounts(st.tries);
    EK.mount(main, html`<div class="page"><div class="page-inner"><div id="study-head">${header()}</div><div id="study-notices">${notices()}</div>
      <div id="strip-slot">${statusStrip()}</div>
      <div class="sub-section"><div class="view-head">${EK.viewTabs(st.view, phone ? null : counts, viewKeys())}<div id="chip-slot" class="actions" style="padding:6px 0">${chipHtml()}</div></div>
        <div class="view-panel" id="view-panel" role="tabpanel" aria-labelledby="tab-${st.view}"></div></div>
      ${handOver()}${nextSteps()}${details()}</div></div>`, acts(main));
    st.lastStrip = String(statusStrip());
    st.lastNotices = String(notices());
    st.lastPlot = "";
    drawView();
    companion();
  }
  function update(main) {
    var slot = document.getElementById("strip-slot");
    if (!slot) return;
    var strip = String(statusStrip());
    if (strip !== st.lastStrip) {
      var focusedAct = document.activeElement && slot.contains(document.activeElement) ? document.activeElement.getAttribute("data-act") : null;
      slot.innerHTML = strip;
      st.lastStrip = strip;
      if (focusedAct) { var again = EK.$('[data-act="' + focusedAct + '"]', slot); if (again) again.focus(); }
    }
    var n = document.getElementById("study-notices");
    if (n) { var nh = String(notices()); if (nh !== st.lastNotices) { n.innerHTML = nh; st.lastNotices = nh; } }
    var spentEl = document.getElementById("details-spent"), spent = spentNow();
    if (spentEl && spent && spentEl.textContent !== spent.line) spentEl.textContent = spent.line;  // Details stays open
    if (!st.hover) drawView();
    companion();
    if (!isLive()) { EK._moveFocus = false; draw(main); watchRuns(); }
  }

  // ------------------------------------------------------------------ actions
  function acts(main) {
    return {
      view: function (el) {
        st.view = el.getAttribute("data-arg");
        EK.$$(".tab", main).forEach(function (t) { var on = t.getAttribute("data-arg") === st.view; t.classList.toggle("is-on", on); t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1; });
        document.getElementById("view-panel").setAttribute("aria-labelledby", "tab-" + st.view);
        st.lastPlot = "";
        drawView();
        history.replaceState(null, "", location.pathname + location.search + "#/study/" + EK.q(st.slug) + "?run=" + EK.q(st.run) + "&view=" + st.view);
      },
      tabkey: function (el, ev) {
        var order = viewKeys(), i = order.indexOf(st.view);
        if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
        ev.preventDefault();
        var next = order[(i + (ev.key === "ArrowRight" ? 1 : 2)) % 3];
        var btn = EK.$('.tab[data-arg="' + next + '"]', main);
        btn.focus(); btn.click();
      },
      choose: function (el) { choose(el.getAttribute("data-arg")); },
      unchoose: function () { unchoose(); var t = EK.$(".tab.is-on", main); if (t) t.focus(); },
      "hover-close": function () { hoverEscape(); },
      "all-settings": function () { st.showAllSettings = true; st.lastPlot = ""; drawView(); },
      stop: function () {
        var queued = st.status.phase === "queued";
        EK.confirm(queued ? "Take it out of the queue?" : st.status.phase === "check" ? "Stop the final check?" : "Stop the study?",
          queued ? "It will not start. Its set-up is kept." : "Everything so far is kept. You can carry on later.", queued ? "Remove from queue" : "Stop").then(function (yes) {
          if (!yes) return;
          EK.post(EK.studyPath(st.slug, "/stop" + runQ())).then(function (res) {
            if (res.dequeued) { EK.toast("done", "Taken out of the queue"); EK.go("#/setup/" + EK.q(st.slug)); return; }
            EK.toast("done", "Stopping…");
          }).catch(EK.fail);
        });
      },
      check: function () { EK.post(EK.studyPath(st.slug, "/final-check" + runQ())).then(function () { EK.toast("done", "The final check started"); EK.rerender(); }).catch(EK.fail); },
      recheck: function () {
        var r = st.results.final.recheck;
        EK.post(EK.studyPath(st.slug, "/final-check" + runQ()), { seeds: r.seeds }).then(function () { EK.toast("done", "Checking again with more runs"); EK.go(EK.studyHref(st.slug)); }).catch(EK.fail);
      },
      resume: function () { EK.post(EK.studyPath(st.slug, "/start"), { resume: st.run }).then(function () { EK.toast("done", "Carrying on"); EK.rerender(); }).catch(EK.fail); },
      restart: function () {
        // A bake-off starts again as itself: the same branches, never a search.
        var again = isBakeoff() ? EK.post(EK.studyPath(st.slug, "/bakeoff"), { refs: st.status.bakeoff_refs }) : EK.post(EK.studyPath(st.slug, "/start"));
        again.then(function (res) { EK.go(EK.studyHref(st.slug, res.run)); }).catch(EK.fail);
      },
      "continue": function () {
        var hours = (st.doc.study.budget || {}).hours || 1;
        EK.post(EK.studyPath(st.slug, "/continue"), { run: st.run, hours: hours }).then(function (res) { EK.toast("done", "Searching on for " + EK.hours(hours)); EK.go(EK.studyHref(st.slug, res.run)); }).catch(EK.fail);
      },
      next: function () {
        EK.post(EK.studyPath(st.slug, "/next" + runQ()), { name: st.doc.study.name + " (next)" }).then(function (doc) { EK.go("#/setup/" + EK.q(doc.slug)); }).catch(EK.fail);
      },
      "save-settings": function () { location.href = EK.withToken(EK.studyPath(st.slug, "/download/settings_json?run=" + EK.q(st.run))); EK.toast("done", "Settings file saved"); },
      "copy-summary": function () {
        var text = (st.results && st.results.summary_text) || "";
        EK.copy(text).then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Summary copied" : "Could not copy"); });
      },
      "show-today": function () {
        return settingsText().then(function (text) {
          var today = text;
          try {
            var parsed = JSON.parse(text);
            ((st.results && st.results.changed) || []).forEach(function (c) { if (c.changed && c.name in parsed && c.old !== undefined) parsed[c.name] = c.old; });
            today = JSON.stringify(parsed, null, 2);
          } catch (e) { /* not JSON: shown as it is */ }
          EK.openDrawer({ title: "Today’s settings", sub: ((st.results || {}).context || {}).compared_with || "What the application runs with now", wide: true,
            body: html`<p class="type-small ink-2">The verdict says keep these. Send this file.</p><pre class="code">${today}</pre><div class="actions">${EK.button("Copy", { act: "copy" })}</div>`,
            acts: { copy: function () { EK.copy(today).then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Copied" : "Could not copy"); }); } } });
        }).catch(EK.fail);
      },
      "show-settings": function () {
        var keepIt = decisionOf() !== "use";
        return settingsText().then(function (text) {
          EK.openDrawer({ title: keepIt ? "Settings the search found" : "The settings file", sub: keepIt ? "Not recommended: not proven on held-back cases" : "Use these in the application", wide: true,
            body: html`<pre class="code">${text}</pre><div class="actions">${EK.button("Copy", { kind: "secondary", act: "copy" })}${EK.button("Save it", { act: "save" })}</div>`,
            acts: { copy: function () { EK.copy(text).then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Copied" : "Could not copy"); }); },
              save: function () { location.href = EK.withToken(EK.studyPath(st.slug, "/download/settings_json?run=" + EK.q(st.run))); } } });
        }).catch(EK.fail);
      },
      why: function () {
        EK.openDrawer({ title: "The rule", body: html`<p class="type-body">${(st.results && st.results.final && st.results.final.rule) || "The app says use it when the chance of a gain is at least 9 in 10 and the worst plausible case is within 1 % of today."}</p>
          <p class="type-small ink-2">The final check runs the best combination and today’s ${whatOf()} on the held-back cases, which the search never saw.</p>` });
      },
      busy: function () {
        EK.openDrawer({ title: "Your computer is busy", body: html`<p class="type-body">Other programs used this computer while the study ran.</p>
          <p class="type-body">Tries then take longer and may find a little less. The answer stays fair: the final check runs today’s ${whatOf() === "both" ? "settings and data" : whatOf()} and the change side by side, under the same load.</p>
          <p class="type-small ink-2">You need not stop the study. For the best result, close heavy programs while it runs.</p>` });
      },
      latest: function () { EK.go(EK.studyHref(st.slug)); },
      runs: runsDrawer,
      branches: branchesDrawer,
      candidates: function () { EK.go("#/compare?run=" + EK.q(st.slug + "/" + st.run)); },
      details: function (el) { return comboDrawer(el.getAttribute("data-arg")); }
    };
  }
  /** One combination in full: what it changed, how it did case by case, and the output of its failed runs. */
  function comboDrawer(id) {
    var c = combo(id);
    if (!c) return;
    return EK.get(EK.studyPath(st.slug, "/tries/" + EK.q(id) + runQ())).then(function (d) {
      var params = (d.parameters || []).filter(function (p) { return p.changed; });
      var per = c.cases[st.tries.full_stage] || c.cases[Object.keys(c.cases).slice(-1)[0]] || {};
      var failed = (d.evaluations || []).filter(function (e) { return String(e.ok) === "false" || e.ok === false; });
      var parent = d.parent_id && combo(d.parent_id);
      var body = html`<p class="type-body strong">${c.failed || c.pct == null ? "Did not finish" : EK.capital(EK.change(chipFigure(c).pct != null ? chipFigure(c).pct : c.pct, chipFigure(c).direction)) + " on the cases it learned from"
          + (chipFigure(c).after ? "; the final check’s verdict is the answer" : ", not checked")}</p>
        <p class="type-small ink-2">${lookWords(c)}${parent ? " · comes from combination " + (parent.base ? (c.diff_stat ? "0, today’s code" : "0, today’s settings") : parent.n) : ""}</p>
        <div class="sub-section"><h3 class="type-heading">What it changed</h3>
          ${c.diff_stat ? html`<p class="type-small ink-2">${diffWords(c.diff_stat)}</p>` : ""}
          ${d.diff ? diffView(d.diff) : d.diff === "" ? "" : params.length ? html`<div class="cases-wrap"><table class="changed-table"><thead><tr><th>Setting</th><th>Today</th><th>This one</th></tr></thead><tbody>
            ${params.map(function (p) { return html`<tr><td>${labelOf(p.name)}</td><td>${EK.valueText(p, p.default)}</td><td class="new">${EK.valueText(p, p.value)}</td></tr>`; })}</tbody></table></div>`
            : html`<p class="type-small ink-2">Nothing: these are today’s settings.</p>`}</div>
        <div class="sub-section"><h3 class="type-heading">Case by case</h3><div class="cases-wrap"><table class="changed-table"><tbody>
          ${Object.keys(per).sort().map(function (cs) { return html`<tr><td class="mono">${cs}</td><td>${EK.change(per[cs], chipFigure(c).direction)}</td></tr>`; })}</tbody></table></div></div>
        ${failed.length ? html`<div class="sub-section"><h3 class="type-heading">${EK.plural(failed.length, "failed run")}</h3>
          ${failed.slice(0, 6).map(function (e) {
            var out = (e.stderr_tail || e.stdout_tail || "").trim();
            return html`<div class="sub-section"><p class="type-small"><span class="mono">${e.instance}</span> · ${e.stage} · ${e.failure || "failed"}${e.duration_s ? " after " + EK.duration(Number(e.duration_s)) : ""}</p>
              ${out ? html`<pre class="code">${out}</pre>` : html`<p class="type-small ink-2">It printed nothing.</p>`}
              ${e.argv ? html`<details class="more-detail"><summary>How it was started</summary><pre class="code">${Array.isArray(e.argv) ? e.argv.join(" ") : e.argv}</pre></details>` : ""}</div>`;
          })}</div>` : ""}`;
      EK.openDrawer({ title: "Combination " + c.n + " · round " + c.round, wide: true, body: body });
    }).catch(EK.fail);
  }
  /** "Compare branches": the shared drawer (`EK.branchesDrawer`, drawer.js). */
  function branchesDrawer() { EK.branchesDrawer(st.slug, heldCount()); }
  /** The runs drawer's body: every run as the page last heard of it, newest first. */
  function runsBody() {
    var runs = (st.doc.runs || []).slice().reverse().map(function (r) {
      var mark = r.kind === "finished" ? EK.markOf({ kind: "finished", decision: r.decision, tone: r.tone }) : r.kind === "failed" ? "error" : "open";
      return Object.assign({}, r, { mark: mark });
    });
    return html`${EK.runList(runs, st.run, function (r) { return EK.studyHref(st.slug, r.id); })}
      ${runs.length > 1 ? html`<p>${EK.link("Compare two runs", { href: "#/compare?a=" + EK.q(st.slug + "/" + runs[0].id) + "&b=" + EK.q(st.slug + "/" + runs[1].id) })}</p>` : ""}`;
  }
  /** Open on what the page knows, then ask for the runs as they are now: the drawer follows. */
  function runsDrawer() {
    EK.openDrawer({ kind: "runs", title: "This study’s runs", sub: "Newest first", body: runsBody() });
    watchRuns();
  }

  EK.pages.study = { render: render };
})();
