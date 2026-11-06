/* evolvekit app: the drawer on the right (Set up's drawers, Home's one-click
   sections, Ask Pepper), and Pepper the floating companion.
   - EK.openDrawer({title, sub, body, foot, wide, dims, acts, onClose, figure})
     docks a drawer on the right (full screen on a phone); Escape or Close
     ends it and focus returns where it was.
   - EK.askPepper() opens Ask Pepper for the study on screen.
   - EK.companion: Pepper over the page. Click = an update bubble; right-click,
     Shift+F10, the context-menu key or a long press = its menu. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;

  // ------------------------------------------------------------------ the drawer
  var open = null;
  EK.openDrawer = function (o) {
    var el = document.getElementById("drawer");
    if (open && open.onClose) open.onClose();
    var opener = open ? open.opener : document.activeElement;
    open = { onClose: o.onClose, opener: opener, kind: o.kind || "", dims: !!o.dims };
    el.className = "drawer" + (o.wide ? " is-wide" : "");
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", EK.isPhone() ? "true" : "false");
    var acts = Object.assign({ "drawer-close": function () { EK.closeDrawer(); } }, o.acts || {});
    EK.mount(el, EK.drawer(o), acts);
    el.hidden = false;
    document.body.classList.add("drawer-open");
    document.body.classList.toggle("drawer-dims", !!o.dims);
    var title = document.getElementById("drawer-title");
    if (o.focus !== false && title) title.focus();
    return el;
  };
  /** Redraw the open drawer's body (and foot) without moving focus. */
  EK.refreshDrawer = function (o) {
    if (!open) return;
    var el = document.getElementById("drawer");
    var body = el.querySelector(".drawer-body"), foot = el.querySelector(".drawer-foot");
    if (o.body != null && body) body.innerHTML = String(o.body);
    if (o.foot != null && foot) foot.innerHTML = String(o.foot);
    if (o.acts) el._acts = Object.assign({ "drawer-close": function () { EK.closeDrawer(); } }, o.acts);
  };
  EK.drawerKind = function () { return open ? open.kind : ""; };
  EK.closeDrawer = function (opts) {
    opts = opts || {};
    if (!open) return;
    if (opts.keepAssistant && open.kind === "assistant") {
      // Ask Pepper stays docked across pages; it follows the study on screen.
      EK.later(0, function () { if (open && open.kind === "assistant") EK.askPepper({ focus: false }); });
      return;
    }
    var was = open;
    open = null;
    var el = document.getElementById("drawer");
    el.hidden = true;
    el.innerHTML = "";
    document.body.classList.remove("drawer-open", "drawer-dims");
    if (was.onClose) was.onClose();
    if (!opts.quiet && was.opener && document.contains(was.opener)) was.opener.focus();
  };
  EK.onEscape = function () {
    if (EK.hoverEscape && EK.hoverEscape()) return true;
    if (open) { EK.closeDrawer(); return true; }
    if (EK.companion && EK.companion.escape()) return true;
    return false;
  };

  /** A small drawer asking for one line of text; resolves with it, or null. */
  EK.askText = function (title, label, value, button) {
    return new Promise(function (resolve) {
      var done = false;
      EK.openDrawer({
        title: title,
        body: html`<form data-submit="ok" class="sub-section">${EK.textInput({ id: "ask-text", label: label, value: value || "" })}
          <div class="actions">${EK.button(button || "Save", { type: "submit" })}${EK.button("Cancel", { kind: "quiet", act: "drawer-close" })}</div></form>`,
        acts: { ok: function () { done = true; var v = document.getElementById("ask-text").value.trim(); EK.closeDrawer(); resolve(v || null); } },
        onClose: function () { if (!done) resolve(null); }
      });
      var input = document.getElementById("ask-text");
      if (input) { input.focus(); input.select(); }
    });
  };

  /** A drawer that asks to confirm something that cannot be undone. Resolves true or false. */
  EK.confirm = function (title, sentence, yes) {
    return new Promise(function (resolve) {
      var done = false;
      EK.openDrawer({
        title: title,
        body: html`<p class="type-body">${sentence}</p><div class="actions">${EK.button(yes, { act: "yes" })}${EK.button("Cancel", { kind: "quiet", act: "drawer-close" })}</div>`,
        acts: { yes: function () { done = true; EK.closeDrawer(); resolve(true); } },
        onClose: function () { if (!done) resolve(false); }
      });
    });
  };

  // ------------------------------------------------------------------ Ask Pepper
  var KIND_WORD = { setting: "What to tune", data_change: "Data change", constraint: "Rule", kpi: "Measure", weighted: "Measure",
    goal: "Goal", guardrail: "Rule", unknown: "Note" };
  var ask = { slug: "", turns: [], thinking: null, total: null, requests: null, unknown: false, editing: {}, poll: null, needsKey: false };

  function screenName() {
    var r = EK.route || {};
    return r.page || "home";
  }
  function cardKey(turn, i) { return turn + ":" + i; }
  function cardTitle(c) {
    return c.says || c.name || (c.kind === "goal" ? "A new goal" : c.kind === "guardrail" ? "A limit on " + (c.kpi || "a measure") : KIND_WORD[c.kind] || "A change");
  }
  function cardDetail(c) {
    if (c.kind === "setting") return (c.name || "") + ": " + (c.mode || "tune") + (c.low != null ? " between " + c.low + " and " + c.high : "") + (c.value != null ? " = " + c.value : "");
    if (c.kind === "data_change") return (c.lever || "") + (c.column ? " · " + c.column : "") + (c.low != null ? " · " + c.low + " to " + c.high : "");
    if (c.kind === "constraint") return c.expr || "";
    if (c.kind === "kpi") return (c.direction === "higher" ? "Higher is better" : "Lower is better") + (c.unit ? " · " + c.unit : "");
    if (c.kind === "weighted") return Object.keys(c.weighted || {}).map(function (k) { return k + " × " + c.weighted[k]; }).join(" + ");
    if (c.kind === "goal") return (c.levels || []).map(function (l) { return l.kpi + ", " + l.direction; }).join("; then ");
    if (c.kind === "guardrail") return (c.kpi || "") + (c.max != null ? " at most " + c.max : "") + (c.min != null ? " at least " + c.min : "");
    return "";
  }
  var EDIT_SKIP = { kind: 1, ok: 1, problem: 1, state: 1, value_today: 1, note: 1, rows: 1, applies_to: 1, table: 1, holds_today: 1, index: 1 };
  function editor(c, key) {
    var fields = Object.keys(c).filter(function (k) { return !EDIT_SKIP[k] && k !== "value"; });
    if (c.kind === "setting" && c.mode === "fixed") fields.push("value");
    return html`<div class="sub-section" data-editor="${key}">${fields.map(function (k) {
      var v = c[k], complex = v !== null && typeof v === "object";
      return EK.textInput({ id: "ed-" + key.replace(/[^0-9a-z]/gi, "-") + "-" + k, label: k.replace(/_/g, " "), value: complex ? JSON.stringify(v) : v == null ? "" : String(v),
        mono: complex || /sql|expr/.test(k), multiline: /sql/.test(k) });
    })}</div>`;
  }
  function readEditor(c, key) {
    var out = { kind: c.kind };
    Object.keys(c).forEach(function (k) {
      if (EDIT_SKIP[k]) return;
      var el = document.getElementById("ed-" + key.replace(/[^0-9a-z]/gi, "-") + "-" + k);
      if (!el) { if (k !== "value") out[k] = c[k]; return; }
      var text = el.value.trim(), old = c[k];
      if (old !== null && typeof old === "object") { try { out[k] = JSON.parse(text); } catch (e) { out[k] = old; } }
      else if (typeof old === "number") { var n = EK.readNumber(text); out[k] = n == null ? old : n; }
      else if (typeof old === "boolean") out[k] = /^(true|on|yes|1)$/i.test(text);
      else out[k] = text;
    });
    return out;
  }

  function costLine() {
    if (ask.requests != null) return EK.plural(Math.round(ask.requests * 100) / 100, "Copilot request") + " so far";
    if (ask.total != null) return "Cost so far: $" + EK.num(ask.total, 2) + (ask.unknown ? ", some not known" : "");
    return ask.unknown ? "Cost so far: not known" : "";
  }

  function chatBody() {
    if (!ask.slug) {
      return html`${EK.chatBubble("Open a study first. I answer about the study on screen.")}<p>${EK.link("Your studies", { href: "#/" })}</p>`;
    }
    var out = [];
    if (!ask.turns.length && !ask.thinking) out.push(EK.chatBubble("Ask me about this study, in your own words. I can also propose changes."));
    var answers = {};
    ask.turns.forEach(function (t) { if (t.role === "assistant") answers[t.turn] = t; });
    var questions = ask.turns.filter(function (t) { return t.role === "user"; }).slice(-6);
    questions.forEach(function (q) {
      out.push(EK.chatBubble(q.text, true));
      var a = answers[q.id];
      if (!a) {
        if (ask.thinking === q.id) out.push(EK.thinkingRow());
        return;
      }
      if (a.cancelled) { out.push(html`<p class="type-small ink-2">Cancelled.</p>`); return; }
      if (a.needs_key) {
        out.push(EK.chatBubble(a.say || "I need an AI service first."));
        out.push(html`<p>${EK.link("Open Settings", { href: "#/settings" })}</p>`);
        return;
      }
      if (a.failed) {
        out.push(html`<div class="thinking"><span class="type-small ink-2">${a.say || "No answer this time."}</span>${EK.button("Ask again", { kind: "secondary", size: "s", act: "ask-again", arg: q.text })}</div>`);
        return;
      }
      if (a.say) out.push(EK.chatBubble(a.say));
      var usable = (a.cards || []).filter(function (c) { return (c.state || "proposed") === "proposed" && c.ok !== false; }).length;
      if ((a.cards || []).length) {
        out.push(html`<div class="actions" style="justify-content:space-between"><span class="type-small ink-2">${EK.plural(a.cards.length, "proposal")}</span>
          ${usable >= 2 ? EK.button("Apply all", { kind: "secondary", size: "s", act: "card-apply-all", arg: a.turn, label: "Apply all " + usable }) : ""}</div>`);
      }
      (a.cards || []).forEach(function (c, i) {
        var key = cardKey(a.turn, i), state = c.state || "proposed";
        if (ask.editing[key]) state = "edited";
        out.push(EK.proposalCard({ key: key, kindWord: KIND_WORD[c.kind] || "Note", title: cardTitle(c), detail: cardDetail(c), state: state,
          check: c.problem || c.note || (c.value != null ? "Today: " + c.value : ""), problem: c.ok === false ? c.problem : null, editor: state === "edited" ? editor(c, key) : null }));
      });
    });
    if (ask.thinking && !questions.some(function (q) { return q.id === ask.thinking; })) out.push(EK.thinkingRow());
    return EK.join(out);
  }
  function composer() {
    if (!ask.slug) return "";
    return html`<form class="composer" data-submit="ask">${EK.textInput({ id: "ask-input", label: "Your question", placeholder: "Ask in your own words", multiline: true, rows: 1 })}
      ${EK.button("Ask", { type: "submit", disabled: !!ask.thinking })}</form><span class="type-small ink-2">${costLine()}</span>`;
  }
  function redraw() {
    if (EK.drawerKind() !== "assistant") return;
    EK.refreshDrawer({ body: chatBody(), foot: composer(), acts: acts });
    var body = EK.$("#drawer .drawer-body");
    if (body) body.scrollTop = body.scrollHeight;
    var input = document.getElementById("ask-input");
    if (input) input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey || !ev.shiftKey)) { ev.preventDefault(); input.form.requestSubmit(); }
    });
  }
  function findCard(key) {
    var parts = key.split(":"), turn = parts[0], i = Number(parts[1]);
    var a = ask.turns.filter(function (t) { return t.role === "assistant" && String(t.turn) === turn; })[0];
    return a ? { answer: a, card: (a.cards || [])[i], index: i, turn: a.turn } : null;
  }
  function load() {
    if (!ask.slug) { redraw(); return Promise.resolve(); }
    return EK.get(EK.studyPath(ask.slug, "/assistant")).then(function (h) {
      ask.turns = h.turns || [];
      ask.total = h.total_usd == null ? null : h.total_usd;
      ask.requests = h.total_requests == null ? null : h.total_requests;
      ask.unknown = !!h.unknown;
      ask.thinking = h.thinking || null;
      redraw();
      if (ask.thinking) pollTurn(ask.thinking);
    }).catch(EK.fail);
  }
  function pollTurn(turn) {
    clearTimeout(ask.poll);
    ask.poll = setTimeout(function () {
      if (!ask.slug) return;
      EK.get(EK.studyPath(ask.slug, "/assistant?turn=" + EK.q(turn))).then(function (s) {
        if (s.state === "thinking") return pollTurn(turn);
        ask.thinking = null;
        load();
        if (EK.companion) EK.companion.answered(s);
      }).catch(function (err) { ask.thinking = null; redraw(); EK.fail(err); });
    }, 1500);
  }
  var acts = {
    ask: function () {
      var input = document.getElementById("ask-input"), text = input ? input.value.trim() : "";
      if (!text || ask.thinking) return;
      var r = EK.route || {};
      EK.post(EK.studyPath(ask.slug, "/assistant"), { message: text, screen: screenName(), run: r.query && r.query.run || null }).then(function (res) {
        ask.thinking = res.turn;
        ask.turns.push({ role: "user", id: res.turn, text: text });
        redraw();
        pollTurn(res.turn);
      }).catch(EK.fail);
    },
    "ask-again": function (el) {
      var input = document.getElementById("ask-input");
      if (input) { input.value = el.getAttribute("data-arg"); acts.ask(); }
    },
    "ask-cancel": function () {
      EK.post(EK.studyPath(ask.slug, "/assistant/cancel")).then(load).catch(EK.fail);
    },
    "card-edit": function (el) {
      var key = el.getAttribute("data-arg");
      ask.editing[key] = !ask.editing[key];
      redraw();
    },
    "card-dismiss": function (el) {
      var f = findCard(el.getAttribute("data-arg"));
      if (!f) return;
      EK.post(EK.studyPath(ask.slug, "/assistant/cards"), { turn: f.turn, index: f.index, state: "dismissed" }).then(load).catch(EK.fail);
    },
    "card-show": function (el) {
      var f = findCard(el.getAttribute("data-arg"));
      if (!f) return;
      EK.post(EK.studyPath(ask.slug, "/assistant/cards"), { turn: f.turn, index: f.index, state: "proposed" }).then(load).catch(EK.fail);
    },
    "card-apply": function (el) {
      var key = el.getAttribute("data-arg"), f = findCard(key);
      if (!f) return;
      var card = ask.editing[key] ? readEditor(f.card, key) : f.card;
      var clean = {};
      Object.keys(card).forEach(function (k) { if (!EDIT_SKIP[k] || k === "kind") clean[k] = card[k]; });
      EK.post(EK.studyPath(ask.slug, "/cards"), { card: clean }).then(function () {
        delete ask.editing[key];
        return EK.post(EK.studyPath(ask.slug, "/assistant/cards"), { turn: f.turn, index: f.index, state: "applied" });
      }).then(function () {
        EK.toast("done", "Applied: " + cardTitle(f.card));
        load();
        if (EK.onStudyChanged) EK.onStudyChanged();
      }).catch(EK.fail);
    },
    "card-apply-all": function (el) {
      var turn = el.getAttribute("data-arg");
      var a = ask.turns.filter(function (t) { return t.role === "assistant" && String(t.turn) === turn; })[0];
      if (!a) return;
      var cards = [];
      (a.cards || []).forEach(function (c, i) {
        if ((c.state || "proposed") !== "proposed" || c.ok === false) return;
        var clean = { index: i };
        Object.keys(c).forEach(function (k) { if (!EDIT_SKIP[k] || k === "kind") clean[k] = c[k]; });
        cards.push(clean);
      });
      EK.post(EK.studyPath(ask.slug, "/cards"), { cards: cards, turn: a.turn }).then(function (res) {
        var bad = (res.cards || []).filter(function (c) { return !c.ok; }).length;
        EK.toast(bad ? "error" : "done", bad ? bad + " could not be applied" : "Applied " + cards.length + " changes");
        load();
        if (EK.onStudyChanged) EK.onStudyChanged();
      }).catch(EK.fail);
    }
  };
  /** AssistantDrawer: Ask Pepper for the study on screen (or the last one opened). */
  EK.assistantDrawer = EK.askPepper = function (o) {
    o = o || {};
    var r = EK.route || {};
    var slug = r.slug || EK.currentStudy();
    if (slug !== ask.slug) { ask = { slug: slug, turns: [], thinking: null, total: null, requests: null, unknown: false, editing: {}, poll: null }; }
    EK.openDrawer({ kind: "assistant", title: "Ask Pepper", figure: html`<span aria-hidden="true">${EK.pepper(EK.companion ? EK.companion.phase() : "cocoon", 34)}</span>`,
      body: chatBody(), foot: composer() || " ", live: true, acts: acts, focus: o.focus, closeLabel: "Close Ask Pepper",
      onClose: function () { clearTimeout(ask.poll); } });
    redraw();
    load();
    if (o.focus !== false) { var input = document.getElementById("ask-input"); if (input) input.focus(); }
  };

  // ------------------------------------------------------------------ the companion
  var POS = "evolvekit.pepper";
  var pep = { phase: "egg", bubble: null, hidden: false, menu: false, state: null, slug: "", left: false, timer: null };
  function host() { return document.getElementById("companion"); }
  function draw() {
    var el = host();
    if (!el) return;
    if (pep.hidden || !pep.phase) { el.innerHTML = ""; return; }
    var size = EK.isPhone() ? 64 : 92;
    var word = EK.PEPPER_WORDS[pep.phase] || "";
    var bubble = pep.bubble ? html`<div class="pep-bubble" role="status"><span class="type-small">${pep.bubble}</span>
      <span class="actions">${EK.button("Ask Pepper", { kind: "quiet", size: "s", act: "pep-ask" })}${EK.button("Close", { kind: "quiet", size: "s", act: "pep-bubble-close" })}</span></div>` : "";
    var menu = html`<div class="menu pep-menu" role="menu" aria-label="Pepper" id="pep-menu"${EK.attrs({ hidden: !pep.menu })}>
      <button type="button" role="menuitem" class="menu-item strong" data-act="pep-ask">Ask a question…</button>
      <button type="button" role="menuitem" class="menu-item" data-act="pep-now">What’s happening now?</button>
      <div role="separator" class="menu-sep"></div>
      <button type="button" role="menuitem" class="menu-item" data-act="pep-corner">Move to the corner</button>
      <button type="button" role="menuitem" class="menu-item" data-act="pep-hide">Hide until it ends</button></div>`;
    EK.mount(el, html`<div class="companion menu-wrap">${bubble}${menu}
      <button type="button" class="companion-btn" id="pep-btn" aria-haspopup="menu" aria-expanded="${pep.menu ? "true" : "false"}"
        aria-label="Pepper, ${word}. Enter for an update, Shift+F10 for the menu.">
        <span class="${pep.phase === "butterfly" || pep.phase === "caterpillar" ? "float" : ""}" style="display:block">${EK.pepper(pep.phase, size)}</span></button></div>`, compActs);
    el.classList.toggle("is-left", pep.left);
    wire();
  }
  function setMenu(on) {
    pep.menu = on;
    var menu = document.getElementById("pep-menu"), btn = document.getElementById("pep-btn");
    if (!menu || !btn) return;
    menu.hidden = !on;
    btn.setAttribute("aria-expanded", on ? "true" : "false");
    menu._opener = btn;
    if (on) { var first = menu.querySelector("[role=menuitem]"); if (first) first.focus(); }
  }
  function update() {
    var s = pep.state;
    if (!s) return "Hello. Open a study and I will follow it.";
    return s.say || "Nothing new.";
  }
  var compActs = {
    "pep-ask": function () { setMenu(false); pep.bubble = null; draw(); EK.askPepper(); },
    "pep-now": function () { setMenu(false); pep.bubble = update(); draw(); },
    "pep-bubble-close": function () { pep.bubble = null; draw(); var b = document.getElementById("pep-btn"); if (b) b.focus(); },
    "pep-corner": function () {
      setMenu(false);
      var el = host();
      el.style.left = ""; el.style.top = ""; el.style.right = ""; el.style.bottom = "";
      pep.left = false;
      try { localStorage.removeItem(POS); } catch (e) { /* nothing kept */ }
      draw();
    },
    "pep-hide": function () {
      setMenu(false);
      pep.hidden = true;
      pep.hiddenFor = pep.slug;
      draw();
      EK.toast("done", "Pepper is back when the study ends.");
    }
  };
  function wire() {
    var btn = document.getElementById("pep-btn");
    if (!btn) return;
    var press = null, moved = false, start = null, origin = null;
    btn.addEventListener("click", function () {
      if (moved) { moved = false; return; }
      pep.bubble = pep.bubble ? null : update();
      draw();
      var b = document.getElementById("pep-btn"); if (b) b.focus();
    });
    btn.addEventListener("contextmenu", function (ev) { ev.preventDefault(); setMenu(!pep.menu); });
    btn.addEventListener("keydown", function (ev) {
      if ((ev.shiftKey && ev.key === "F10") || ev.key === "ContextMenu") { ev.preventDefault(); setMenu(true); }
    });
    btn.addEventListener("pointerdown", function (ev) {
      if (ev.button !== 0) return;
      var el = host(), rect = el.getBoundingClientRect();
      start = [ev.clientX, ev.clientY];
      origin = [rect.left, rect.top];
      moved = false;
      if (ev.pointerType === "touch") press = setTimeout(function () { press = null; start = null; setMenu(true); }, 550);
      btn.setPointerCapture(ev.pointerId);
    });
    btn.addEventListener("pointermove", function (ev) {
      if (!start) return;
      var dx = ev.clientX - start[0], dy = ev.clientY - start[1];
      if (!moved && Math.abs(dx) + Math.abs(dy) < 8) return;
      clearTimeout(press); press = null;
      moved = true;
      var el = host();
      var x = Math.min(Math.max(0, origin[0] + dx), window.innerWidth - el.offsetWidth);
      var y = Math.min(Math.max(64, origin[1] + dy), window.innerHeight - el.offsetHeight);
      el.style.left = x + "px"; el.style.top = y + "px"; el.style.right = "auto"; el.style.bottom = "auto";
      pep.left = x < window.innerWidth / 2;
      el.classList.toggle("is-left", pep.left);
    });
    btn.addEventListener("pointerup", function () {
      clearTimeout(press); press = null; start = null;
      if (moved) {
        var el = host();
        try { localStorage.setItem(POS, JSON.stringify([el.style.left, el.style.top, pep.left])); } catch (e) { /* not kept */ }
      }
    });
  }
  function restore() {
    try {
      var p = JSON.parse(localStorage.getItem(POS) || "null");
      if (!p) return;
      var el = host();
      var x = parseFloat(p[0]), y = parseFloat(p[1]);
      if (!(x >= 0 && x < window.innerWidth - 40 && y >= 0 && y < window.innerHeight - 40)) return;
      el.style.left = p[0]; el.style.top = p[1]; el.style.right = "auto"; el.style.bottom = "auto";
      pep.left = !!p[2];
    } catch (e) { /* nothing kept */ }
  }

  EK.companion = {
    start: function () { restore(); draw(); window.addEventListener("resize", function () { draw(); }); },
    phase: function () { return pep.phase; },
    /** A page tells Pepper the state it shows: {phase, say, finished}. */
    show: function (o) {
      var was = pep.phase;
      pep.state = o;
      pep.phase = o.phase === null ? null : o.phase || "egg";
      if (pep.hidden && (o.finished || pep.slug !== pep.hiddenFor)) pep.hidden = false;
      if (o.announce && was !== pep.phase && !EK.reducedMotion()) pep.bubble = o.announce;
      if (o.announce && was !== pep.phase && EK.reducedMotion()) pep.bubble = o.announce;
      if (was !== pep.phase || o.redraw) draw();
    },
    onRoute: function (page, slug) {
      if (slug !== pep.slug) { pep.bubble = null; }
      pep.slug = slug;
      if (page !== "study" && page !== "home") { pep.state = pep.state && pep.state.slug === slug ? pep.state : null; }
      pep.menu = false;
      draw();
    },
    answered: function (s) {
      if (EK.drawerKind() === "assistant") return;
      pep.bubble = s && s.say ? "I have an answer. Open Ask Pepper to read it." : null;
      draw();
    },
    escape: function () {
      if (pep.menu) { setMenu(false); var b = document.getElementById("pep-btn"); if (b) b.focus(); return true; }
      if (pep.bubble) { pep.bubble = null; draw(); return true; }
      return false;
    }
  };
})();
