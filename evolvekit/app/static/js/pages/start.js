/* evolvekit app: Start a study — what do you want to find out?
   1 Application (from a harness, found on this computer), 2 Kind of study
   (the harness's templates), 3 Cases (drop them; some are held back for the
   final check), 4 Name. The study is created on the first thing that needs
   it; then the page lives at #/start/<slug>. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  var st = null;

  function fresh() {
    return { slug: "", doc: null, harness: null, harnesses: [], pick: "", template: null, name: "", named: false,
      found: null, finding: false, reading: null, refused: null };
  }

  function render(main, slug, query, gen) {
    st = fresh();
    st.gen = gen;
    if (slug) {
      return Promise.all([EK.get(EK.studyPath(slug)), EK.get(EK.studyPath(slug, "/harness"))]).then(function (r) {
        if (!EK.isCurrent(gen)) return;
        st.slug = slug; st.doc = r[0]; st.harness = r[1];
        st.template = r[0].study.template || "";
        st.name = r[0].study.name;
        st.named = true;
        if (r[0].state && (r[0].state.kind === "running" || r[0].state.kind === "queued")) { location.replace(EK.studyHref(slug)); return; }
        draw(main);
        if (!r[0].study.application.path) find(main);
        inspectIfNeeded(main);
        EK.companion.show({ phase: "egg", slug: slug, say: "Add the cases, then set up the study." });
      });
    }
    return EK.get("/api/home").then(function (home) {
      if (!EK.isCurrent(gen)) return;
      st.harnesses = (home.harnesses || []).filter(function (h) { return !h.problem; })
        .sort(function (a, b) { return (/demo/i.test(a.id) ? 1 : 0) - (/demo/i.test(b.id) ? 1 : 0); });
      st.pick = query.harness && st.harnesses.some(function (h) { return h.id === query.harness; }) ? query.harness : (st.harnesses[0] || {}).id || "";
      var h = harnessOf(st.pick);
      st.template = h && h.templates && h.templates.length ? h.templates[0].id : "";
      st.name = suggestName();
      draw(main);
      if (st.pick) find(main);
      EK.companion.show({ phase: "egg", say: "Pick what to tune, then drop your cases." });
    });
  }

  function harnessOf(id) { return st.harnesses.filter(function (h) { return h.id === id; })[0]; }
  function appLabel() {
    if (st.harness) return st.harness.application.label || st.harness.title;
    var h = harnessOf(st.pick);
    return h ? (h.application && h.application.label) || h.title : "the application";
  }
  function suggestName() {
    var h = st.harness || harnessOf(st.pick), t = templateOf(st.template);
    return h ? h.title + (t ? " · " + t.title.toLowerCase() : "") : "";
  }
  function templates() {
    if (st.harness) return Object.keys(st.harness.templates || {}).map(function (id) { return Object.assign({ id: id }, st.harness.templates[id]); });
    var h = harnessOf(st.pick);
    return h ? h.templates || [] : [];
  }
  function templateOf(id) { return templates().filter(function (t) { return t.id === id; })[0]; }

  // ------------------------------------------------------------------ finding the application
  function find(main) {
    st.finding = true;
    var q = st.slug ? "study=" + EK.q(st.slug) : "harness=" + EK.q(st.pick);
    var asked = st.pick + st.slug;
    EK.post("/api/detect?" + q).then(function (list) {
      if (asked !== st.pick + st.slug || !EK.isCurrent(st.gen)) return;
      st.finding = false;
      st.found = list || [];
      var ok = st.found.filter(function (x) { return x.ok; })[0];
      if (st.slug && ok && !st.doc.study.application.path) return useApplication(ok.path, main, true);
      draw(main);
    }).catch(function (err) { st.finding = false; st.found = []; draw(main); EK.fail(err); });
  }
  function chosenApp() {
    if (st.doc && st.doc.study.application.path) return { path: st.doc.study.application.path, version: st.doc.study.application.version, ok: true, mine: true };
    return (st.found || []).filter(function (x) { return x.ok; })[0] || null;
  }
  function useApplication(path, main, quiet) {
    return ensureStudy().then(function () {
      return EK.post(EK.studyPath(st.slug, "/application"), { path: path });
    }).then(function (res) {
      st.doc = res.study;
      if (!res.probe.ok) EK.toast("error", res.probe.message || "That is not " + appLabel());
      else if (!quiet) EK.toast("done", appLabel() + " " + (res.probe.version || "") + " will be used");
      draw(main);
      inspectIfNeeded(main);
    }).catch(EK.fail);
  }

  // ------------------------------------------------------------------ the study itself
  function ensureStudy() {
    if (st.slug) return Promise.resolve(st.doc);
    var body = { harness: st.pick, template: st.template || null, name: (st.name || suggestName()).trim() || "New study" };
    return EK.post("/api/studies", body).then(function (doc) {
      st.slug = doc.slug;
      st.doc = doc;
      EK.rememberStudy(doc.slug);
      return EK.get(EK.studyPath(doc.slug, "/harness")).then(function (h) {
        st.harness = h;
        history.replaceState(null, "", location.pathname + location.search + "#/start/" + EK.q(doc.slug));
        EK.route.slug = doc.slug;
        var app = (st.found || []).filter(function (x) { return x.ok; })[0];
        if (app) return EK.post(EK.studyPath(doc.slug, "/application"), { path: app.path }).then(function (res) { st.doc = res.study; return st.doc; });
        return doc;
      });
    });
  }
  function refresh(main) {
    return EK.get(EK.studyPath(st.slug)).then(function (doc) { st.doc = doc; draw(main); return doc; });
  }
  function inspectIfNeeded(main) {
    if (!st.doc || !st.doc.study.application.path) return;
    var cases = st.doc.cases || [];
    if (cases.length && !cases.some(function (c) { return c.inspected; })) {
      EK.post(EK.studyPath(st.slug, "/inspect")).then(function (doc) { st.doc = doc; draw(main); }).catch(function () { /* the cases stay unread; Set up says so */ });
    }
  }
  function firstSplit(main) {
    var d = st.doc, cases = d.cases || [];
    if (cases.length >= 4 && !cases.some(function (c) { return c.set === "test"; }) && d.suggested_test > 0) {
      return EK.post(EK.studyPath(st.slug, "/split"), { test: d.suggested_test }).then(function (doc) { st.doc = doc; draw(main); });
    }
    return Promise.resolve();
  }

  // ------------------------------------------------------------------ cases in
  function addFiles(files, main) {
    var list = Array.prototype.slice.call(files || []);
    if (!list.length) return;
    var formats = ((st.harness || {}).cases || {}).formats || [];
    ensureStudy().then(function () {
      formats = (st.harness.cases || {}).formats || formats;
      var done = 0, refused = [];
      st.reading = { done: 0, total: list.length };
      draw(main);
      return list.reduce(function (p, f) {
        return p.then(function () {
          if (formats.length && !formats.some(function (x) { return f.name.toLowerCase().endsWith(x.toLowerCase()); })) { refused.push(f.name); return; }
          return f.arrayBuffer().then(function (buf) {
            return EK.api("PUT", EK.studyPath(st.slug, "/cases/" + EK.q(f.name)), new Blob([buf])).catch(function (err) { refused.push(f.name + ": " + err.message); });
          }).then(function () { done++; st.reading.done = done; var z = EK.$("#case-drop .progress-fill", main); if (z) z.style.width = Math.round(done / list.length * 100) + "%"; });
        });
      }, Promise.resolve()).then(function () {
        st.reading = null;
        st.refused = refused.length ? { names: refused, formats: formats } : null;
        return refresh(main);
      }).then(function () { return firstSplit(main); }).then(function () { inspectIfNeeded(main); });
    }).catch(function (err) { st.reading = null; draw(main); EK.fail(err); });
  }
  /** Files from a drop, folders walked. */
  function droppedFiles(dt) {
    var items = dt.items ? Array.prototype.slice.call(dt.items) : [];
    var entries = items.map(function (i) { return i.webkitGetAsEntry && i.webkitGetAsEntry(); }).filter(Boolean);
    if (!entries.length) return Promise.resolve(Array.prototype.slice.call(dt.files || []));
    var out = [];
    function walk(entry) {
      if (entry.isFile) return new Promise(function (res) { entry.file(function (f) { out.push(f); res(); }, function () { res(); }); });
      if (!entry.isDirectory) return Promise.resolve();
      var reader = entry.createReader();
      return new Promise(function (res) {
        var all = [];
        (function more() {
          reader.readEntries(function (batch) {
            if (!batch.length) return Promise.all(all.map(walk)).then(res);
            all = all.concat(Array.prototype.slice.call(batch));
            more();
          }, function () { res(); });
        })();
      });
    }
    return Promise.all(entries.map(walk)).then(function () { return out; });
  }

  // ------------------------------------------------------------------ drawing
  function caseItems() {
    EK.caseNames((st.doc ? st.doc.cases : []).map(function (c) { return c.name; }));
    return (st.doc ? st.doc.cases : []).map(function (c) {
      return { name: c.name, short: EK.shortCase(c.name), size: c.bytes || 0, held: c.set === "test", refused: c.inspected && c.inspected.ok === false };
    }).sort(function (a, b) { return a.size - b.size || (a.name < b.name ? -1 : 1); });
  }
  function appBlock() {
    var app = chosenApp(), label = appLabel();
    if (!st.pick && !st.harness) {
      return html`<div class="app-line" role="alert">${EK.icon("warning", 20)}<div class="app-line-words"><span class="app-line-title">No application found</span>
        <span class="type-small ink-2">Add one from a harness zip.</span></div>${EK.link("Add a harness", { href: "#/" })}</div>`;
    }
    var picker = !st.harness && st.harnesses.length > 1 ? html`<div class="field"><label class="field-label" for="pick-harness">Which application</label>
      <select class="input" id="pick-harness" data-change="harness">${st.harnesses.map(function (h) {
        return html`<option value="${h.id}"${EK.attrs({ selected: h.id === st.pick })}>${h.title}</option>`;
      })}</select></div>` : "";
    var line;
    if (st.finding) line = html`<div class="app-line" role="status"><span class="breathe">${EK.gl("folder", 20)}</span><div class="app-line-words"><span class="app-line-title">Looking for ${label}…</span>
      <span class="type-small ink-2">On this computer. Up to 10 s.</span></div></div>`;
    else if (app) line = html`<div class="app-line">${EK.icon("good", 20)}<div class="app-line-words"><span class="app-line-title">${(st.harness || harnessOf(st.pick) || {}).title || label} ${app.version || ""}</span>
      <span class="type-small ink-2"${EK.attrs({ title: app.path })}>${app.mine ? "Ready on this computer" : "Found on this computer"}</span></div>${EK.button("Other", { kind: "quiet", size: "s", act: "other-app" })}</div>`;
    else {
      var near = (st.found || []).filter(function (x) { return x.version; })[0] || (st.found || [])[0];
      // One found with another version says what it found and where, never "not found" (persona p5, J3):
      // "PyVRP 0.13.4 found in C:\…\python.exe; this harness needs PyVRP 0.14.x. Point to the Python that has it."
      var other = near && near.version && !near.ok, title = (st.harness || harnessOf(st.pick) || {}).title || label;
      var python = ((st.harness || {}).application || {}).kind === "python";
      line = html`<div class="app-line" role="alert">${EK.icon("warning", 20)}<div class="app-line-words"><span class="app-line-title">${other ? "Another version of " + title + " found" : label + " not found"}</span>
      <span class="type-small ink-2">${near ? near.message + ". " : ""}${other ? (python ? "Point to the Python that has it." : "Point to the one it needs.") : "Point to it on this computer."}</span></div>${EK.button("Find it", { kind: "secondary", size: "s", act: "other-app" })}</div>`;
    }
    return html`${picker}${line}`;
  }
  function kindBlock() {
    var t = templates();
    var opts = t.map(function (x) { return { value: x.id, title: x.title, sub: x.summary || (x.kind === "data" ? "Change what goes into each case." : "Tune the program’s settings.") }; });
    opts.push({ value: "", title: "Blank", sub: "Choose everything yourself." });
    if (st.slug) opts = opts.map(function (o) { return Object.assign(o, { disabled: o.value !== (st.template || "") }); });
    return html`${EK.kindChoice("kind", "Kind of study", opts, st.template || "", "template")}
      ${st.slug ? html`<p class="type-small ink-2">A study keeps its kind. Start a new one for another.</p>` : ""}`;
  }
  function casesBlock() {
    var d = st.doc, cases = d ? d.cases : [], held = cases.filter(function (c) { return c.set === "test"; }).length;
    var label = ((st.harness || {}).cases || {}).label || "cases";
    var formats = (((st.harness || harnessOf(st.pick) || {}).cases) || {}).formats || [];
    var zone;
    if (st.reading) zone = EK.dropZone({ id: "case-drop", state: "reading", title: "Reading " + st.reading.done + " of " + st.reading.total + " cases…", frac: st.reading.done / Math.max(1, st.reading.total), label: "Cases" });
    else if (st.refused && !cases.length) zone = EK.dropZone({ id: "case-drop", state: "refused", title: "That file is not a case.", sub: st.refused.names[0] + (formats.length ? " · " + appLabel() + " reads " + formats.join(", ") + " cases" : ""), label: "Cases", accept: formats.join(","), multiple: true, change: "files" });
    else if (cases.length) zone = EK.dropZone({ id: "case-drop", state: "read", title: EK.plural(cases.length, "case") + " read", sub: html`Drop more, or ${EK.link("choose again", { act: "choose-files" })}`, label: "Cases", accept: formats.join(","), multiple: true, change: "files" });
    else zone = EK.dropZone({ id: "case-drop", title: "Drop the folder of " + label.toLowerCase(), sub: html`Or ${EK.link("choose files", { act: "choose-files" })} · ${EK.link("choose a folder", { act: "choose-folder" })}`, label: "Cases", accept: formats.join(","), multiple: true, change: "files" });
    var out = [zone];
    if (st.refused && cases.length) out.push(EK.notice("error", st.refused.names.length === 1 ? st.refused.names[0] + " was left out." : st.refused.names.length + " files were left out.", { text: "Show them", act: "show-refused" }));
    var unread = cases.filter(function (c) { return c.inspected && c.inspected.ok === false; });
    if (unread.length) out.push(EK.notice("error", unread[0].name + " cannot be read" + (unread.length > 1 ? ", and " + (unread.length - 1) + " more" : "") + ".", { text: "Show it", act: "which" }));
    if (cases.length >= 2) out.push(EK.caseStrip(caseItems(), { width: EK.isPhone() ? 358 : 760, phone: EK.isPhone(), sized: true }));
    if (cases.length) {
      var warn = cases.length < 3 || held < 3;
      var words = cases.length < 3 ? "Too few cases: none can be held back." : d.split_words;
      out.push(html`<div class="held-line">${warn ? EK.icon("warning", 18) : ""}<span>${words}</span>
        ${cases.length < 3 ? EK.button("Add cases", { kind: "secondary", size: "s", act: "choose-files" })
          : held < 3 && d.suggested_test > held ? EK.button("Hold back " + d.suggested_test, { kind: "secondary", size: "s", act: "hold", arg: d.suggested_test }) : ""}
        ${EK.link("Which ones?", { act: "which" })}</div>
        <p class="type-small ink-2">Held back: kept aside, never searched on. The final check tries the result on them.</p>`);
    } else {
      out.push(html`<p class="type-small ink-2">${EK.link("Use the sample cases", { act: "samples" })} · ${EK.link("A folder on this computer", { act: "folder-path" })}</p>`);
    }
    return EK.join(out);
  }
  function blockers() {
    if (!st.doc) return [!chosenApp() ? "Add an application first." : "Add the cases first."];
    var p = (st.doc.problems || []).filter(function (x) { return x.step <= 3; }).map(function (x) { return x.text; });
    if (!st.doc.study.application.path) p.unshift("Add an application first.");
    if ((st.doc.cases || []).length < 2) p.unshift("Add more cases first.");
    return p;
  }
  function draw(main) {
    var block = blockers(), cases = st.doc ? st.doc.cases.length : 0;
    var sizes = st.doc ? (st.doc.cases || []).map(function (c) { return c.bytes || 0; }) : [];
    var aside = cases >= 2 ? html`<span class="type-small ink-2">${EK.plural(cases, "case")}${sizes.length ? " · " + fmtBytes(Math.min.apply(null, sizes)) + " to " + fmtBytes(Math.max.apply(null, sizes)) : ""}</span>` : "";
    var body = html`${EK.titleBlock("Start a study", "What do you want to find out?")}
      <div class="start-form">
        ${EK.fieldRow(1, "Application", appBlock())}
        ${EK.fieldRow(2, "Kind of study", kindBlock())}
        ${EK.fieldRow(3, "Cases", casesBlock(), aside)}
        ${EK.fieldRow(4, "Name", EK.textInput({ id: "study-name", label: "Name", value: st.name, placeholder: "Name your study", change: "name", input: "typing" }))}
        <div class="after-rows actions">${EK.button("Next: set up", { act: "next", disabled: block.length > 0 })}
          ${block.length ? html`<span class="type-small ink-2">${block[0]}</span>` : st.slug ? html`<span class="type-small ink-2">Saved as you go.</span>` : ""}</div>
      </div>`;
    var focused = document.activeElement && document.activeElement.id;
    EK.mount(main, html`<div class="page"><div class="page-inner">${body}</div>
      <input type="file" class="sr-only" id="pick-files" multiple tabindex="-1" aria-hidden="true" data-change="files"></div>`, acts(main));
    if (focused && document.getElementById(focused)) document.getElementById(focused).focus();
    var zone = document.getElementById("case-drop");
    if (zone) {
      zone.addEventListener("dragover", function (ev) { ev.preventDefault(); zone.classList.add("is-dragging"); });
      zone.addEventListener("dragleave", function () { zone.classList.remove("is-dragging"); });
      zone.addEventListener("drop", function (ev) {
        ev.preventDefault();
        zone.classList.remove("is-dragging");
        droppedFiles(ev.dataTransfer).then(function (files) { addFiles(files, main); });
      });
    }
  }
  function fmtBytes(b) { return b >= 1048576 ? EK.num(b / 1048576, 1) + " MB" : b >= 1024 ? Math.round(b / 1024) + " KB" : b + " B"; }

  function acts(main) {
    return {
      harness: function (el) {
        st.pick = el.value;
        var h = harnessOf(st.pick);
        st.template = h && h.templates && h.templates.length ? h.templates[0].id : "";
        if (!st.named) st.name = suggestName();
        st.found = null;
        draw(main);
        find(main);
      },
      template: function (el) {
        if (st.slug) return;
        st.template = el.value;
        if (!st.named) st.name = suggestName();
        draw(main);
      },
      typing: function () { st.named = true; },
      name: function (el) {
        st.name = el.value.trim();
        st.named = true;
        if (st.slug && st.name) EK.patch(EK.studyPath(st.slug), { name: st.name }).then(function (doc) { st.doc = doc; }).catch(EK.fail);
      },
      "choose-files": function () { document.getElementById("pick-files").click(); },
      files: function (el) { var f = el.files; addFiles(f, main); el.value = ""; },
      "choose-folder": function () {
        ensureStudy().then(function () {
          return EK.post("/api/pick-file", { kind: "folder", title: "The folder of cases" });
        }).then(function (r) {
          if (!r || r.cancelled || !r.path) return;
          return importFolder(r.path, main);
        }).catch(function (err) {
          if (err.status === 501) { folderPathDrawer(main); return; }
          EK.fail(err);
        });
      },
      "folder-path": function () { folderPathDrawer(main); },
      samples: function () {
        ensureStudy().then(function () { return EK.post(EK.studyPath(st.slug, "/samples")); })
          .then(function (doc) { st.doc = doc; draw(main); return firstSplit(main); }).then(function () { inspectIfNeeded(main); }).catch(EK.fail);
      },
      hold: function (el) {
        EK.post(EK.studyPath(st.slug, "/split"), { test: Number(el.getAttribute("data-arg")) }).then(function (doc) { st.doc = doc; draw(main); }).catch(EK.fail);
      },
      which: function () { casesDrawer(main); },
      "show-refused": function () {
        EK.openDrawer({ title: "Left out", body: html`<ul class="row-list">${st.refused.names.map(function (n) { return html`<li><span class="mono">${n}</span></li>`; })}</ul>
          <p class="type-small ink-2">${appLabel()} reads ${(st.refused.formats || []).join(", ")} files.</p>` });
      },
      "other-app": function () { appDrawer(main); },
      next: function () {
        return ensureStudy().then(function () {
          var d = st.doc;
          var smallest = (d.cases || []).filter(function (c) { return c.set === "training"; })[0];
          var stale = !d.preview || d.preview.state === "none" || d.preview.state === "interrupted" ||
            (d.preview.state === "failed") || (smallest && d.preview.result && d.preview.result.case && d.preview.result.case.indexOf(smallest.name.replace(/\.[^.]+$/, "")) < 0);
          var go = function () { EK.go("#/setup/" + EK.q(st.slug)); };
          var step = (d.study.step || 1) < 4 ? EK.patch(EK.studyPath(st.slug), { step: 4 }) : Promise.resolve();
          return step.then(function () {
            if (stale && d.preview.state !== "running") EK.post(EK.studyPath(st.slug, "/preview")).catch(function () { /* Set up shows the preview's state */ });
            go();
          });
        }).catch(EK.fail);
      }
    };
  }

  function importFolder(path, main) {
    return EK.post(EK.studyPath(st.slug, "/import"), { folder: path }).then(function (res) {
      st.doc = res.study;
      var added = res.imported.added.length, skipped = res.imported.skipped.length;
      EK.toast(added ? "done" : "error", EK.plural(added, "case") + " added" + (skipped ? ", " + skipped + " left out" : ""));
      if (skipped) st.refused = { names: res.imported.skipped, formats: (st.harness.cases || {}).formats || [] };
      draw(main);
      return firstSplit(main);
    }).then(function () { inspectIfNeeded(main); });
  }
  function folderPathDrawer(main) {
    EK.openDrawer({ title: "A folder of cases", sub: "On this computer",
      body: html`<form class="sub-section" data-submit="go">${EK.textInput({ id: "folder-path", label: "Folder", mono: true, placeholder: "C:\\…\\cases" })}
        <div>${EK.button("Add the cases in it", { type: "submit" })}</div><p class="type-small tone-critical" id="folder-say" role="status" style="overflow-wrap:anywhere"></p></form>`,
      acts: { go: function () {
        var box = document.getElementById("folder-path"), path = box.value.trim().replace(/^"|"$/g, "");
        if (!path) return;
        var say = document.getElementById("folder-say");
        say.textContent = "Looking in the folder…";
        ensureStudy().then(function () {
          return EK.post(EK.studyPath(st.slug, "/import"), { folder: path });
        }).then(function (res) {
          EK.closeDrawer();
          st.doc = res.study;
          var added = res.imported.added.length, skipped = res.imported.skipped.length;
          EK.toast(added ? "done" : "error", EK.plural(added, "case") + " added" + (skipped ? ", " + skipped + " left out" : ""));
          if (skipped) st.refused = { names: res.imported.skipped, formats: (st.harness.cases || {}).formats || [] };
          draw(main);
          return firstSplit(main).then(function () { inspectIfNeeded(main); });
        }).catch(function (err) { say.textContent = err.message; box.focus(); });
      } } });
    setTimeout(function () { var box = document.getElementById("folder-path"); if (box) box.focus(); }, 260);
  }

  function appDrawer(main) {
    var label = appLabel();
    var list = (st.found || []).map(function (x) {
      return html`<li><div class="row-main"><span class="mono">${x.path}</span><span class="type-small ${x.ok ? "ink-2" : "tone-critical"}">${x.ok ? (x.version || "") + " · works" : x.message}</span></div>
        ${x.ok ? EK.button("Use this", { kind: "secondary", size: "s", act: "use", arg: x.path }) : ""}</li>`;
    });
    var help = st.harness ? st.harness.application.help : "";
    EK.openDrawer({ title: "Which " + label + "?", sub: "The one this study runs", wide: true,
      body: html`${list.length ? html`<ul class="row-list">${list}</ul>` : html`<p class="type-body ink-2">${st.finding ? "Looking…" : "None found on this computer."}</p>`}
        <div class="actions">${EK.button("Look again", { kind: "secondary", size: "s", act: "again" })}${EK.button("Browse…", { kind: "secondary", size: "s", act: "browse" })}</div>
        <form class="sub-section" data-submit="path">${EK.textInput({ id: "app-path", label: "Or paste its path", mono: true, placeholder: "C:\\…\\python.exe" })}
          <div>${EK.button("Check this path", { type: "submit", kind: "secondary" })}</div></form>
        ${help ? html`<details class="more-detail"><summary>Where do I find it?</summary><p class="type-small">${help}</p></details>` : ""}`,
      acts: {
        use: function (el) { EK.closeDrawer(); useApplication(el.getAttribute("data-arg"), main); },
        again: function () { EK.closeDrawer({ quiet: true }); st.found = null; find(main); },
        browse: function () {
          EK.post("/api/pick-file", { kind: "file", title: "Where is " + label + "?" }).then(function (r) {
            if (r && r.path) { EK.closeDrawer(); useApplication(r.path, main); }
          }).catch(EK.fail);
        },
        path: function () {
          var p = document.getElementById("app-path").value.trim().replace(/^"|"$/g, "");
          if (p) { EK.closeDrawer(); useApplication(p, main); }
        }
      } });
  }

  function casesDrawer(main) {
    function body() {
      var cases = st.doc.cases || [], held = cases.filter(function (c) { return c.set === "test"; }).length;
      var rows = cases.map(function (c) {
        var read = c.inspected ? (c.inspected.ok === false ? html`<span class="tone-critical">${c.inspected.error || "cannot be read"}</span>` : c.inspected.summary || "") : html`<span class="ink-3">not read yet</span>`;
        // The kind of case: typed in, saved on Enter or on leaving the field; the group of groups.json beside it.
        var tag = { act: "tag", arg: c.name };
        var kind = html`<input${EK.attrs({ type: "text", class: "input case-tag", value: c.tag || "", maxlength: 40, autocomplete: "off",
          placeholder: "e.g. big", "aria-label": "Kind of case " + c.name, "data-change": tag.act, "data-key": tag.act, "data-arg": tag.arg })}>
          <span class="type-small ink-2" data-group-of="${c.name}">${c.group || ""}</span>`;
        return html`<tr><td class="mono">${c.name}</td><td>${read}</td><td>${kind}</td><td>${c.set === "test" ? "held back" : "learns from"}</td>
          <td>${EK.button(c.set === "test" ? "Learn from it" : "Hold back", { kind: "quiet", size: "s", act: "flip", arg: c.name })}
          ${EK.button("Remove", { kind: "quiet", size: "s", act: "remove", arg: c.name, label: "Remove " + c.name })}</td></tr>`;
      });
      return html`<p class="type-small ink-2">${st.doc.split_words}</p>
        <form class="range-edit" data-submit="count">${EK.numberUnit({ id: "held-count", label: "Held back", value: held, unit: "of " + cases.length, width: 160 })}
          ${EK.button("Hold these back", { kind: "secondary", type: "submit" })}</form>
        <div class="cases-wrap"><table class="case-table"><thead><tr><th>Case</th><th>What it is</th><th>Kind</th><th>Used for</th><th><span class="sr-only">Actions</span></th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }
    var dacts = {
      count: function () {
        var n = Math.round(EK.readNumber(document.getElementById("held-count").value));
        if (!(n >= 0)) return;
        EK.post(EK.studyPath(st.slug, "/split"), { test: n }).then(function (doc) { st.doc = doc; draw(main); EK.refreshDrawer({ body: body() }); }).catch(EK.fail);
      },
      flip: function (el) {
        var name = el.getAttribute("data-arg"), c = st.doc.study.cases;
        var path = "cases/" + name, inTest = c.test.indexOf(path) >= 0;
        var training = inTest ? c.training.concat([path]) : c.training.filter(function (p) { return p !== path; });
        var test = inTest ? c.test.filter(function (p) { return p !== path; }) : c.test.concat([path]);
        EK.patch(EK.studyPath(st.slug), { cases: { training: training, test: test } }).then(function (doc) { st.doc = doc; draw(main); EK.refreshDrawer({ body: body() }); }).catch(EK.fail);
      },
      tag: function (el, ev) {
        if (ev.type === "keydown") {  // Enter leaves the field, which saves it
          if (ev.key === "Enter") { ev.preventDefault(); el.blur(); }
          return;
        }
        var name = el.getAttribute("data-arg"), value = el.value.trim();
        return EK.put(EK.studyPath(st.slug, "/cases/" + EK.q(name) + "/tag"), { tag: value }).then(function (doc) {
          st.doc = doc; draw(main);
          el.value = value;
          // Only the groups beside the fields change: redrawing the table would take the next field from under the cursor.
          var rows = {};
          (doc.cases || []).forEach(function (c) { rows[c.name] = c; });
          Array.prototype.forEach.call(document.querySelectorAll("#drawer [data-group-of]"), function (g) {
            var c = rows[g.getAttribute("data-group-of")];
            g.textContent = c && c.group ? c.group : "";
          });
        }).catch(function (err) {
          // Not saved: the field shows the tag the case still has, and the toast says why.
          var kept = (st.doc.cases || []).filter(function (c) { return c.name === name; })[0];
          el.value = kept && kept.tag ? kept.tag : "";
          EK.fail(err);
        });
      },
      remove: function (el) {
        var name = el.getAttribute("data-arg");
        EK.del(EK.studyPath(st.slug, "/cases/" + EK.q(name))).then(function (doc) {
          st.doc = doc; draw(main); EK.refreshDrawer({ body: body() });
          EK.undo("Case " + name + " removed. " + EK.plural(doc.cases.length, "case") + " left.", function () {
            return EK.post(EK.studyPath(st.slug, "/cases/" + EK.q(name) + "/restore")).then(function (d2) { st.doc = d2; draw(main); if (EK.drawerKind() === "cases") EK.refreshDrawer({ body: body() }); });
          });
        }).catch(EK.fail);
      }
    };
    EK.openDrawer({ kind: "cases", title: "Cases", sub: "Held back ones wait for the final check", wide: true, body: body(), acts: dacts });
  }

  EK.pages.start = { render: render };
})();
