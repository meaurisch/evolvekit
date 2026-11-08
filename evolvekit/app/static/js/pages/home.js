/* evolvekit app: Home — your studies. Cards with their state, verdict and
   Pepper's phase; Active · Archived · Deleted; one click away: the
   applications you can tune, adding a harness or a study zip, comparing two
   studies, the library folder. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  var HAS_RUN = { running: 1, finished: 1, stopped: 1, failed: 1 };
  var data = null;

  function studyMenu(s) {
    var items = [{ text: "Rename", act: "rename", arg: s.slug }, { text: "Duplicate", act: "duplicate", arg: s.slug }];
    if (HAS_RUN[s.kind]) items.push({ text: "Compare with…", act: "compare-with", arg: s.slug }, { text: "Past runs", act: "runs", arg: s.slug });
    items.push({ text: "Save as zip", act: "zip", arg: s.slug }, null,
      { text: s.archived ? "Restore from archive" : "Archive", act: "archive", arg: s.slug },
      { text: "Delete", act: "delete", arg: s.slug, danger: true });
    return items;
  }

  function cards(list) {
    return html`<div class="card-grid">${list.map(function (s) {
      var c = EK.cardOf(s);
      c.menu = s.kind === "broken" ? [{ text: "Delete", act: "delete", arg: s.slug, danger: true }] : studyMenu(s);
      if (s.archived) c.actions = [{ text: "Restore", act: "archive", arg: s.slug, kind: "secondary" }];
      return EK.studyCard(c);
    })}</div>`;
  }

  function trashCards(trash) {
    if (!trash.length) return html`<p class="type-body ink-2">Nothing deleted in the last 7 days.</p>`;
    return html`<div class="card-grid">${trash.map(function (t) {
      return EK.studyCard({ slug: t.entry, name: t.name, kicker: "Deleted " + EK.when(t.deleted_at), head: "Restore it within 7 days", sub: "",
        mark: null, href: "#/?show=deleted", actions: [{ text: "Restore", act: "restore", arg: t.entry, kind: "secondary" }] });
    })}</div>`;
  }

  /** What we learned: the harness of the newest finished study; the Applications drawer when there is none. */
  function learnedLink() {
    var newest = (data.studies || []).filter(function (s) { return s.kind === "finished" && s.harness_id && !s.archived; })[0];
    var icon = EK.gl("chev", 18);
    return newest ? EK.link("What we learned", { href: "#/harness/" + EK.q(newest.harness_id), icon: icon })
      : EK.link("What we learned", { act: "apps", icon: icon });
  }
  function links() {
    var apps = (data.harnesses || []).length;
    return html`<div class="home-links">
      ${EK.link("Applications (" + apps + ")", { act: "apps", icon: EK.gl("folder", 18) })}
      ${learnedLink()}
      ${EK.link("Add a harness or zip", { act: "add", icon: EK.gl("drop", 18) })}
      ${EK.link("Compare two studies", { act: "compare", icon: EK.gl("chev", 18) })}
      ${EK.link("Library folder", { act: "library", icon: EK.gl("folder", 18) })}</div>`;
  }

  var remembered = null;
  /** After a change made here: draw from a fresh answer, never the remembered one. */
  function freshRender() { remembered = null; EK.rerender(); }
  function render(main, slug, query, gen) {
    // Back on Home: the last answer at once, then the fresh one when it differs.
    if (remembered) {
      data = remembered;
      draw(main, query);
      EK.get("/api/home").then(function (d) {
        var same = JSON.stringify(d) === JSON.stringify(remembered);
        remembered = d;
        if (!same && EK.isCurrent(gen) && !document.body.classList.contains("drawer-open")) { data = d; EK._moveFocus = false; draw(main, query); }
      }).catch(function () { /* the page shows what it had */ });
      return Promise.resolve().then(function () { poll(main, query, gen); });
    }
    return EK.get("/api/home").then(function (d) {
      if (!EK.isCurrent(gen)) return;
      data = remembered = d;
      draw(main, query);
      poll(main, query, gen);
    });
  }
  function poll(main, query, gen) {
    // Chosen again after every answer: 10 s while a study runs or waits in the queue; 30 s while a
    // campaign is armed between windows, so the next window's words never go stale (D20); else none.
    var d = data;
    var live = d.studies.some(function (s) { return s.kind === "running" || s.kind === "queued"; });
    var armed = d.studies.some(function (s) { return s.kind === "campaign" || (s.campaign && s.campaign.state === "armed"); });
    if (!live && !armed) return;
    EK.later(live ? 10000 : 30000, function () {
      EK.get("/api/home").then(function (n) {
        if (!EK.isCurrent(gen)) return;
        data = remembered = n;
        var grid = EK.$(".home-cards", main);
        if (grid && !EK.$(".menu:not([hidden])", main)) {
          var busy = document.activeElement && document.activeElement !== main && main.contains(document.activeElement);
          if (!busy && !document.body.classList.contains("drawer-open")) { EK._moveFocus = false; draw(main, query); }
        }
      }).catch(function () { /* the next poll tries again */ }).then(function () { if (EK.isCurrent(gen)) poll(main, query, gen); });
    });
  }

  function draw(main, query) {
    var show = query.show || "active";
    var active = data.studies.filter(function (s) { return !s.archived; });
    var archived = data.studies.filter(function (s) { return s.archived; });
    var trash = data.trash || [];
    // A rehearsal clock (`evolvekit app --clock`): the campaigns' windows follow it, runs keep real time.
    var clock = data.clock ? html`<div class="home-clock">${EK.notice("warning", data.clock.line)}</div>` : "";
    companion(active);
    var newBtn = EK.button("New study", { act: "new", icon: EK.gl("plus", 18) });
    var body;
    if (!data.studies.length && !trash.length) {
      body = html`${clock}<div class="hero"><div aria-hidden="true">${EK.pepper("egg", EK.isPhone() ? 200 : 280)}</div>
        <h1 class="type-display" id="page-title" tabindex="-1">No studies yet</h1>
        <p class="type-body ink-2">Start one in a few steps. Pepper, the butterfly, follows it.</p>
        <div class="actions" style="justify-content:center;margin-top:8px">${newBtn}${EK.button("Open a study zip", { kind: "secondary", act: "pick-zip" })}</div></div>
        <div style="display:flex;justify-content:center"><div style="width:100%;max-width:560px">${dropZone()}</div></div>${links()}`;
    } else {
      var tabs = [["active", "Active", 0], ["archived", "Archived", archived.length], ["deleted", "Deleted", trash.length]];
      var list = show === "archived" ? html`<p class="type-small ink-2">Archived studies keep everything. Restore one to use it again.</p>${archived.length ? cards(archived) : html`<p class="type-body ink-2">Nothing archived.</p>`}`
        : show === "deleted" ? trashCards(trash)
        : active.length ? cards(active) : html`<p class="type-body ink-2">Every study is archived. ${EK.link("Show them", { href: "#/?show=archived" })}</p>`;
      body = html`${EK.titleBlock("Home", "Your studies", null, html`<span class="wide-only">${newBtn}</span>`)}
        <div class="phone-only">${newBtn}</div>
        <nav class="tabs-row" aria-label="Which studies">${tabs.map(function (t) {
          return html`<a href="#/${t[0] === "active" ? "" : "?show=" + t[0]}"${EK.attrs({ "aria-current": t[0] === show ? "page" : null })}>${t[1]}${t[2] ? " (" + t[2] + ")" : ""}</a>`;
        })}</nav>${clock}<div class="home-cards">${list}</div>${links()}`;
    }
    EK.mount(main, html`<div class="page"><div class="page-inner">${body}</div>
      <input type="file" accept=".zip" class="sr-only" id="zip-file" tabindex="-1" aria-hidden="true" data-change="zip-picked"></div>`, acts(main, query));
    wireDrop(main);
  }

  function companion(active) {
    var running = active.filter(function (s) { return s.kind === "running"; })[0];
    var latest = running || active.filter(function (s) { return s.kind === "finished"; })[0];
    if (!latest) { EK.companion.show({ phase: data.studies.length ? "egg" : null, say: "Start a study and I will follow it." }); return; }
    var c = EK.cardOf(latest);
    EK.companion.show({ phase: c.pepper || "egg", slug: latest.slug, say: latest.name + ": " + c.head + ". " + (c.sub || "") });
  }

  function dropZone() {
    return EK.dropZone({ id: "home-drop", title: "Or drop a harness or study zip", sub: html`Or ${EK.link("choose a zip", { act: "pick-zip" })}`, label: "Harness or study zip" });
  }

  function wireDrop(root) {
    EK.$$(".drop-zone", root).forEach(function (zone) {
      zone.addEventListener("dragover", function (ev) { ev.preventDefault(); zone.classList.add("is-dragging"); });
      zone.addEventListener("dragleave", function () { zone.classList.remove("is-dragging"); });
      zone.addEventListener("drop", function (ev) {
        ev.preventDefault();
        zone.classList.remove("is-dragging");
        var f = ev.dataTransfer.files && ev.dataTransfer.files[0];
        if (f) addZip(f);
      });
    });
  }

  /** A study zip becomes a study; any other zip is installed as a harness. */
  function addZip(file) {
    if (!/\.zip$/i.test(file.name)) { EK.toast("error", file.name + " is not a .zip file"); return; }
    file.arrayBuffer().then(function (buf) {
      var names = EK.zipNames(buf);
      var study = names.some(function (n) { return /^([^/]+\/)?study\.yaml$/.test(n); });
      if (study) {
        return EK.api("POST", "/api/studies?name=" + EK.q(file.name), new Blob([buf])).then(function (doc) {
          EK.toast("done", "Study added: " + doc.study.name);
          EK.go("#/setup/" + EK.q(doc.slug));
        });
      }
      return EK.api("POST", "/api/harnesses?name=" + EK.q(file.name), new Blob([buf])).then(installed);
    }).catch(EK.fail);
  }
  function installed(h) {
    EK.toast("done", h.already ? h.title + " is already " + h.already : "Installed " + h.title + " " + (h.version || ""));
    freshRender();
  }

  function acts(main, query) {
    return {
      menu: EK.toggleMenu,
      "new": function () { EK.go("#/start"); },
      "pick-zip": function () { document.getElementById("zip-file").click(); },
      "zip-picked": function (el) { if (el.files[0]) addZip(el.files[0]); el.value = ""; },
      rename: function (el) {
        var s = find(el.getAttribute("data-arg"));
        EK.closeMenus();
        EK.askText("Rename the study", "Name", s.name, "Rename").then(function (name) {
          if (!name || name === s.name) return;
          return EK.patch(EK.studyPath(s.slug), { name: name }).then(function () { EK.toast("done", "Renamed to " + name); freshRender(); });
        }).catch(EK.fail);
      },
      duplicate: function (el) {
        var s = find(el.getAttribute("data-arg"));
        EK.closeMenus();
        EK.askText("Duplicate the study", "Name of the copy", s.name + " (copy)", "Duplicate").then(function (name) {
          if (!name) return;
          return EK.post(EK.studyPath(s.slug, "/duplicate"), { name: name }).then(function (doc) {
            EK.toast("done", "Copied as " + doc.study.name);
            EK.go(((doc.study.step || 1) >= 4 ? "#/setup/" : "#/start/") + EK.q(doc.slug));
          });
        }).catch(EK.fail);
      },
      "compare-with": function (el) { EK.go("#/compare?a=" + EK.q(el.getAttribute("data-arg"))); },
      runs: function (el) { EK.go("#/study/" + EK.q(el.getAttribute("data-arg")) + "?runs=1"); },
      zip: function (el) { EK.closeMenus(); location.href = EK.withToken(EK.studyPath(el.getAttribute("data-arg"), "/download/study")); },
      archive: function (el) {
        var s = find(el.getAttribute("data-arg"));
        EK.closeMenus();
        EK.patch(EK.studyPath(s.slug), { archived: !s.archived }).then(function () {
          EK.toast("done", s.archived ? "“" + s.name + "” is back" : "“" + s.name + "” archived");
          freshRender();
        }).catch(EK.fail);
      },
      "delete": function (el) {
        var s = find(el.getAttribute("data-arg"));
        EK.closeMenus();
        EK.del(EK.studyPath(s.slug)).then(function (res) {
          freshRender();
          EK.undo("“" + s.name + "” deleted.", function () {
            return EK.post("/api/trash/" + EK.q(res.trashed) + "/restore").then(function () { EK.toast("done", "“" + s.name + "” is back"); freshRender(); });
          });
        }).catch(EK.fail);
      },
      restore: function (el) {
        EK.post("/api/trash/" + EK.q(el.getAttribute("data-arg")) + "/restore").then(function () { EK.toast("done", "Restored"); freshRender(); }).catch(EK.fail);
      },
      apps: appsDrawer,
      add: addDrawer,
      compare: compareDrawer,
      library: function () {
        EK.openDrawer({ title: "Library folder", sub: "Where your studies and applications live",
          body: html`<p class="code">${data.home}</p><div class="actions">${EK.button("Copy the path", { kind: "secondary", act: "copy" })}</div>`,
          acts: { copy: function () { EK.copy(data.home).then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Path copied" : "Could not copy"); }); } } });
      }
    };
    function find(slug) { return data.studies.filter(function (s) { return s.slug === slug; })[0]; }
  }

  function appsDrawer() {
    var list = (data.harnesses || []).map(function (h) {
      if (h.problem) return html`<li><div class="row-main"><span class="strong">${h.id}</span><span class="type-small tone-critical">Cannot be read: ${h.problem}</span></div></li>`;
      return html`<li><div class="row-main"><span class="strong">${h.title} <span class="ink-2">${h.version || ""}</span></span>
        <span class="type-small ink-2">${h.summary || ""}</span>
        <span class="type-small ink-2">${(h.templates || []).map(function (t) { return t.title; }).join(" · ")}</span></div>
        ${EK.link("What we learned", { href: "#/harness/" + EK.q(h.id) })}
        <span class="badge">${h.built_in ? "built in" : "installed"}</span></li>`;
    });
    EK.openDrawer({ title: "Applications you can tune", sub: "Each comes with its harness", wide: true,
      body: html`<ul class="row-list">${list}</ul>${addBody()}`, acts: addActs() });
    wireDrop(document.getElementById("drawer"));
  }
  function addBody() {
    return html`<div class="sub-section"><h3 class="type-heading">Add a harness or zip</h3>${dropZone()}
      <form class="sub-section" data-submit="install">${EK.textInput({ id: "harness-path", label: "Or a harness folder on this computer", mono: true, placeholder: "C:\\…\\my-harness" })}
      <div>${EK.button("Install", { kind: "secondary", type: "submit" })}</div></form></div>`;
  }
  function addActs() {
    return {
      "pick-zip": function () { document.getElementById("zip-file").click(); },
      install: function () {
        var path = document.getElementById("harness-path").value.trim();
        if (!path) return;
        EK.post("/api/harnesses", { path: path }).then(function (h) { EK.closeDrawer(); installed(h); }).catch(EK.fail);
      }
    };
  }
  function addDrawer() {
    EK.openDrawer({ title: "Add a harness or zip", sub: "A study zip opens as a study", wide: true, body: addBody(), acts: addActs() });
    wireDrop(document.getElementById("drawer"));
  }
  function compareDrawer() {
    var ran = data.studies.filter(function (s) { return HAS_RUN[s.kind]; });
    if (ran.length < 2) {
      EK.openDrawer({ title: "Compare two studies", body: html`<p class="type-body">Two studies need a run each first.</p>` });
      return;
    }
    var options = function (sel) {
      return ran.map(function (s, i) { return html`<option value="${s.slug}"${EK.attrs({ selected: i === sel })}>${s.name}</option>`; });
    };
    EK.openDrawer({ title: "Compare two studies", sub: "Side by side, case by case",
      body: html`<form class="sub-section" data-submit="go"><div class="field"><label class="field-label" for="cmp-a">A</label><select class="input" id="cmp-a">${options(0)}</select></div>
        <div class="field"><label class="field-label" for="cmp-b">B</label><select class="input" id="cmp-b">${options(1)}</select></div>
        <div>${EK.button("Compare", { type: "submit" })}</div></form>`,
      acts: { go: function () {
        var a = document.getElementById("cmp-a").value, b = document.getElementById("cmp-b").value;
        if (a === b) { EK.toast("error", "Pick two different studies"); return; }
        EK.closeDrawer({ quiet: true });
        EK.go("#/compare?a=" + EK.q(a) + "&b=" + EK.q(b));
      } } });
  }

  EK.pages.home = { render: render };
})();
