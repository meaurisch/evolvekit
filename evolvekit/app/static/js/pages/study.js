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
    st = { slug: slug, gen: gen, run: query.run || "", view: ["tries", "cases", "settings"].indexOf(query.view) >= 0 ? query.view : "tries",
      chosen: null, chosenSet: false, doc: null, h: null, status: null, results: null, tries: null, hover: null, lastStrip: "", lastPlot: "", phase: null,
      showAllSettings: false, openRuns: query.runs === "1" };
    return Promise.all([EK.get(EK.studyPath(slug)), EK.get(EK.studyPath(slug, "/harness"))]).then(function (r) {
      if (!EK.isCurrent(gen)) return;
      st.doc = r[0]; st.h = r[1];
      if (!st.doc.runs || !st.doc.runs.length) { location.replace(((st.doc.study.step || 1) >= 4 ? "#/setup/" : "#/start/") + EK.q(slug)); return; }
      return load().then(function () {
        if (!EK.isCurrent(gen)) return;
        draw(main);
        if (st.openRuns) runsDrawer();
        EK.every(2500, function () {
          if (!st.status || !isLive()) return;
          load().then(function () { if (EK.isCurrent(gen)) update(main); }).catch(function () { /* the next poll tries again */ });
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
  function runQ() { return st.run ? "?run=" + EK.q(st.run) : ""; }

  function load() {
    return EK.get(EK.studyPath(st.slug, "/status" + runQ())).then(function (s) {
      st.status = s;
      if (!st.run) st.run = s.run;
      var wantResults = s.phase === "done" || s.phase === "stopped" || s.phase === "failed" || s.phase === "check";
      return Promise.all([
        EK.get(EK.studyPath(st.slug, "/tries?run=" + EK.q(st.run))).catch(function () { return null; }),
        wantResults ? EK.get(EK.studyPath(st.slug, "/results?run=" + EK.q(st.run))).catch(function () { return null; }) : Promise.resolve(null)
      ]);
    }).then(function (r) {
      st.tries = r[0];
      st.results = r[1];
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
  function whatOf() { return Object.keys((st.doc.study.vary || {}).data || {}).length && !Object.keys((st.doc.study.vary || {}).settings || {}).length ? "data" : "settings"; }
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
  function bestPct() { var b = (st.status.best || {}).pct; return b != null && b > 0 ? b : null; }

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
        caption: "Then " + EK.plural((s.rounds || {}).planned || 0, "round") + " and the final check", acts: [{ text: "Stop", kind: "secondary", act: "stop" }] });
    } else if (s.phase === "search") {
      var b = bestPct();
      Object.assign(x, { kicker: (cardState().continued ? "Searching again · " : "Searching · ") + roundWord(), head: b ? "Best so far: " + EK.better(b) : "Nothing better yet",
        tag: b ? "Not checked yet" : "", sub: s.now || s.sentence, caption: (s.time && s.time.finish_at ? "Done around " + s.time.finish_at : "") + (left ? " · " + left : ""),
        acts: [{ text: "Stop", kind: "secondary", act: "stop" }] });
    } else if (s.phase === "check") {
      Object.assign(x, { kicker: "Final check" + (ch && ch.planned ? " · " + ch.pairs + " of " + ch.planned + " runs" : ""), head: "No answer yet",
        sub: "Adding runs on " + EK.plural(heldCount(), "held-back case") + " while time remains.", right: ch && ch.mean != null ? "both" : "timeline",
        range: ch && ch.mean != null ? [ch.mean, ch.ci95 && ch.ci95[0], ch.ci95 && ch.ci95[1]] : null, caption: "So far, on held-back cases",
        acts: [{ text: "Stop the check", kind: "quiet", act: "stop" }] });
    } else if (s.phase === "failed") {
      Object.assign(x, { mark: "error", kickerTone: "critical", kicker: "Stopped by an error", head: "The study stopped in " + roundWord().replace(/ of \d+$/, ""),
        sub: String(s.error || "No reason recorded.").split("\n")[0].slice(0, 140), caption: EK.capital(roundWord()) + (s.time && s.time.used ? " · stopped at " + s.time.used : ""),
        acts: [s.can_resume ? { text: "Continue where it stopped", act: "resume" } : null, { text: "Start it again", kind: "secondary", act: "restart" }].filter(Boolean) });
    } else if (s.phase === "stopped" && !(f.state === "stopped")) {
      var b2 = bestPct();
      Object.assign(x, { kickerTone: "", kicker: "Stopped by you · " + roundWord().replace(/ of \d+$/, ""), head: b2 ? "Best so far: " + EK.better(b2) : "Stopped before a better one",
        tag: b2 ? "Not checked yet" : "", sub: s.can_check ? "Check it, or carry on searching." : s.can_resume ? "Carry on searching when you like." : "Tries up to the stop are kept.",
        caption: EK.capital(roundWord()) + (s.time && s.time.used ? " · stopped at " + s.time.used : ""), stoppedAt: true,
        acts: [s.can_check ? { text: "Run the final check", act: "check" } : null, s.can_resume ? { text: "Carry on", kind: s.can_check ? "secondary" : "primary", act: "resume" } : null].filter(Boolean) });
    } else if (d === "use") {
      Object.assign(x, { tone: "accent", mark: "use", kickerTone: "", kicker: "Final check done · " + EK.plural(f.n || heldCount(), "case"), head: "Use it: " + EK.better(f.mean),
        sub: EK.rangeCaption({ decision: "use", chance: f.chance, ci95: f.ci95 }).split(" · at best")[0], right: "range",
        range: [f.mean, f.ci95 && f.ci95[0], f.ci95 && f.ci95[1]], caption: "Likely range, on the final check", rule: true,
        acts: [hasExport("settings_json") ? { text: "Save settings file", act: "save-settings" } : null, { text: "Copy", kind: "secondary", act: "copy-summary", label: "Copy a summary" }].filter(Boolean) });
    } else if (d === "keep") {
      Object.assign(x, { mark: "keep", kickerTone: "", kicker: "Final check done · " + EK.plural(f.n || heldCount(), "held-back case"), head: keep,
        sub: f.mean != null ? EK.capital(EK.better(f.mean)) + ", but not sure enough." : "Not proven on new cases.", right: "range",
        range: f.ci95 ? [f.mean, f.ci95[0], f.ci95[1]] : null, caption: EK.rangeCaption({ decision: "keep", chance: f.chance, ci95: f.ci95 }), rule: true,
        acts: [f.recheck ? { text: "Check with more runs", kind: "secondary", act: "recheck" } : null].filter(Boolean) });
    } else if (d === "worse") {
      Object.assign(x, { mark: "worse", kickerTone: "", kicker: "Final check done · " + EK.plural(f.n || heldCount(), "case"), head: "Worse: " + EK.better(f.mean),
        sub: keep + ".", right: "range", range: f.ci95 ? [f.mean, f.ci95[0], f.ci95[1]] : null, caption: EK.rangeCaption({ decision: "worse", chance: f.chance, ci95: f.ci95 }), rule: true,
        acts: [{ text: "Start a new study", kind: "secondary", act: "next" }] });
    } else if (d === "single") {
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Final check done · 1 held-back case", head: EK.capital(EK.better(f.mean)) + " on one case",
        sub: "One case gives no range, so no answer.", right: "range", range: [f.mean, null, null], caption: "One case: a number, no range",
        acts: [{ text: "Hold back more cases", kind: "secondary", act: "next" }] });
    } else if (d === "skipped") {
      var nothing = /starting point stayed/.test(f.line || "") || /nothing better/.test(f.advice || "");
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Search done" + (s.time && s.time.used ? " · " + s.time.used : ""),
        head: nothing ? "Nothing beat " + keep.replace(/^Keep /, "") : "No final check", sub: nothing ? "No final check needed. " + keep + "." : "Nothing was held back to check it on.",
        caption: EK.capital(roundWord()) + " · no final check", stoppedAt: true,
        acts: [s.can_continue ? { text: "Search for longer", kind: "secondary", act: "continue" } : null].filter(Boolean) });
    } else if (d === "stopped") {
      Object.assign(x, { kickerTone: "", kicker: "Final check stopped" + (ch && ch.planned ? " · " + ch.pairs + " of " + ch.planned + " runs" : ""), head: "No answer yet",
        sub: "The runs so far are kept. Continue for an answer.", right: ch && ch.mean != null ? "both" : "timeline",
        range: ch && ch.mean != null ? [ch.mean, ch.ci95 && ch.ci95[0], ch.ci95 && ch.ci95[1]] : null, caption: "So far, on the final check",
        acts: [{ text: "Continue the check", act: "check" }] });
    } else {
      Object.assign(x, { mark: "none", kickerTone: "", kicker: "Finished", head: "No final check", sub: f.line || "There is no final check for this run.", acts: [] });
    }
    return x;
  }
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
    return html`<section class="status-strip${acc ? " is-accent" : ""}" aria-label="Status" aria-live="polite" aria-atomic="true">
      <div class="strip-main">${EK.verdictMark(x.mark, phone ? 36 : 44, acc)}<div class="strip-words">
        <span class="type-label${x.kickerTone === "accent" && !acc ? " accent-ink" : x.kickerTone === "critical" ? " tone-critical" : ""}">${x.kicker}</span>
        <p class="${acc ? "type-display" : "strip-head"}">${EK.figures(x.head)}${x.tag ? html`<span class="strip-tag">${x.tag}</span>` : ""}</p>
        <p class="type-body${acc ? "" : " ink-2"}">${x.sub || ""}</p></div></div>
      <div class="strip-side">${vis}${x.caption ? html`<span class="type-small${acc ? "" : " ink-2"}">${x.caption}</span>` : ""}
        ${acts.length ? html`<div class="actions">${acts}</div>` : ""}${rule}</div></section>`;
  }

  // ------------------------------------------------------------------ Pepper and the page title
  function companion() {
    var cs = cardState(), phase = EK.pepperPhase(cs), x = strip();
    var finished = cs.kind === "finished";
    var announce = null;
    var seenKey = "evolvekit.hatched." + st.slug + "/" + st.run, seen = true;
    try { seen = !!localStorage.getItem(seenKey); if (finished && !seen) localStorage.setItem(seenKey, "1"); } catch (e) { seen = true; }
    if (((st.phase && st.phase !== phase) || !seen) && finished) announce = phase === "butterfly" ? "I’m out! " + x.head + ", checked on " + EK.plural(((st.results || {}).final || {}).n || heldCount(), "case") + "." : "I’m out. " + x.head + ".";
    if (st.phase && st.phase !== phase && !finished && phase === "cocoon") announce = "Halfway. I’m in my cocoon now.";
    EK.companion.show({ phase: phase, slug: st.slug, say: x.head + ". " + (x.sub || ""), announce: announce, finished: finished });
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
    return [head, c.failed || c.pct == null ? "Did not finish" : EK.better(c.pct), lookWords(c)];
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
    var keepWord = whatOf() === "data" ? "today’s data" : "today’s settings";
    if (st.view === "tries") {
      var say = sayLine(t, family);
      var p = EK.triesPlot(t, { W: W, H: phone ? 250 : 380, phone: phone, chosen: st.chosen, family: family, keepWord: keepWord });
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
    var params = (t.parameters || []).slice().sort(function (a, b) { return EK.settingWeight(t, b) - EK.settingWeight(t, a); });
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
    if (chip) chip.innerHTML = String(EK.chosenChip(chosenCombo()));
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
          card.insertAdjacentHTML("beforeend", String(EK.button(chosen ? "Stop following" : "Follow it", { kind: "secondary", size: "s", act: chosen ? "unchoose" : "choose", arg: p.id })));
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
  EK.hoverEscape = function () {
    var card = document.getElementById("hover-card");
    if (!card) return false;
    card.remove();
    return true;
  };
  function choose(id) {
    st.chosen = id; st.chosenSet = true;
    var c = combo(id);
    st.lastPlot = "";
    drawView();
    EK.toast("done", "Following combination " + (c ? c.n : "") + " across the views");
  }
  function unchoose() { st.chosen = null; st.chosenSet = true; st.lastPlot = ""; drawView(); }

  // ------------------------------------------------------------------ below the views
  function changedTable() {
    var ch = ((st.results || {}).changed || []).filter(function (c) { return c.changed; });
    if (!ch.length) return html`<p class="type-body ink-2">${(st.results || {}).is_seed ? "Nothing: the starting point stayed the best." : "No changes."}</p>`;
    return html`<div class="cases-wrap"><table class="changed-table"><thead><tr><th>Setting</th><th>Today</th><th>New</th></tr></thead><tbody>
      ${ch.map(function (c) { return html`<tr><td>${c.label || c.name}${c.help ? html`<br><span class="ink-2">${c.help}</span>` : ""}</td><td>${c.old_text}</td><td class="new">${c.new_text}</td></tr>`; })}</tbody></table></div>`;
  }
  function handOver() {
    var r = st.results;
    if (!r || st.status.phase !== "done") return "";
    var d = decisionOf();
    var dl = (r.downloads || []).map(function (x) {
      return EK.link(x.label, { href: EK.withToken(EK.studyPath(st.slug, "/download/" + EK.q(x.name) + "?run=" + EK.q(st.run))), download: true, icon: EK.gl("file", 18) });
    });
    return html`<section class="section" aria-labelledby="hand-title"><h2 id="hand-title">${d === "use" ? "Hand it over" : "What the search found"}</h2>
      ${d !== "use" ? html`<p class="type-small ink-2">Not proven on held-back cases. Use it at your own risk.</p>` : ""}
      ${changedTable()}
      <div class="actions">${hasExport("settings_json") ? EK.button("Show the settings file", { kind: "secondary", act: "show-settings" }) : ""}
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
  function details() {
    var r = st.results, s = st.status;
    var parts = [];
    if (r) {
      var c = r.context || {};
      parts.push(html`<div class="facts-grid">
        <div class="fact"><span class="type-label">Time per case</span><span>${EK.duration(c.time_per_case_s)}</span></div>
        <div class="fact"><span class="type-label">Time in total</span><span>${EK.hours(c.hours)}${c.took ? " · took " + c.took : ""}</span></div>
        <div class="fact"><span class="type-label">Combinations</span><span>${c.tried != null ? EK.int(c.tried) + " tried" : ""}</span></div>
        <div class="fact"><span class="type-label">Starting point</span><span>${c.compared_with || ""}</span></div></div>`);
      if (r.final && r.final.line) parts.push(html`<p class="type-body">${r.final.line}</p>${r.final.next ? html`<p class="type-small ink-2">${r.final.next}</p>` : ""}`);
      if (r.final && r.final.recheck) parts.push(html`<div class="actions">${EK.button(r.final.recheck.label, { kind: r.final.recheck.over ? "quiet" : "secondary", act: "recheck" })}</div><p class="type-small ink-2">${r.final.recheck.why}</p>`);
      var table = function (title, rows) {
        if (!rows || !rows.length) return "";
        return html`<div class="sub-section"><h3>${title}</h3><div class="cases-wrap"><table class="changed-table"><thead><tr><th>Case</th><th>Runs</th><th>Start</th><th>Best</th><th>Change</th></tr></thead><tbody>
          ${rows.map(function (x) { return html`<tr><td class="mono">${x.case}</td><td>${x.runs}${x.failed ? " · " + x.failed + " failed" : ""}</td><td>${EK.num(x.start)}</td><td>${EK.num(x.best)}</td><td>${EK.better(x.change_pct)}</td></tr>`; })}</tbody></table></div></div>`;
      };
      parts.push(table("Held-back cases", (r.by_case || {}).held), table("Cases it learned from", (r.by_case || {}).learned));
      if ((r.kpis || []).length) {
        parts.push(html`<div class="sub-section"><h3>Every measure</h3><div class="cases-wrap"><table class="changed-table"><thead><tr><th>Measure</th><th>Study cases</th><th>Held-back cases</th></tr></thead><tbody>
          ${r.kpis.map(function (k) { return html`<tr><td>${k.says}${k.unit ? " (" + k.unit + ")" : ""}</td><td>${EK.num(k.training.start)} → ${EK.num(k.training.best)}</td><td>${k.test ? EK.num(k.test.start) + " → " + EK.num(k.test.best) : "–"}</td></tr>`; })}</tbody></table></div>
          ${(r.rules || []).length ? html`<p class="type-small ink-2">Rules: ${r.rules.join(" · ")}</p>` : ""}</div>`);
      }
    }
    (s.failures || []).forEach(function (fl) { parts.push(EK.notice("warning", fl.count + " × " + fl.message + (fl.todo ? " " + fl.todo : ""))); });
    if (s.gated_line) parts.push(html`<p class="type-small ink-2">${s.gated_line}</p>`);
    if (s.best && s.best.line) parts.push(html`<p class="type-small ink-2">${s.best.line}</p>`);
    return html`<details class="section-fold"><summary>Details</summary><div class="sub-section">${parts}</div></details>`;
  }

  // ------------------------------------------------------------------ the page
  function header() {
    var t = st.tries, tuned = ((t && t.parameters) || []).length, cases = ((st.doc.study.cases || {}).training || []).length;
    var runs = st.doc.runs || [];
    var sub = (tuned ? EK.plural(tuned, "setting") + " · " : "") + EK.plural(cases, "study case") + " · " + heldCount() + " held back";
    return html`${EK.titleBlock("Study · " + EK.when(runStartedAt()), st.doc.study.name, sub,
      html`<div class="actions">${EK.link("Past runs (" + runs.length + ")", { act: "runs" })}${EK.link("Set up", { href: "#/setup/" + EK.q(st.slug) })}</div>`)}`;
  }
  function runStartedAt() { var r = (st.doc.runs || []).filter(function (x) { return x.id === st.run; })[0]; return r ? r.started_at : ""; }
  function notices() {
    var s = st.status, out = [];
    if (s.phase === "search" && /again/.test(s.sentence || "")) out.push(EK.notice("info", "Continued where it stopped, after a restart."));
    if (s.busy_line && isLive()) out.push(EK.notice("warning", "Your computer is busy. Tries may run slower.", { text: "What is busy?", act: "busy" }));
    var latest = (st.doc.runs || []).slice(-1)[0];
    if (latest && latest.id !== st.run) out.push(EK.notice("info", "An older run of this study.", { text: "See the latest", act: "latest" }));
    return EK.join(out);
  }
  function draw(main) {
    var phone = EK.isPhone();
    var counts = st.tries && st.tries.available ? { tries: st.tries.combinations.filter(function (c) { return !c.base; }).length, cases: st.tries.cases.length, settings: (st.tries.parameters || []).length } : null;
    EK.mount(main, html`<div class="page"><div class="page-inner">${header()}<div id="study-notices">${notices()}</div>
      <div id="strip-slot">${statusStrip()}</div>
      <div class="sub-section"><div class="view-head">${EK.viewTabs(st.view, phone ? null : counts)}<div id="chip-slot" style="padding:6px 0">${EK.chosenChip(chosenCombo())}</div></div>
        <div class="view-panel" id="view-panel" role="tabpanel" aria-labelledby="tab-${st.view}"></div></div>
      ${handOver()}${nextSteps()}${details()}
      <p class="type-small ink-2">${EK.link("Detailed dashboard (for experts)", { href: EK.withToken(st.status.dashboard), target: "_blank" })}</p></div></div>`, acts(main));
    st.lastStrip = document.getElementById("strip-slot").innerHTML;
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
      st.lastStrip = slot.innerHTML;
      if (focusedAct) { var again = EK.$('[data-act="' + focusedAct + '"]', slot); if (again) again.focus(); }
    }
    var n = document.getElementById("study-notices");
    if (n) { var nh = String(notices()); if (n.innerHTML !== nh) n.innerHTML = nh; }
    if (!st.hover) drawView();
    companion();
    if (!isLive()) { EK._moveFocus = false; draw(main); }
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
        var order = ["tries", "cases", "settings"], i = order.indexOf(st.view);
        if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft") return;
        ev.preventDefault();
        var next = order[(i + (ev.key === "ArrowRight" ? 1 : 2)) % 3];
        var btn = EK.$('.tab[data-arg="' + next + '"]', main);
        btn.focus(); btn.click();
      },
      choose: function (el) { choose(el.getAttribute("data-arg")); },
      unchoose: function () { unchoose(); var t = EK.$(".tab.is-on", main); if (t) t.focus(); },
      "hover-close": function () { EK.hoverEscape(); },
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
      restart: function () { EK.post(EK.studyPath(st.slug, "/start")).then(function (res) { EK.go(EK.studyHref(st.slug, res.run)); }).catch(EK.fail); },
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
      "show-settings": function () {
        fetch(EK.studyPath(st.slug, "/download/settings_json?run=" + EK.q(st.run)), { headers: { "X-Evolvekit-Token": EK._token } }).then(function (r) { return r.text(); }).then(function (text) {
          EK.openDrawer({ title: "The settings file", sub: "What the application runs with", wide: true,
            body: html`<pre class="code">${text}</pre><div class="actions">${EK.button("Copy", { kind: "secondary", act: "copy" })}${EK.button("Save it", { act: "save" })}</div>`,
            acts: { copy: function () { EK.copy(text).then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Copied" : "Could not copy"); }); },
              save: function () { location.href = EK.withToken(EK.studyPath(st.slug, "/download/settings_json?run=" + EK.q(st.run))); } } });
        }).catch(EK.fail);
      },
      why: function () {
        EK.openDrawer({ title: "The rule", body: html`<p class="type-body">${(st.results && st.results.final && st.results.final.rule) || "The app says use it when the chance of a gain is at least 9 in 10 and the worst plausible case is within 1 % of today."}</p>
          <p class="type-small ink-2">The final check runs the best combination and today’s ${whatOf()} on the held-back cases, which the search never saw.</p>` });
      },
      busy: function () { EK.openDrawer({ title: "Your computer is busy", body: html`<p class="type-body">${st.status.busy_line}</p>` }); },
      latest: function () { EK.go(EK.studyHref(st.slug)); },
      runs: runsDrawer
    };
  }
  function runsDrawer() {
    var runs = (st.doc.runs || []).slice().reverse().map(function (r) {
      var mark = r.kind === "finished" ? EK.markOf({ kind: "finished", decision: r.decision, tone: r.tone }) : r.kind === "failed" ? "error" : "open";
      return Object.assign({}, r, { mark: mark });
    });
    EK.openDrawer({ title: "This study’s runs", sub: "Newest first", body: html`${EK.runList(runs, st.run, function (r) { return EK.studyHref(st.slug, r.id); })}
      ${runs.length > 1 ? html`<p>${EK.link("Compare two runs", { href: "#/compare?a=" + EK.q(st.slug + "/" + runs[0].id) + "&b=" + EK.q(st.slug + "/" + runs[1].id) })}</p>` : ""}` });
  }

  EK.pages.study = { render: render };
})();
