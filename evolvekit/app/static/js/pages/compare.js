/* evolvekit app: Compare — which did better? Two studies or two runs side by
   side, aligned by label: the answer, the likely range, the held-back cases
   joined by name, what changed, the set-up. #/compare?a=<slug>[/<run>]&b=… */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;

  function sideKind(s) {
    var f = s.final || {};
    if (f.state === "missing" || f.state === "skipped" || f.state === "stopped") return "none";
    return { use: "use", keep: "keep", worse: "worse", single: "none" }[f.decision] || (f.state === "confirmed" ? "use" : "keep");
  }
  function checked(s) { var f = s.final || {}; return !(f.state === "missing" || f.state === "skipped" || f.state === "stopped"); }
  function answer(s) {
    var f = s.final || {}, kind = sideKind(s);
    if (!checked(s)) {
      return [kind, (s.improvement_pct != null ? EK.capital(EK.better(s.improvement_pct)) : "A number") + ", not checked", "On its own study cases"];
    }
    var w = EK.answerWords({ decision: f.decision, mean: f.mean, ci95: f.ci95, chance: f.chance, what: "settings" });
    var sub = (EK.capital(EK.chanceWords(f.chance)) || "") + (f.n ? (f.chance != null ? " · " : "") + EK.plural(f.n, "held-back case") : "");
    return [kind, w.head, sub];
  }
  function overlap(a, b) { return a && b && a[0] <= b[1] && b[0] <= a[1]; }

  function verdictHead(d) {
    var A = d.a, B = d.b, ka = sideKind(A), kb = sideKind(B);
    if (!d.same_goal) return ["Compare · different goals", "Different goals: no winner", "Each gain is against its own starting point."];
    if (checked(A) && !checked(B)) return ["Compare · one side unchecked", "Only A has a final check", "B’s number may not hold on new cases."];
    if (!checked(A) && checked(B)) return ["Compare · one side unchecked", "Only B has a final check", "A’s number may not hold on new cases."];
    if (!checked(A) && !checked(B)) return ["Compare · not checked", "Neither has a final check", "Both numbers are on their own cases."];
    var both = ka === kb ? (ka === "use" ? "both say use it" : ka === "keep" ? "both say keep" : "both say the same") : null;
    var fa = A.final.ci95, fb = B.final.ci95;
    if (both && overlap(fa, fb)) return ["Compare · same goal", "About the same: " + both, "Their likely ranges overlap."];
    if (fa && fb && !overlap(fa, fb)) return ["Compare · same goal", (fa[0] > fb[1] ? "A" : "B") + " did better", "Their likely ranges do not overlap."];
    return ["Compare · same goal", "A says " + (ka === "use" ? "use it" : ka) + ", B says " + (kb === "use" ? "use it" : kb), "Read each range below."];
  }

  function changes(s) {
    var ch = (s.changes || []).filter(function (c) { return c.changed; });
    if (!ch.length) return html`<p class="type-small ink-2">Nothing changed.</p>`;
    return html`<ul class="row-list">${ch.slice(0, 12).map(function (c) {
      return html`<li><span class="type-small"><span class="strong">${c.label || c.name}</span>: ${c.old_text} → <strong>${c.new_text}</strong></span></li>`;
    })}</ul>${ch.length > 12 ? html`<p class="type-small ink-2">${ch.length - 12} more</p>` : ""}`;
  }

  function picker(main, a, gen) {
    return EK.get("/api/home").then(function (home) {
      if (!EK.isCurrent(gen)) return;
      var slug = a.split("/")[0];
      var others = home.studies.filter(function (s) { return s.slug !== slug && (s.kind === "finished" || s.kind === "stopped" || s.kind === "failed" || s.kind === "running"); });
      return EK.get(EK.studyPath(slug)).then(function (doc) {
        var runs = (doc.runs || []).filter(function (r) { return r.kind !== "queued" && a !== slug + "/" + r.id; });
        var options = runs.map(function (r) { return html`<option value="${slug + "/" + r.id}">${doc.study.name} · run of ${EK.when(r.started_at)}</option>`; })
          .concat(others.map(function (s) { return html`<option value="${s.slug}">${s.name}</option>`; }));
        EK.mount(main, html`<div class="page"><div class="page-inner narrow">${EK.titleBlock("Compare", "Compare with what?", doc.study.name)}
          ${options.length ? html`<form class="sub-section" data-submit="go"><div class="field"><label class="field-label" for="cmp-b">B</label><select class="input" id="cmp-b">${options}</select></div>
            <div>${EK.button("Compare", { type: "submit" })}</div></form>` : EK.notice("info", "Nothing else has run yet.")}</div></div>`,
          { go: function () { EK.go("#/compare?a=" + EK.q(a) + "&b=" + EK.q(document.getElementById("cmp-b").value)); } });
        EK.companion.show({ phase: "egg", say: "Pick what to compare with." });
      });
    });
  }

  function render(main, slug, query, gen) {
    if (!query.a) {
      EK.mount(main, html`<div class="page"><div class="page-inner narrow">${EK.titleBlock("Compare", "Which did better?")}
        ${EK.notice("info", "Pick two studies on Home, under Compare two studies.")}<p>${EK.link("Home", { href: "#/" })}</p></div></div>`);
      return;
    }
    if (!query.b) return picker(main, query.a, gen);
    return EK.get("/api/compare?a=" + EK.q(query.a) + "&b=" + EK.q(query.b)).then(function (d) {
      if (!EK.isCurrent(gen)) return;
      var head = verdictHead(d), phone = EK.isPhone(), w = phone ? 300 : 440;
      var A = d.a, B = d.b, aa = answer(A), ab = answer(B);
      var link = function (s) { return EK.studyHref(s.slug, s.run); };
      var title = function (s, tag) {
        return html`<div><span class="type-label ink-2">${tag}</span><p class="type-heading"><a class="card-link" href="${link(s)}">${s.name}</a></p>
          <p class="type-small ink-2">${s.harness} · ${EK.hours(s.setup.hours)}</p></div>`;
      };
      var range = function (s) {
        var f = s.final || {};
        if (!checked(s) || f.mean == null) return html`<p class="type-small ink-2">No range: no final check.</p>`;
        return html`${EK.intervalStrip(f.mean, f.ci95 && f.ci95[0], f.ci95 && f.ci95[1], { width: w })}<p class="type-small ink-2">${EK.rangeWords(f.ci95 && f.ci95[0], f.ci95 && f.ci95[1])}</p>`;
      };
      var shared = {}, rows = [];
      (A.held || []).forEach(function (h) { shared[h.case] = [h.change_pct, null]; });
      (B.held || []).forEach(function (h) { if (shared[h.case]) shared[h.case][1] = h.change_pct; });
      Object.keys(shared).forEach(function (k) { if (shared[k][1] != null) rows.push([EK.shortCase(k), shared[k][0], shared[k][1]]); });
      var cases = d.same_goal && rows.length ? html`<section class="compare-row" style="grid-template-columns:${phone ? "1fr" : "160px minmax(0,1fr)"}"><h3 class="type-label ink-2">Held-back cases</h3>
        <div>${EK.casePair(rows, { width: phone ? 358 : 1000 })}<p class="type-small ink-2">Same ${EK.plural(rows.length, "held-back case")} on both sides.</p></div></section>`
        : html`${EK.compareRow("Held-back cases", html`<p class="type-small">${EK.plural((A.held || []).length, "case")}</p>`, html`<p class="type-small">${EK.plural((B.held || []).length, "case")}</p>`,
            d.same_goal ? html`<p class="type-small ink-2">No held-back case in common.</p>` : "")}`;
      var setup = function (s) {
        return html`<p class="type-small">${EK.duration(s.setup.time_per_case_s)} a case · ${EK.hours(s.setup.hours)} · ${s.setup.combinations != null ? EK.int(s.setup.combinations) + " combinations" : ""}</p>`;
      };
      EK.mount(main, html`<div class="page"><div class="page-inner">${EK.titleBlock(head[0], head[1], head[2])}
        <div class="compare-head wide-only"><span></span>${title(A, "A")}${title(B, "B")}</div>
        <div class="phone-only">${title(A, "A")}${title(B, "B")}</div>
        ${EK.compareRow("Goal", html`<p class="type-small">${EK.capital(A.goal.says)}, ${A.goal.direction}</p>`, html`<p class="type-small">${EK.capital(B.goal.says)}, ${B.goal.direction}</p>`,
          !d.same_goal ? EK.notice("warning", "Different goals: the numbers do not compare.") : "")}
        ${EK.compareRow("Answer", EK.verdictLine(aa[0], aa[1], aa[2]), EK.verdictLine(ab[0], ab[1], ab[2]))}
        ${EK.compareRow("Likely range", range(A), range(B))}
        ${cases}
        ${EK.compareRow("What changed", changes(A), changes(B))}
        ${EK.compareRow("Set-up", setup(A), setup(B))}
        <div class="actions">${EK.link("Swap A and B", { href: "#/compare?a=" + EK.q(query.b) + "&b=" + EK.q(query.a) })}${EK.link("Home", { href: "#/" })}</div></div></div>`);
      EK.companion.show({ phase: sideKind(A) === "use" || sideKind(B) === "use" ? "butterfly" : "butterfly-dull", say: head[1] + ". " + head[2] });
    }, function (err) {
      if (!EK.isCurrent(gen)) return;
      EK.mount(main, html`<div class="page"><div class="page-inner narrow">${EK.titleBlock("Compare", "Nothing to compare")}${EK.notice("error", err.message)}<p>${EK.link("Home", { href: "#/" })}</p></div></div>`);
    });
  }

  EK.pages.compare = { render: render };
})();
