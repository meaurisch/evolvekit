/* evolvekit app: Set up (option A) — ready to start?
   One screen: three summaries (What to tune, Goal, Time), each with Change
   opening a drawer; the plan bar with its fixes; a test run; Start. One
   click away in the "more" row: held-back cases, runs per case, rules,
   measures, data changes, AI help. What to tune and the goal come from the
   application's harness only: its labels, groups, help, recommended ranks,
   measures and fill-in-the-blank measures. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  var st = null;

  function render(main, slug, query, gen) {
    if (!slug) { location.replace("#/start"); return; }
    st = { slug: slug, gen: gen, doc: null, h: null, plan: null, planError: "", settings: null, choices: null, values: {}, show: "", group: "All", all: false };
    return Promise.all([EK.get(EK.studyPath(slug)), EK.get(EK.studyPath(slug, "/harness"))]).then(function (r) {
      if (!EK.isCurrent(gen)) return;
      st.doc = r[0]; st.h = r[1];
      if (r[0].state && (r[0].state.kind === "running" || r[0].state.kind === "queued")) { location.replace(EK.studyHref(slug)); return; }
      EK.companion.show({ phase: "egg", slug: slug, say: "Check the three summaries, then start." });
      EK.onStudyChanged = function () { refresh(main); };
      return loadPlan().then(function () { draw(main); watch(main); });
    });
  }

  // ------------------------------------------------------------------ data
  function loadPlan() {
    return EK.get(EK.studyPath(st.slug, "/plan")).then(function (p) { st.plan = p; st.planError = ""; }, function (err) { st.plan = null; st.planError = err.message; });
  }
  function refresh(main) {
    return EK.get(EK.studyPath(st.slug)).then(function (doc) { st.doc = doc; return loadPlan(); }).then(function () { draw(main); });
  }
  function save(changes, main) {
    return EK.patch(EK.studyPath(st.slug), changes).then(function (doc) { st.doc = doc; return loadPlan(); }).then(function () {
      draw(main);
      if (EK.drawerKind() && redrawDrawer) redrawDrawer();
    }).catch(EK.fail);
  }
  /** Poll the preview (a run on one case, about 10 s) and the test run while they run. */
  function watch(main) {
    EK.every(1500, function () {
      var d = st.doc, busy = (d.preview && d.preview.state === "running") || (d.test_run && d.test_run.state === "running");
      if (!busy) return;
      EK.get(EK.studyPath(st.slug)).then(function (doc) {
        if (!EK.isCurrent(st.gen)) return;
        var was = st.doc;
        st.doc = doc;
        var changed = (was.preview || {}).state !== (doc.preview || {}).state || (was.test_run || {}).state !== (doc.test_run || {}).state;
        if (changed) loadPlan().then(function () { draw(main); if (redrawDrawer) redrawDrawer(); });
        else { var t = document.getElementById("test-run-box"); if (t) t.outerHTML = String(testRun()); }
      }).catch(function () { /* next poll */ });
    });
  }
  var redrawDrawer = null;

  function study() { return st.doc.study; }
  function settingsList() { return st.h.settings || []; }
  function vary() { return study().vary || { settings: {}, data: {} }; }
  function modeOf(name) {
    var v = (vary().settings || {})[name];
    if (v == null || v === "default") return "default";
    if (v === "tune" || (typeof v === "object" && v.tune)) return "tune";
    if (typeof v === "object" && "fixed" in v) return "fixed";
    return "default";
  }
  function recRank(s) {
    var r = s.recommended;
    if (r === true) return 0;
    if (typeof r === "number") return r;
    return null;
  }
  function valueWords(s, v) {
    if (v == null) return "";
    if (s.type === "bool") return v ? "on" : "off";
    if (s.type === "float") return Math.abs(v) >= 1000 ? EK.int(v) : EK.num(v, 2);
    if (s.type === "int") return EK.int(v);
    return String(v);
  }
  function triesWords(s) {
    if (s.type === "bool") return "on or off";
    if (s.type === "choice") return (s.choices || []).join(" or ");
    var v = (vary().settings || {})[s.name], lo = s.low, hi = s.high;
    if (v && typeof v === "object" && v.low != null) { lo = v.low; hi = v.high; }
    return valueWords(s, lo) + " to " + valueWords(s, hi);
  }
  function tuned() { return settingsList().filter(function (s) { return modeOf(s.name) === "tune"; }); }
  function kpiLabel(name) {
    var k = (study().kpis || {})[name] || {}, h = (st.h.kpis || {})[name];
    if (h) return h.label;
    if (k.template) {
      var t = (st.h.kpi_templates || {})[k.template];
      if (t) return t.label.replace(/\{(\w+)\}/g, function (_, p) { return (k.params || {})[p] != null ? k.params[p] : "…"; });
    }
    return k.says || String(name).replace(/_/g, " ").replace(/^./, function (c) { return c.toUpperCase(); });
  }
  function kpiDirection(name) {
    var k = (study().kpis || {})[name] || {}, h = (st.h.kpis || {})[name];
    if (k.direction) return k.direction;
    if (h) return h.direction;
    if (k.template && (st.h.kpi_templates || {})[k.template]) return st.h.kpi_templates[k.template].direction;
    return "lower";
  }
  function dirWords(d) { return d === "higher" ? "higher is better" : "lower is better"; }
  function levels() { return ((study().goal || {}).levels || []); }
  function isData() { return Object.keys(vary().data || {}).length > 0 || ((st.h.templates || {})[study().template] || {}).kind === "data"; }

  // ------------------------------------------------------------------ the summaries
  function tuneSummary() {
    var all = settingsList().length, n = tuned().length;
    if (!all) return null;
    var per = st.plan && n ? Math.floor(st.plan.combinations / n) : null;
    return { kicker: "What to tune", value: n ? n + " of " + all + " settings" : "No settings tuned", state: n || isData() ? "complete" : "missing",
      sub: n ? (per != null ? "About " + per + " tries each. Others keep today’s values." : "Recommended first.") : "Pick what may change.", action: n ? "Change" : "Pick settings", act: "tune" };
  }
  function goalSummary() {
    var lv = levels();
    if (!lv.length) return { kicker: "Goal", value: "No goal yet", sub: "Pick the measure to improve.", state: "missing", action: "Pick a goal", act: "goal" };
    var head = kpiLabel(lv[0].kpi) + ", " + (kpiDirection(lv[0].kpi) === "higher" ? "higher" : "lower");
    if (lv.length > 1) head += "; then " + kpiLabel(lv[1].kpi).toLowerCase();
    var train = (study().cases || {}).training || [];
    return { kicker: "Goal", value: head, sub: lv.length > 1 ? "The second breaks ties within " + (lv[0].equal_within || "1 %") + "." : "On the " + EK.plural(train.length, "study case") + ".", act: "goal" };
  }
  function timeSummary() {
    var lim = study().limits || {}, hours = (study().budget || {}).hours;
    var value = EK.duration(lim.time_per_case_s) + " a case · " + EK.hours(hours) + " in total";
    if (st.plan && st.plan.blocked) return { kicker: "Time", value: value, sub: "Too short for one full look.", state: "open", action: "Change", act: "time" };
    var done = hours ? new Date(Date.now() + hours * 3600 * 1000) : null;
    var tomorrow = done && done.toDateString() !== new Date().toDateString();
    return { kicker: "Time", value: value, sub: done ? "Done around " + done.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) + (tomorrow ? " tomorrow." : ".") : "", act: "time" };
  }
  function dataSummary() {
    var data = vary().data || {}, n = Object.keys(data).length;
    if (!n && !isData()) return null;
    return { kicker: "Data changes", value: n ? EK.plural(n, "change") : "No changes yet", sub: n ? Object.keys(data).join(", ") : "Pick what may change in each case.",
      state: n ? "complete" : "missing", action: n ? "Change" : "Add one", act: "data" };
  }

  function planBlock() {
    if (!st.plan) return html`${EK.notice("warning", st.planError || "No plan yet.")}`;
    var p = st.plan, tunables = p.tunables || 0;
    if (p.blocked) {
      return html`${EK.planBar({ state: "impossible", head: "This plan cannot work", barText: p.blocked, left: p.blocked, aria: "The plan does not fit: " + p.blocked })}
        ${(p.ways_out || []).length ? html`<ul class="type-small">${p.ways_out.map(function (w) { return html`<li>${w}</li>`; })}</ul>` : ""}
        <div class="actions">${EK.button("Change the time", { kind: "secondary", act: "time" })}</div>`;
    }
    var thin = !!p.warning;
    var searchH = (p.search_s || 0) / 3600, checkH = (p.final_check_s || 0) / 3600;
    var done = new Date(Date.now() + (p.hours || 0) * 3600 * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    var phone = EK.isPhone();
    var bar = EK.planBar({ state: thin ? "thin" : "fits", rounds: p.rounds || 1, searchH: searchH || 1, checkH: checkH,
      head: thin ? "Thin plan: " + p.combinations + " combinations for " + EK.plural(tunables, "setting") : EK.hours(p.hours) + " in total · done around " + done,
      barText: thin && tunables ? "about " + Math.max(1, Math.floor(p.combinations / tunables)) + " tries per setting" : "",
      left: phone ? "Search · " + p.rounds + " rounds" : "Search · " + EK.plural(p.rounds || 0, "round") + " · " + p.combinations + " combinations · " + EK.hours(searchH),
      right: checkH ? (phone ? "Final check" : "Final check · " + (p.test_cases || 0) + " held back · " + EK.hours(checkH)) : "No final check",
      aria: "Plan: " + p.rounds + " rounds of search, " + EK.hours(searchH) + ", then the final check, " + EK.hours(checkH) + "; done around " + done });
    var fixes = (p.fixes || []).map(function (f, i) {
      var text = f.kind === "fewer" ? "Tune the top " + f.keep.length : "Allow " + EK.hours(f.hours);
      return EK.button(text, { kind: i === 0 ? "secondary" : "quiet", act: "fix", arg: i, describedby: "fix-" + i });
    });
    var details = (p.fixes || []).map(function (f, i) { return html`<p class="type-small ink-2" id="fix-${i}">${f.kind === "fewer" ? "Top " + f.keep.length : "More time"}: ${f.detail}</p>`; });
    return html`${bar}${fixes.length ? html`<div class="actions">${fixes}</div>${details}` : ""}
      ${p.estimated ? html`<p class="type-small ink-2">An estimate until the test run has timed a case.</p>` : ""}`;
  }

  function testRun() {
    var t = st.doc.test_run || { state: "none" }, r = t.result || {};
    var kpis = r.kpis || {};
    var values = html`<div class="kpi-values">${Object.keys(kpis).slice(0, 4).map(function (k) {
      return html`<span class="type-small">${kpiLabel(k)} <strong class="type-data" style="font-size:14px">${EK.num(kpis[k])}</strong></span>`;
    })}</div>`;
    if (t.state === "running") {
      var began = typeof t.started_at === "number" ? t.started_at * 1000 : t.started_at ? new Date(t.started_at).getTime() : Date.now();
      var secs = Math.max(0, Math.round((Date.now() - began) / 1000));
      var frac = t.expected_s ? Math.min(0.95, secs / t.expected_s) : 0.4;
      return html`<div class="test-run" id="test-run-box" role="status"><div class="test-run-line"><span class="type-label accent-ink">Test run</span>
        <span class="type-small ink-2">${secs} s${t.expected_s ? " of about " + Math.round(t.expected_s) + " s" : ""}</span></div>
        <div class="progress" style="width:100%" aria-hidden="true"><div class="progress-fill breathe" style="width:${Math.round(frac * 100)}%"></div></div></div>`;
    }
    if (t.state === "failed" || (t.state === "done" && r.ok === false)) {
      var why = (r.failures || []).concat(t.error ? [t.error] : []);
      return html`<div class="test-run" id="test-run-box" role="alert"><div class="test-run-line">${EK.icon("critical", 20)}<span class="test-run-title">The test run failed${r.case ? " on " + r.case : ""}</span></div>
        <p class="type-small">${why[0] || "It stopped with an error. Nothing was started."}</p>
        <div class="actions">${why.length > 1 ? EK.button("Show the error", { kind: "secondary", size: "s", act: "test-error" }) : ""}${EK.button("Run it again", { kind: "quiet", size: "s", act: "test" })}</div></div>`;
    }
    if (t.state === "done") {
      return html`<div class="test-run" id="test-run-box"><div class="test-run-line">${EK.icon("good", 20)}<span class="test-run-title">Test run passed${r.case ? " on " + r.case : ""}</span>
        ${EK.button("Again", { kind: "quiet", size: "s", act: "test" })}</div>${values}
        ${(r.warnings || []).map(function (w) { return html`<p class="type-small tone-warning">${w}</p>`; })}</div>`;
    }
    return html`<div class="test-run" id="test-run-box"><div class="test-run-line">${EK.icon("info", 20)}<span class="test-run-title">Try it once on one case</span>
      ${EK.button("Test run", { kind: "secondary", size: "s", act: "test" })}</div><p class="type-small ink-2">About a minute. Start works without it.</p></div>`;
  }

  function draw(main) {
    var d = st.doc, problems = (d.problems || []).filter(function (x) { return x.step >= 4; });
    var t = d.test_run || {};
    var failed = t.state === "failed" || (t.state === "done" && t.result && t.result.ok === false);
    var blocked = st.plan && st.plan.blocked;
    var missing = problems.length;
    var head = missing ? EK.plural(missing, "thing") + " missing" : blocked ? "Not ready: the plan cannot work" : failed ? "Not ready: the test run failed"
      : st.plan && st.plan.warning ? "Ready, but the plan is thin" : "Ready to start?";
    var why = missing ? problems[0].text : blocked ? "Fix the plan first." : failed ? "Fix the test run first." : t.state === "running" ? "Start works while the test runs." : "";
    var can = !missing && !blocked && !failed;
    var rows = [tuneSummary(), dataSummary(), goalSummary(), timeSummary()].filter(Boolean);
    var c = study().cases || {}, rules = (study().constraints || []).length, measures = Object.keys(study().kpis || {}).length;
    var lim = study().limits || {}, ai = ((study().budget || {}).ai || {}).enabled;
    var more = html`<div class="more-row">
      ${EK.link("Held-back cases (" + (c.test || []).length + ")", { href: "#/start/" + EK.q(st.slug) })}
      ${EK.link("Runs per case (" + (lim.runs_per_case || 1) + ")", { act: "time" })}
      ${EK.link("Rules (" + rules + ")", { act: "rules" })}
      ${EK.link("Measures (" + measures + ")", { act: "measures" })}
      ${(Object.keys(st.h.levers || {}).length && !dataSummary()) ? EK.link("Data changes (" + Object.keys(vary().data || {}).length + ")", { act: "data" }) : ""}
      ${EK.link("AI help: " + (ai ? "on" : "off"), { act: "time", arg: "ai" })}
      ${EK.link("Save as zip", { href: EK.withToken(EK.studyPath(st.slug, "/download/study")), download: true })}</div>`;
    var notice = missing ? EK.notice("error", problems[0].text, problems[0].step === 5 ? { text: "Pick a goal", act: "goal" } : problems[0].step === 4 ? { text: "Fix it", act: "tune" } : { text: "Fix it", act: "time" }) : "";
    var preview = d.preview || {};
    var previewLine = preview.state === "running" ? EK.notice("info", "Timing one case to plan the study…")
      : preview.state === "failed" || preview.state === "interrupted" ? EK.notice("warning", "The timing run did not finish: " + (preview.error || "the plan is an estimate."), { text: "Try again", act: "preview" }) : "";
    EK.mount(main, html`<div class="page"><div class="page-inner narrow" style="max-width:1000px">
      ${EK.titleBlock("Set up · " + study().name, head)}${notice}${previewLine}
      <div class="summary-rows">${rows.map(EK.summaryRow)}</div>
      <section aria-label="Plan" class="sub-section">${planBlock()}</section>
      ${testRun()}
      <div class="actions">${EK.button("Start", { act: "start", disabled: !can })}${EK.button("Save for later", { kind: "secondary", act: "later" })}<span class="type-small ink-2">${why}</span></div>
      ${more}</div></div>`, acts(main));
  }

  // ------------------------------------------------------------------ actions
  function acts(main) {
    return {
      tune: function () { tuneDrawer(main); },
      goal: function () { goalPicker(main); },
      time: function (el) { timeDrawer(main, el && el.getAttribute("data-arg")); },
      rules: function () { rulesDrawer(main); },
      measures: function () { measuresDrawer(main); },
      data: function () { dataDrawer(main); },
      preview: function () { EK.post(EK.studyPath(st.slug, "/preview")).then(function () { return refresh(main); }).catch(EK.fail); },
      fix: function (el) {
        var f = st.plan.fixes[Number(el.getAttribute("data-arg"))];
        if (f.kind === "fewer") {
          EK.post(EK.studyPath(st.slug, "/tune-only"), { keep: f.keep }).then(function (doc) {
            st.doc = doc; EK.toast("done", "Tuning the top " + f.keep.length); return loadPlan();
          }).then(function () { draw(main); }).catch(EK.fail);
        } else {
          save({ budget: Object.assign({}, study().budget, { hours: f.hours }) }, main).then(function () { EK.toast("done", "Allowed " + EK.hours(f.hours)); });
        }
      },
      test: function () {
        EK.post(EK.studyPath(st.slug, "/test-run")).then(function () { return refresh(main); }).catch(EK.fail);
      },
      "test-error": function () {
        var r = (st.doc.test_run || {}).result || {};
        EK.openDrawer({ title: "Why the test run failed", wide: true,
          body: html`${(r.failures || []).map(function (f) { return html`<pre class="code">${f}</pre>`; })}${st.doc.test_run.error ? html`<pre class="code">${st.doc.test_run.error}</pre>` : ""}` });
      },
      start: function () {
        EK.post(EK.studyPath(st.slug, "/start")).then(function (res) {
          EK.toast("done", res.queued ? "Queued: it starts by itself" : "Started");
          EK.go(EK.studyHref(st.slug));
        }).catch(EK.fail);
      },
      later: function () { EK.toast("done", "Saved. It waits on Home."); EK.go("#/"); }
    };
  }

  // ------------------------------------------------------------------ What to tune
  function tuneDrawer(main) {
    var groups = [];
    settingsList().forEach(function (s) { if (s.group && groups.indexOf(s.group) < 0) groups.push(s.group); });
    function rows() {
      var list = settingsList().slice();
      list.sort(function (a, b) {
        var ma = modeOf(a.name) === "tune" ? 0 : 1, mb = modeOf(b.name) === "tune" ? 0 : 1;
        if (ma !== mb) return ma - mb;
        var ra = recRank(a), rb = recRank(b);
        if ((ra != null) !== (rb != null)) return ra != null ? -1 : 1;
        if (ra != null && rb != null && ra !== rb) return ra - rb;
        return settingsList().indexOf(a) - settingsList().indexOf(b);
      });
      if (st.group !== "All") list = list.filter(function (s) { return s.group === st.group; });
      var shown = st.all || st.group !== "All" ? list : list.slice(0, 9);
      var out = shown.map(function (s) {
        var mode = modeOf(s.name), v = (vary().settings || {})[s.name];
        var detail = mode === "tune" ? "Tries " + triesWords(s) : mode === "fixed" ? "Always " + valueWords(s, v.fixed) : "Today’s value: " + valueWords(s, s.default);
        var extra = "";
        if (mode === "tune" && (s.type === "int" || s.type === "float")) {
          var lo = v && typeof v === "object" && v.low != null ? v.low : s.low, hi = v && typeof v === "object" && v.high != null ? v.high : s.high;
          extra = html`<details class="more-detail"><summary>Change the range</summary><form class="range-edit" data-submit="range" data-arg="${s.name}">
            ${EK.numberUnit({ id: "lo-" + s.name, label: "From", value: lo, unit: "", width: 140 })}${EK.numberUnit({ id: "hi-" + s.name, label: "To", value: hi, unit: "", width: 140 })}
            ${EK.button("Use this range", { kind: "secondary", size: "s", type: "submit" })}</form></details>`;
        } else if (mode === "fixed") {
          var control;
          if (s.type === "bool") control = html`<select class="input" id="fx-${s.name}" data-change="fixed" data-arg="${s.name}" style="width:auto"><option value="true"${EK.attrs({ selected: !!v.fixed })}>on</option><option value="false"${EK.attrs({ selected: !v.fixed })}>off</option></select>`;
          else if (s.type === "choice") control = html`<select class="input" id="fx-${s.name}" data-change="fixed" data-arg="${s.name}" style="width:auto">${(s.choices || []).map(function (c) { return html`<option${EK.attrs({ selected: c === v.fixed })}>${c}</option>`; })}</select>`;
          else control = html`<input class="input" id="fx-${s.name}" inputmode="decimal" value="${v.fixed}" data-change="fixed" data-arg="${s.name}" style="width:140px">`;
          extra = html`<div class="range-edit"><label class="sr-only" for="fx-${s.name}">Value of ${s.label}</label>${control}</div>`;
        }
        return EK.settingRow(s, mode, { recommended: recRank(s) != null, detail: detail, today: valueWords(s, s.default), tries: triesWords(s), extra: extra });
      });
      var rest = list.length - shown.length;
      return html`${out}${rest > 0 ? html`<p class="type-small ink-2" style="margin-top:6px">${rest} more · ${EK.link("Show all", { act: "show-all" })}</p>` : ""}`;
    }
    function body() {
      var n = tuned().length, all = settingsList().length;
      var inputs = Object.keys(st.h.inputs || {}).filter(function (k) { return st.h.inputs[k].provides === "settings"; });
      return html`${groups.length > 1 ? html`<div class="filter-chips" role="group" aria-label="Groups">${["All"].concat(groups).map(function (g) {
          return html`<button type="button" class="filter-chip" aria-pressed="${g === st.group ? "true" : "false"}" data-act="group" data-arg="${g}">${g}</button>`;
        })}</div>` : ""}
        <p class="type-small ink-2">${n} tuned · ${all - n} keep today’s values · recommended first</p>
        <div>${rows()}</div>${inputs.map(settingsInput)}`;
    }
    function settingsInput(name) {
      var spec = st.h.inputs[name], have = (study().inputs || {})[name];
      return html`<section class="sub-section expert"><h3 class="type-heading">${spec.label || "Today’s settings file"}</h3><p class="type-small ink-2">${spec.describe || ""}</p>
        ${have ? html`<p class="type-small">In use: <span class="mono">${have}</span></p><div>${EK.button("Remove it", { kind: "quiet", size: "s", act: "input-remove", arg: name })}</div>`
          : html`<div><label class="btn btn-secondary btn-s" for="input-${name}"><span>Import a file</span></label>
            <input type="file" class="sr-only" id="input-${name}" accept="${(spec.formats || []).join(",")}" data-change="input-file" data-arg="${name}"></div>`}</section>`;
    }
    var dacts = {
      mode: function (el) {
        var name = el.getAttribute("data-arg"), mode = el.getAttribute("data-arg2"), s = settingsList().filter(function (x) { return x.name === name; })[0];
        var settings = Object.assign({}, vary().settings || {});
        if (mode === "default") delete settings[name];
        else if (mode === "tune") settings[name] = "tune";
        else settings[name] = { fixed: s.default };
        save({ vary: { settings: settings, data: vary().data || {} } }, main);
      },
      segkey: segKey,
      info: function (el) {
        var tip = document.getElementById(el.getAttribute("data-arg")), open = tip.hidden;
        EK.$$(".info-tip:not([hidden])").forEach(function (t) { t.hidden = true; var b = EK.$('[aria-controls="' + t.id + '"]'); if (b) b.setAttribute("aria-expanded", "false"); });
        tip.hidden = !open;
        el.setAttribute("aria-expanded", open ? "true" : "false");
      },
      range: function (el) {
        var name = el.getAttribute("data-arg"), lo = EK.readNumber(document.getElementById("lo-" + name).value), hi = EK.readNumber(document.getElementById("hi-" + name).value);
        if (lo == null || hi == null || lo >= hi) { EK.toast("error", "From must be below To"); return; }
        var settings = Object.assign({}, vary().settings || {});
        settings[name] = { tune: true, low: lo, high: hi };
        save({ vary: { settings: settings, data: vary().data || {} } }, main);
      },
      fixed: function (el) {
        var name = el.getAttribute("data-arg"), s = settingsList().filter(function (x) { return x.name === name; })[0], v = el.value;
        var value = s.type === "bool" ? v === "true" : s.type === "choice" ? v : EK.readNumber(v);
        if (value == null) { EK.toast("error", "A number, please"); return; }
        var settings = Object.assign({}, vary().settings || {});
        settings[name] = { fixed: s.type === "int" ? Math.round(value) : value };
        save({ vary: { settings: settings, data: vary().data || {} } }, main);
      },
      group: function (el) { st.group = el.getAttribute("data-arg"); EK.refreshDrawer({ body: body() }); },
      "show-all": function () { st.all = true; EK.refreshDrawer({ body: body() }); },
      recommended: function () {
        var settings = {};
        settingsList().forEach(function (s) { if (recRank(s) != null) settings[s.name] = "tune"; });
        save({ vary: { settings: settings, data: vary().data || {} } }, main);
      },
      none: function () { save({ vary: { settings: {}, data: vary().data || {} } }, main); },
      "input-file": function (el) {
        var f = el.files[0], name = el.getAttribute("data-arg");
        if (!f) return;
        f.arrayBuffer().then(function (buf) {
          return EK.api("PUT", EK.studyPath(st.slug, "/inputs/" + EK.q(name) + "/" + EK.q(f.name)), new Blob([buf]));
        }).then(function (doc) { st.doc = doc; EK.toast("done", f.name + " imported"); return loadPlan(); }).then(function () { draw(main); redrawDrawer(); }).catch(EK.fail);
      },
      "input-remove": function (el) {
        EK.del(EK.studyPath(st.slug, "/inputs/" + EK.q(el.getAttribute("data-arg")))).then(function (doc) { st.doc = doc; draw(main); redrawDrawer(); }).catch(EK.fail);
      },
      done: function () { EK.closeDrawer(); }
    };
    var name = st.h.title || st.h.application.label;
    redrawDrawer = function () { if (EK.drawerKind() === "tune") EK.refreshDrawer({ body: body(), acts: dacts }); };
    EK.openDrawer({ kind: "tune", title: "What to tune", sub: name + "’s settings, named by " + name, wide: true, dims: true, body: body(),
      foot: html`<div class="actions">${EK.button("Done", { act: "done" })}${EK.button("Tune the recommended", { kind: "quiet", act: "recommended" })}${EK.button("Tune none", { kind: "quiet", act: "none" })}</div>`,
      acts: dacts, backLabel: "Back to Set up", onClose: function () { redrawDrawer = null; } });
  }
  /** Arrow keys move between the Tune · Fixed · Default buttons (a radio group). */
  function segKey(el, ev) {
    if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].indexOf(ev.key) < 0) return;
    var btns = EK.$$(".seg-btn", el), i = btns.indexOf(document.activeElement);
    if (i < 0) return;
    ev.preventDefault();
    var next = btns[(i + (ev.key === "ArrowRight" || ev.key === "ArrowDown" ? 1 : btns.length - 1)) % btns.length];
    next.focus();
    next.click();
  }

  // ------------------------------------------------------------------ Goal
  function todayValue(name) {
    var pv = ((st.doc.preview || {}).result || {}).kpis || {};
    return pv[name] != null ? EK.num(pv[name]) : null;
  }
  function goalPicker(main) {
    var draft = { levels: JSON.parse(JSON.stringify(levels())), kpis: JSON.parse(JSON.stringify(study().kpis || {})), params: {} };
    function current() { return draft.levels[0] ? draft.levels[0].kpi : ""; }
    function templateKey(t, params) {
      return t + "_" + Object.keys(params).sort().map(function (k) { return String(params[k]).replace(/[^A-Za-z0-9]+/g, "-"); }).join("_");
    }
    function body() {
      var harnessKpis = st.h.kpis || {}, names = Object.keys(harnessKpis);
      var showAll = draft.showAll || names.length <= 6;
      var shown = showAll ? names : names.slice(0, 6);
      var chosen = current();
      var ready = shown.map(function (k) {
        var x = harnessKpis[k], on = chosen === k, today = todayValue(k);
        return html`<label class="goal-option${on ? " is-on" : ""}"><input type="radio" name="goal1" value="${k}"${EK.attrs({ checked: on })} data-change="pick">
          <span class="goal-words"><span class="goal-title">${x.label}</span><span class="type-small ink-2">${dirWords(x.direction)}${x.unit ? " · " + x.unit : ""}${today != null ? " · today " + today : ""}</span>
</span></label>`;
      });
      var templates = st.h.kpi_templates || {};
      var blanks = Object.keys(templates).map(function (t) {
        var x = templates[t], key = Object.keys(draft.kpis).filter(function (k) { return draft.kpis[k].template === t; })[0];
        var on = key && chosen === key;
        var params = draft.params[t] || (key ? draft.kpis[key].params : {}) || {};
        var fill = "";
        if (on || draft.open === t) {
          fill = html`<span class="blank-line">${Object.keys(x.params || {}).map(function (p) {
            var spec = x.params[p], options = (st.choices && st.choices[t] && st.choices[t][p]) || spec.choices || [];
            var id = "blank-" + t + "-" + p;
            if (spec.from && st.choices && !options.length) {
              return html`<span class="type-small ink-2 blank-line">${EK.icon("info", 16)}These cases carry no ${spec.label || p}.</span>`;
            }
            if (options.length) return html`<label class="sr-only" for="${id}">${spec.label || p}</label><select id="${id}" data-change="blank" data-arg="${t}" data-arg2="${p}">
              <option value="">${spec.label || p}…</option>${options.map(function (o) { return html`<option${EK.attrs({ selected: String(params[p]) === String(o) })}>${o}</option>`; })}</select>`;
            return html`<label class="sr-only" for="${id}">${spec.label || p}</label><input id="${id}" inputmode="decimal" placeholder="${spec.label || p}" value="${params[p] == null ? "" : params[p]}" data-change="blank" data-arg="${t}" data-arg2="${p}">`;
          })}</span>`;
        }
        var label = x.label.replace(/\{(\w+)\}/g, function (_, p) { return params[p] != null && params[p] !== "" ? params[p] : "…"; });
        return html`<label class="goal-option${on ? " is-on" : ""}"><input type="radio" name="goal1" value="tpl:${t}"${EK.attrs({ checked: on })} data-change="pick-blank" data-arg="${t}">
          <span class="goal-words"><span class="goal-title">${label}</span><span class="type-small ink-2">${dirWords(x.direction)}</span>${fill}</span></label>`;
      });
      var own = Object.keys(draft.kpis).filter(function (k) { var x = draft.kpis[k]; return !x.template && x.from !== "harness" && !harnessKpis[k]; }).map(function (k) {
        var on = chosen === k;
        return html`<label class="goal-option${on ? " is-on" : ""}"><input type="radio" name="goal1" value="${k}"${EK.attrs({ checked: on })} data-change="pick">
          <span class="goal-words"><span class="goal-title">${draft.kpis[k].says || k}</span><span class="type-small ink-2">${dirWords(draft.kpis[k].direction)} · your own</span></span></label>`;
      });
      var second = "";
      if (draft.levels.length > 1) {
        var l2 = draft.levels[1];
        second = html`<section class="second-goal"><span class="type-label ink-2">Then, when within ${draft.levels[0].equal_within || "1 %"}</span>
          <div class="actions"><label class="sr-only" for="second-kpi">Second goal</label><select class="input" id="second-kpi" data-change="second" style="flex:1;width:auto">
            ${Object.keys(draft.kpis).filter(function (k) { return k !== chosen; }).map(function (k) { return html`<option value="${k}"${EK.attrs({ selected: k === l2.kpi })}>${kpiLabelOf(k)}</option>`; })}</select>
            ${EK.button("Remove", { kind: "quiet", size: "s", act: "second-remove" })}</div>
          <span class="type-small ink-2">Breaks ties between combinations that are nearly as good.</span></section>`;
      } else if (chosen && Object.keys(draft.kpis).length > 1) {
        second = html`<div>${EK.button("Add a second goal", { kind: "quiet", act: "second-add", icon: EK.gl("plus", 16) })}</div>`;
      } else if (chosen && names.length > 1) {
        second = html`<div>${EK.button("Add a second goal", { kind: "quiet", act: "second-add", icon: EK.gl("plus", 16) })}</div>`;
      }
      return html`<section class="drawer-section"><h3 class="type-label ink-2">Ready-made</h3>${ready}
          ${!showAll ? html`<p class="type-small ink-2" style="margin:2px 0 0 12px">${names.length - 6} more · ${EK.link("Show all", { act: "goal-all" })}</p>` : ""}</section>
        ${blanks.length ? html`<section class="drawer-section"><h3 class="type-label ink-2">Fill in the blank</h3>${blanks}</section>` : ""}
        ${own.length ? html`<section class="drawer-section"><h3 class="type-label ink-2">Your own</h3>${own}</section>` : ""}${second}`;
    }
    function kpiLabelOf(k) {
      if ((st.h.kpis || {})[k]) return st.h.kpis[k].label;
      var x = draft.kpis[k] || {};
      if (x.template && (st.h.kpi_templates || {})[x.template]) return st.h.kpi_templates[x.template].label.replace(/\{(\w+)\}/g, function (_, p) { return (x.params || {})[p]; });
      return x.says || k;
    }
    function dirOf(k) {
      if ((st.h.kpis || {})[k]) return st.h.kpis[k].direction;
      var x = draft.kpis[k] || {};
      if (x.template && (st.h.kpi_templates || {})[x.template]) return st.h.kpi_templates[x.template].direction;
      return x.direction || "lower";
    }
    function setFirst(k) {
      var rest = draft.levels.slice(1).filter(function (l) { return l.kpi !== k; });
      draft.levels = [{ kpi: k, direction: dirOf(k) }].concat(rest);
      if (draft.levels.length > 1) draft.levels[0].equal_within = "1 %";
    }
    function redraw() { EK.refreshDrawer({ body: body() }); }
    var dacts = {
      pick: function (el) {
        var k = el.value;
        if ((st.h.kpis || {})[k] && !draft.kpis[k]) draft.kpis[k] = { from: "harness" };
        setFirst(k); draft.open = null; redraw();
      },
      "pick-blank": function (el) { draft.open = el.getAttribute("data-arg"); redraw(); },
      blank: function (el) {
        var t = el.getAttribute("data-arg"), p = el.getAttribute("data-arg2"), x = st.h.kpi_templates[t];
        var params = draft.params[t] = Object.assign({}, draft.params[t] || {});
        var spec = x.params[p], v = el.value;
        params[p] = spec.type === "int" || spec.type === "float" ? EK.readNumber(v) : v;
        var complete = Object.keys(x.params).every(function (q) { return params[q] != null && params[q] !== ""; });
        if (complete) {
          Object.keys(draft.kpis).forEach(function (k) { if (draft.kpis[k].template === t && !draft.levels.some(function (l) { return l.kpi === k; })) delete draft.kpis[k]; });
          var key = templateKey(t, params);
          draft.kpis[key] = { template: t, params: params };
          setFirst(key);
          draft.open = null;
        }
        redraw();
      },
      "second-add": function () {
        var other = Object.keys(draft.kpis).filter(function (k) { return k !== current(); })[0] || Object.keys(st.h.kpis || {}).filter(function (k) { return k !== current(); })[0];
        if (!other) return;
        if (!draft.kpis[other]) draft.kpis[other] = { from: "harness" };
        draft.levels = [Object.assign({}, draft.levels[0], { equal_within: "1 %" }), { kpi: other, direction: dirOf(other) }];
        redraw();
      },
      second: function (el) {
        var k = el.value;
        if (!draft.kpis[k] && (st.h.kpis || {})[k]) draft.kpis[k] = { from: "harness" };
        draft.levels[1] = { kpi: k, direction: dirOf(k) };
        redraw();
      },
      "second-remove": function () { draft.levels = [{ kpi: draft.levels[0].kpi, direction: draft.levels[0].direction }]; redraw(); },
      "goal-all": function () { draft.showAll = true; redraw(); },
      done: function () {
        if (!draft.levels.length) { EK.closeDrawer(); return; }
        var lv = draft.levels.map(function (l, i) { var o = { kpi: l.kpi, direction: l.direction }; if (i < draft.levels.length - 1) o.equal_within = l.equal_within || "1 %"; return o; });
        save({ kpis: draft.kpis, goal: { levels: lv } }, main).then(function () { EK.closeDrawer(); });
      }
    };
    var name = st.h.title || st.h.application.label;
    EK.openDrawer({ kind: "goal", title: "Goal", sub: "What counts as better: " + name + "’s own measures", wide: true, dims: true, body: body(),
      foot: html`<div class="actions">${EK.button("Done", { act: "done" })}${EK.button("Cancel", { kind: "quiet", act: "drawer-close" })}</div>`,
      acts: dacts, backLabel: "Back to Set up" });
    redrawDrawer = null;
    if (!st.choices && Object.keys(st.h.kpi_templates || {}).length) {
      EK.get(EK.studyPath(st.slug, "/choices")).then(function (c) { st.choices = c || {}; if (EK.drawerKind() === "goal") redraw(); }).catch(function () { st.choices = {}; });
    }
  }

  // ------------------------------------------------------------------ Time, runs, AI help, the expert plan
  function timeDrawer(main, focus) {
    var lim = study().limits || {}, b = study().budget || {}, ai = b.ai || {}, plan = study().plan || {};
    function body() {
      var hours = b.hours || 0, whole = Math.floor(hours), mins = Math.round((hours - whole) * 60);
      var perCase = lim.time_per_case_s || 0, unitMin = perCase >= 120 && perCase % 60 === 0;
      var keys = st.settings ? st.settings.keys || {} : {};
      var provider = st.settings ? st.settings.provider : "";
      var needs = { openrouter: ["OPENROUTER_API_KEY"], azure: ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"] }[provider] || [];
      var aiReady = st.settings && needs.every(function (k) { return keys[k]; });
      var p = st.plan || {};
      return html`<form class="sub-section" data-submit="save-time">
        <div class="fields">${EK.numberUnit({ id: "t-case", label: "Time per case", value: unitMin ? perCase / 60 : perCase, unit: unitMin ? "min" : "s", width: 180 })}
          ${EK.numberUnit({ id: "t-hours", label: "Time in total", value: whole, unit: "h", width: 140 })}${EK.numberUnit({ id: "t-mins", label: "and", value: mins, unit: "min", width: 140 })}</div>
        <div class="fields"><div class="field"><label class="field-label" for="t-runs">Runs per case</label><select class="input" id="t-runs" style="width:140px">
          ${[1, 2, 3, 5].map(function (n) { return html`<option value="${n}"${EK.attrs({ selected: n === (lim.runs_per_case || 1) })}>${n}</option>`; })}</select></div>
          <div class="field"><label class="field-label" for="t-retries">Retries on a failure</label><select class="input" id="t-retries" style="width:140px">
          ${[0, 1, 2, 3].map(function (n) { return html`<option value="${n}"${EK.attrs({ selected: n === (lim.retries || 0) })}>${n}</option>`; })}</select></div></div>
        <p class="type-small ink-2">More runs per case make each number steadier, and the study slower.</p>
        <section class="sub-section expert" id="ai-section"><h3 class="type-heading">AI help</h3>
          <p class="type-small ink-2">The AI proposes combinations too, from what the search has learned.</p>
          <label class="check-line"><input type="checkbox" id="t-ai"${EK.attrs({ checked: !!ai.enabled, disabled: !aiReady && !ai.enabled })}> Let the AI propose combinations</label>
          ${!aiReady ? html`<p class="type-small ink-2">Needs an AI service first: ${EK.link("Settings", { href: "#/settings" })}</p>` : ""}
          ${EK.numberUnit({ id: "t-ai-cap", label: "At most", value: ai.max_usd == null ? 1 : ai.max_usd, unit: provider === "copilot-cli" ? "$ (Copilot: requests)" : "$", width: 200 })}</section>
        <div class="actions">${EK.button("Save", { type: "submit" })}${EK.button("Cancel", { kind: "quiet", act: "drawer-close" })}</div></form>
        <details class="expert"${EK.attrs({ open: plan.auto === false })}><summary>Adjust the plan (for experts)</summary>
          <p class="type-small ink-2">Today: ${p.workers || "?"} at once · ${p.children || "?"} combinations a round · ${p.rounds || "?"} rounds · quick look on ${p.screen_cases || 0} cases.</p>
          <form class="fields" data-submit="expert">${EK.numberUnit({ id: "x-children", label: "Combinations a round", value: plan.children || p.children || "", unit: "", width: 200 })}
            ${EK.numberUnit({ id: "x-rounds", label: "Rounds", value: plan.rounds || p.rounds || "", unit: "", width: 140 })}
            <div class="actions" style="align-self:flex-end">${EK.button("Use these numbers", { kind: "secondary", size: "s", type: "submit" })}
            ${plan.auto === false ? EK.button("Back to automatic", { kind: "quiet", size: "s", act: "auto" }) : ""}</div></form></details>`;
    }
    var dacts = {
      "save-time": function () {
        var unit = EK.$("#t-case").closest(".nu-box").querySelector(".nu-unit").textContent.trim();
        var per = EK.readNumber(document.getElementById("t-case").value);
        var h = EK.readNumber(document.getElementById("t-hours").value) || 0, m = EK.readNumber(document.getElementById("t-mins").value) || 0;
        if (per == null || per <= 0) { EK.toast("error", "Time per case: a number above 0"); return; }
        var hours = h + m / 60;
        if (hours <= 0) { EK.toast("error", "Time in total: more than 0"); return; }
        var cap = EK.readNumber(document.getElementById("t-ai-cap").value);
        save({ limits: { time_per_case_s: unit === "min" ? per * 60 : per, retries: Number(document.getElementById("t-retries").value), runs_per_case: Number(document.getElementById("t-runs").value) },
          budget: { hours: Math.round(hours * 1000) / 1000, ai: { enabled: document.getElementById("t-ai").checked, max_usd: cap == null ? 1 : cap } } }, main).then(function () { EK.closeDrawer(); EK.toast("done", "Saved"); });
      },
      expert: function () {
        var c = EK.readNumber(document.getElementById("x-children").value), r = EK.readNumber(document.getElementById("x-rounds").value);
        if (!(c > 0) || !(r > 0)) { EK.toast("error", "Two whole numbers above 0"); return; }
        save({ plan: { auto: false, children: Math.round(c), rounds: Math.round(r) } }, main).then(function () { EK.refreshDrawer({ body: body() }); });
      },
      auto: function () { save({ plan: { auto: true } }, main).then(function () { EK.refreshDrawer({ body: body() }); }); }
    };
    EK.openDrawer({ kind: "time", title: "Time", sub: "How long the study may take", wide: true, dims: true, body: body(), acts: dacts, backLabel: "Back to Set up" });
    EK.get("/api/settings").then(function (s) {
      st.settings = s;
      if (EK.drawerKind() === "time") { EK.refreshDrawer({ body: body() }); if (focus === "ai") { var el = document.getElementById("t-ai"); if (el) el.focus(); } }
    }).catch(function () { /* AI help stays as it is */ });
  }

  // ------------------------------------------------------------------ Rules
  function rulesDrawer(main) {
    function body() {
      var list = study().constraints || [];
      var names = tuned().map(function (s) { return s.name; }).concat(Object.keys(vary().data || {}));
      return html`${list.length ? html`<ul class="row-list">${list.map(function (c, i) {
          return html`<li><div class="row-main"><span class="strong">${c.says || "A rule"}</span><span class="mono type-small ink-2">${c.expr || c.sql}</span></div>
            ${EK.button("Remove", { kind: "quiet", size: "s", act: "remove", arg: i })}</li>`;
        })}</ul>` : html`<p class="type-body ink-2">No rules: every combination may be tried.</p>`}
        <form class="sub-section expert" data-submit="add"><h3 class="type-heading">Write a rule</h3>
          ${EK.textInput({ id: "rule-says", label: "In words", placeholder: "Keep the smallest below the largest" })}
          ${EK.textInput({ id: "rule-expr", label: "As an expression", mono: true, placeholder: names.length > 1 ? names[0] + " < " + names[1] : "a < b", hint: names.length ? "Names: " + names.join(", ") : "" })}
          <div>${EK.button("Add the rule", { kind: "secondary", type: "submit" })}</div></form>`;
    }
    var dacts = {
      remove: function (el) {
        var i = Number(el.getAttribute("data-arg")), list = (study().constraints || []).slice(), gone = list.splice(i, 1)[0];
        save({ constraints: list }, main).then(function () {
          EK.refreshDrawer({ body: body() });
          EK.undo("Rule removed.", function () {
            var back = (study().constraints || []).slice(); back.splice(i, 0, gone);
            return save({ constraints: back }, main).then(function () { if (EK.drawerKind() === "rules") EK.refreshDrawer({ body: body() }); });
          });
        });
      },
      add: function () {
        var says = document.getElementById("rule-says").value.trim(), expr = document.getElementById("rule-expr").value.trim();
        if (!expr) return;
        EK.post(EK.studyPath(st.slug, "/cards"), { card: { kind: "constraint", says: says || expr, expr: expr } }).then(function (res) {
          st.doc = res.study; draw(main); EK.refreshDrawer({ body: body() }); EK.toast("done", "Rule added");
        }).catch(EK.fail);
      }
    };
    EK.openDrawer({ kind: "rules", title: "Rules", sub: "Combinations that may not be tried", wide: true, dims: true, body: body(), acts: dacts, backLabel: "Back to Set up" });
  }

  // ------------------------------------------------------------------ Measures (own measures, guardrails, the expert box)
  function measuresDrawer(main) {
    function body() {
      var k = study().kpis || {}, inUse = {};
      levels().forEach(function (l) { inUse[l.kpi] = 1; });
      (study().guardrails || []).forEach(function (g) { inUse[g.kpi] = 1; });
      Object.keys(k).forEach(function (n) { if (k[n].weighted) Object.keys(k[n].weighted).forEach(function (w) { inUse[w] = 1; }); });
      var rows = Object.keys(k).map(function (n) {
        var g = (study().guardrails || []).filter(function (x) { return x.kpi === n; })[0], today = todayValue(n);
        return html`<li><div class="row-main"><span class="strong">${kpiLabel(n)}</span>
          <span class="type-small ink-2">${dirWords(kpiDirection(n))}${today != null ? " · today " + today : ""}${g ? " · limit: " + (g.max != null ? "at most " + g.max : "at least " + g.min) : ""}</span></div>
          ${EK.button(g ? "No limit" : "Set a limit", { kind: "quiet", size: "s", act: "guard", arg: n })}
          ${inUse[n] && !g ? html`<span class="badge">in use</span>` : !inUse[n] ? EK.button("Remove", { kind: "quiet", size: "s", act: "remove", arg: n }) : ""}</li>`;
      });
      return html`<ul class="row-list">${rows}</ul>
        <p class="type-small ink-2">A limit keeps a measure in bounds: a combination that breaks it does not count.</p>
        <details class="expert"><summary>Your own measure (SQL, for experts)</summary>
          <form class="sub-section" data-submit="own">${EK.textInput({ id: "m-name", label: "Short name", mono: true, placeholder: "late_tasks" })}
            ${EK.textInput({ id: "m-says", label: "What it counts", placeholder: "Tasks served after their window" })}
            <div class="field"><label class="field-label" for="m-dir">Better is</label><select class="input" id="m-dir" style="width:160px"><option value="lower">lower</option><option value="higher">higher</option></select></div>
            ${EK.textInput({ id: "m-sql", label: "SQL returning one number", mono: true, multiline: true })}
            <div class="actions">${EK.button("Try it", { kind: "secondary", size: "s", act: "try" })}${EK.button("Add the measure", { kind: "secondary", size: "s", type: "submit" })}</div>
            <div id="m-tried" aria-live="polite"></div></form></details>`;
    }
    var dacts = {
      remove: function (el) {
        var n = el.getAttribute("data-arg"), k = Object.assign({}, study().kpis), gone = k[n];
        delete k[n];
        save({ kpis: k }, main).then(function () {
          EK.refreshDrawer({ body: body() });
          EK.undo("Measure removed.", function () {
            var back = Object.assign({}, study().kpis); back[n] = gone;
            return save({ kpis: back }, main).then(function () { if (EK.drawerKind() === "measures") EK.refreshDrawer({ body: body() }); });
          });
        });
      },
      guard: function (el) {
        var n = el.getAttribute("data-arg"), list = (study().guardrails || []).slice(), at = -1;
        list.forEach(function (g, i) { if (g.kpi === n) at = i; });
        if (at >= 0) { list.splice(at, 1); return save({ guardrails: list }, main).then(function () { EK.refreshDrawer({ body: body() }); }); }
        var today = ((st.doc.preview || {}).result || {}).kpis || {};
        var bound = today[n];
        EK.askText("A limit on " + kpiLabel(n), (kpiDirection(n) === "lower" ? "At most" : "At least") + (bound != null ? " (today " + EK.num(bound) + ")" : ""), bound != null ? String(bound) : "", "Set the limit").then(function (v) {
          var x = EK.readNumber(v);
          if (x == null) return measuresDrawer(main);
          var g = { kpi: n }; g[kpiDirection(n) === "lower" ? "max" : "min"] = x;
          list.push(g);
          return save({ guardrails: list }, main).then(function () { measuresDrawer(main); });
        });
      },
      "try": function () {
        var sql = document.getElementById("m-sql").value.trim(), out = document.getElementById("m-tried");
        if (!sql) return;
        out.textContent = "Trying…";
        EK.post(EK.studyPath(st.slug, "/kpis/try"), { sql: sql }).then(function (r) {
          out.innerHTML = String(html`<p class="type-small">Today: <strong>${r.value == null ? "nothing" : EK.num(r.value)}</strong>${r.note ? " · " + r.note : ""}</p>`);
        }).catch(function (err) { out.innerHTML = String(EK.notice("error", err.message)); });
      },
      own: function () {
        var card = { kind: "kpi", name: document.getElementById("m-name").value.trim(), says: document.getElementById("m-says").value.trim(),
          direction: document.getElementById("m-dir").value, sql: document.getElementById("m-sql").value.trim() };
        if (!card.name || !card.sql) { EK.toast("error", "A name and the SQL, please"); return; }
        EK.post(EK.studyPath(st.slug, "/cards"), { card: card }).then(function (res) { st.doc = res.study; draw(main); EK.refreshDrawer({ body: body() }); EK.toast("done", "Measure added"); }).catch(EK.fail);
      }
    };
    EK.openDrawer({ kind: "measures", title: "Measures", sub: "What the study counts on every run", wide: true, dims: true, body: body(), acts: dacts, backLabel: "Back to Set up" });
  }

  // ------------------------------------------------------------------ Data changes
  function dataDrawer(main) {
    var levers = st.h.levers || {};
    function body() {
      var data = vary().data || {};
      var list = Object.keys(data).map(function (n) {
        var x = data[n], today = (st.doc.data_today || {})[n];
        return html`<li><div class="row-main"><span class="strong">${x.says || n}</span><span class="type-small ink-2">${(levers[x.lever] || {}).label || x.lever} · ${x.mode} · ${x.low} to ${x.high}${today ? " · today " + today : ""}</span></div>
          ${EK.button("Remove", { kind: "quiet", size: "s", act: "remove", arg: n })}</li>`;
      });
      var names = Object.keys(levers);
      if (!names.length) return html`<p class="type-body ink-2">This application has no data that may change.</p>`;
      var lv = levers[st.lever || names[0]] || levers[names[0]];
      return html`${list.length ? html`<ul class="row-list">${list}</ul>` : html`<p class="type-body ink-2">No data changes yet.</p>`}
        <form class="sub-section expert" data-submit="add"><h3 class="type-heading">Add a data change</h3>
          <div class="field"><label class="field-label" for="d-lever">What changes</label><select class="input" id="d-lever" data-change="lever">${names.map(function (n) {
            return html`<option value="${n}"${EK.attrs({ selected: n === (st.lever || names[0]) })}>${levers[n].label || n}</option>`; })}</select></div>
          ${lv.help ? html`<p class="type-small ink-2">${lv.help}</p>` : ""}
          ${Object.keys(lv.columns || {}).length ? html`<div class="field"><label class="field-label" for="d-column">Which part</label><select class="input" id="d-column">${Object.keys(lv.columns).map(function (c) { return html`<option value="${c}">${lv.columns[c] || c}</option>`; })}</select></div>` : ""}
          <div class="field"><label class="field-label" for="d-mode">How</label><select class="input" id="d-mode">${(lv.modes || []).map(function (m) { return html`<option>${m}</option>`; })}</select></div>
          ${EK.textInput({ id: "d-name", label: "Short name", mono: true })}
          <div class="fields">${EK.numberUnit({ id: "d-low", label: "From", value: "", unit: "", width: 140 })}${EK.numberUnit({ id: "d-high", label: "To", value: "", unit: "", width: 140 })}${EK.numberUnit({ id: "d-start", label: "Start at", value: "", unit: "", width: 140 })}</div>
          ${EK.textInput({ id: "d-where", label: "Only where (SQL, optional)", mono: true })}
          <div>${EK.button("Add the change", { kind: "secondary", type: "submit" })}</div></form>`;
    }
    var dacts = {
      lever: function (el) { st.lever = el.value; EK.refreshDrawer({ body: body() }); },
      remove: function (el) {
        var n = el.getAttribute("data-arg"), data = Object.assign({}, vary().data), gone = data[n];
        delete data[n];
        save({ vary: { settings: vary().settings || {}, data: data } }, main).then(function () {
          EK.refreshDrawer({ body: body() });
          EK.undo("Data change removed.", function () {
            var back = Object.assign({}, vary().data); back[n] = gone;
            return save({ vary: { settings: vary().settings || {}, data: back } }, main).then(function () { if (EK.drawerKind() === "data") EK.refreshDrawer({ body: body() }); });
          });
        });
      },
      add: function () {
        var g = function (id) { var e = document.getElementById(id); return e ? e.value.trim() : ""; };
        var card = { kind: "data_change", lever: g("d-lever"), column: g("d-column") || undefined, mode: g("d-mode"), name: g("d-name"),
          low: EK.readNumber(g("d-low")), high: EK.readNumber(g("d-high")), start: EK.readNumber(g("d-start")), where: g("d-where") };
        if (card.start == null) delete card.start;
        EK.post(EK.studyPath(st.slug, "/cards"), { card: card }).then(function (res) { st.doc = res.study; draw(main); EK.refreshDrawer({ body: body() }); EK.toast("done", "Data change added"); }).catch(EK.fail);
      }
    };
    EK.openDrawer({ kind: "data", title: "Data changes", sub: "What may change in each case", wide: true, dims: true, body: body(), acts: dacts, backLabel: "Back to Set up" });
  }

  EK.pages.setup = { render: render };
})();
