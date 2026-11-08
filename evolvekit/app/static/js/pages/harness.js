/* evolvekit app: What we learned — one application's finished studies, setting by setting.
   #/harness/<id>. The page claims nothing of its own: each study shows its own verdict line,
   and a winning value comes only from a study whose final check said use. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;

  /** "14 Nov": the day a study finished. */
  function dayOf(stamp) {
    var t = new Date(stamp);
    return isNaN(t) ? "" : t.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  }

  function studiesList(studies) {
    return html`<ul class="row-list learned-studies">${studies.map(function (s) {
      return html`<li>${EK.verdictMark(EK.markOf({ kind: "finished", decision: s.decision }), 28)}
        <div class="row-main"><span class="strong"><a class="link" href="${EK.studyHref(s.slug, s.run)}">${s.name}</a></span>
        <span class="type-small ink-2">finished ${dayOf(s.finished_at)}</span>
        <span class="type-small">${EK.figures(s.line || "")}</span></div></li>`;
    })}</ul>`;
  }

  function importanceCell(s) {
    if (s.importance == null) return html`<span class="type-small ink-2">not enough tries to tell</span>`;
    var pct = Math.max(0, Math.min(100, Math.round(s.importance * 100)));
    return html`<span class="imp"><span class="imp-bar" aria-hidden="true"><span class="imp-fill" style="width:${pct}%"></span></span>
      <span class="type-small">${EK.num(s.importance, 2)} · from ${EK.plural(s.importance_n, "study", "studies")}</span></span>`;
  }

  function winnersCell(s, names) {
    if (!s.winners.length) return html`<span class="type-small ink-2">none yet</span>`;
    return html`<ul class="plain-list">${s.winners.map(function (w) {
      return html`<li class="type-small"><span class="strong">${String(w.value)}</span> in ${names[w.study] || w.study} (${dayOf(w.finished_at)})</li>`;
    })}</ul>`;
  }

  /** Where the searches leaned (`leaned`, the median of the studies' search-best values): a search
      figure, never a winner, so it says so beside it. */
  function leanedCell(s) {
    var v = s.leaned;
    if (v == null) return html`<span class="type-small ink-2">—</span>`;
    var text = typeof v === "number" ? (Number.isInteger(v) ? EK.int(v) : String(Number(v.toPrecision(3)))) : String(v);
    return html`<span class="type-small"><span class="strong">${text}</span> <span class="ink-2">(search, not checked)</span></span>`;
  }

  /** The page's body: the title, the studies, the settings table, what was left out. */
  function learnedView(h) {
    var learned = h.learned, studies = learned.studies || [], names = {};
    studies.forEach(function (s) { names[s.slug] = s.name; });
    var title = EK.titleBlock("", "What we learned · " + h.title,
      studies.length ? "From " + studies.length + " finished studies on this application. A winning value comes only from a study whose final check said use."
        : "No study on this application has finished yet.");
    if (!studies.length) return html`${title}`;
    var moved = { up: "up", down: "down", mixed: "both ways" };
    var rows = Object.keys(learned.settings || {}).map(function (name) {
      var s = learned.settings[name];
      return html`<tr><td><span class="strong">${s.label || name}</span></td><td>${s.tuned_in}</td><td>${importanceCell(s)}</td>
        <td>${leanedCell(s)}</td><td>${winnersCell(s, names)}</td><td>${moved[s.direction] || "—"}</td></tr>`;
    });
    // The strength's scale and what it is not (persona p4, H4): a search figure, not proof.
    var table = rows.length ? html`<div class="cases-wrap"><table class="changed-table"><thead><tr><th>Setting</th><th>Tuned in</th>
      <th>How strongly it went with the measure (0 to 1, not proof)</th><th>The searches leaned to</th><th>Winning values</th><th>Winners moved it</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="type-small ink-2">0 means the setting did not go with the measure in the searches, 1 that it went with it every time. It comes from the searches, not from final checks: it says which settings are worth tuning, not which value wins.</p>`
      : "";
    var notes = (learned.notes || []).length ? html`<section class="sub-section"><h2 class="type-heading">Left out</h2>
      <ul class="plain-list">${learned.notes.map(function (n) { return html`<li class="type-small ink-2">${n}</li>`; })}</ul></section>` : "";
    return html`${title}${studiesList(studies)}${table}${notes}`;
  }

  function render(main, id, query, gen) {
    return EK.get("/api/harnesses/" + EK.q(id)).then(function (h) {
      if (!EK.isCurrent(gen)) return;
      document.title = "What we learned · " + h.title + " · evolvekit";
      EK.mount(main, html`<div class="page"><div class="page-inner">${learnedView(h)}<p>${EK.link("Home", { href: "#/" })}</p></div></div>`);
    }, function (err) {
      if (!EK.isCurrent(gen)) return;
      EK.mount(main, html`<div class="page"><div class="page-inner narrow">${EK.titleBlock("", "What we learned")}${EK.notice("error", err.message)}<p>${EK.link("Home", { href: "#/" })}</p></div></div>`);
    });
  }

  EK.pages.harness = { render: render };
})();
