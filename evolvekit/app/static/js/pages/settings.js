/* evolvekit app: Settings — the AI assistant (Pepper's service).
   Four services: OpenRouter, Azure OpenAI, Claude Code here, GitHub Copilot
   CLI. Keys are kept on this computer and never shown again. Test asks the
   model one tiny question and says what to do when it fails. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  var SERVICES = [
    { value: "openrouter", title: "OpenRouter", sub: "One key for many models", keys: [["OPENROUTER_API_KEY", "OpenRouter key"]], model: "anthropic/claude-sonnet-5" },
    { value: "azure", title: "Azure OpenAI", sub: "Key and endpoint", keys: [["AZURE_OPENAI_API_KEY", "Azure OpenAI key"], ["AZURE_OPENAI_ENDPOINT", "Endpoint"]], model: "" },
    { value: "claude-cli", title: "Claude Code here", sub: "No key needed", keys: [], model: "" },
    { value: "copilot-cli", title: "GitHub Copilot CLI", sub: "Your GitHub sign-in", keys: [], model: "" }
  ];
  var st = null;
  function svc(v) { return SERVICES.filter(function (s) { return s.value === v; })[0] || SERVICES[0]; }

  function render(main, slug, query, gen) {
    return EK.get("/api/settings").then(function (s) {
      if (!EK.isCurrent(gen)) return;
      st = { saved: s, provider: s.provider, models: Object.assign({}, s.models), keys: {}, replacing: {}, check: null, checking: false, prices: JSON.parse(JSON.stringify(s.prices || {})) };
      draw(main);
    });
  }

  /* What the notice says once the app has stopped, for how it was started: the installer
     (`frozen`: evolvekit-app.exe, updated by extracting a new zip) or pip (`evolvekit app`). A study
     that is running holds the app folder's files, so a new version goes over it only when none is. */
  function stoppedWords(frozen) {
    var words = "The app has stopped. Studies that are running carry on. ";
    if (!frozen) return words + "To open it again, run evolvekit app.";
    return words + "To open it again, double-click evolvekit-app.exe. To update the app, extract the new version over its folder " +
      "only when no study is running (Home shows them); while one runs, extract it into a new folder and start it from there.";
  }

  function hasKeys() { return svc(st.provider).keys.every(function (k) { return st.saved.keys[k[0]] && st.keys[k[0]] !== ""; }); }
  function head() {
    var c = st.check, p = st.provider;
    if (p === "copilot-cli") {
      if (c && c.ok) return "Pepper is ready, through Copilot";
      if (c && c.state === "policy_blocked") return "Copilot CLI is blocked";
      if (c && (c.state === "not_signed_in" || c.state === "missing")) return "Pepper needs a Copilot sign-in";
      return "Pepper through Copilot";
    }
    if (!hasKeys()) return "Pepper needs a key";
    if (!c) return "Pepper’s AI service";
    if (c.ok) return c.usd == null && p !== "claude-cli" ? "Pepper is ready, cost not known" : "Pepper is ready";
    if (/refus|401|403|invalid|unauthori|key/i.test(c.reason || "")) return "The key was refused";
    return "No answer from the model";
  }
  function account() {
    if (st.provider === "copilot-cli") {
      var c = st.check || {}, kind = c.ok ? "ok" : c.state || "unknown";
      var rows = {
        ok: ["good", "Signed in with your GitHub account", "Through Copilot CLI on this computer. No key here.", null],
        not_signed_in: ["warning", "Not signed in to Copilot CLI", "Open a terminal, run copilot, then type /login.", { text: "How to sign in", act: "how-login" }],
        missing: ["warning", "Copilot CLI is not installed", "Install it, then sign in once.", { text: "How to install", act: "how-login" }],
        policy_blocked: ["critical", "Signed in, but Copilot CLI is off", "Your organisation’s Copilot policy blocks it.", { text: "What to ask your admin", act: "how-admin" }],
        failed: ["warning", "Copilot CLI gave an error", c.reason || "", null],
        unknown: ["info", "Uses your GitHub sign-in", "Run Test to see if Copilot CLI works here.", null]
      }[kind] || [];
      return html`<div class="app-line">${EK.icon(rows[0], 20)}<div class="app-line-words"><span class="app-line-title">${rows[1]}</span><span class="type-small ink-2">${rows[2]}</span></div>
        ${rows[3] ? EK.button(rows[3].text, { kind: "quiet", size: "s", act: rows[3].act }) : ""}</div>`;
    }
    if (st.provider === "claude-cli") {
      return html`<div class="app-line">${EK.icon("info", 20)}<div class="app-line-words"><span class="app-line-title">Claude Code on this computer</span>
        <span class="type-small ink-2">Uses its own sign-in. No key here.</span></div></div>`;
    }
    return EK.join(svc(st.provider).keys.map(function (k) {
      var set = st.saved.keys[k[0]] && st.keys[k[0]] !== "";
      if (set && !st.replacing[k[0]]) {
        return html`<div class="field"><span class="field-label">${k[1]}</span><div class="app-line"><span class="type-data" style="font-size:14px;flex:1;min-width:120px">set · •••• kept here</span>
          ${EK.button("Replace", { kind: "secondary", size: "s", act: "replace", arg: k[0] })}${EK.button("Remove", { kind: "quiet", size: "s", act: "remove-key", arg: k[0] })}</div></div>`;
      }
      return EK.textInput({ id: "key-" + k[0], label: k[1], type: k[0].indexOf("ENDPOINT") >= 0 ? "text" : "password", value: st.keys[k[0]] || "",
        placeholder: k[0].indexOf("ENDPOINT") >= 0 ? "https://….openai.azure.com" : "Paste it here", hint: "Kept on this computer. Never shown again.", autocomplete: "off", mono: k[0].indexOf("ENDPOINT") >= 0 });
    }));
  }
  function result() {
    var c = st.check, p = st.provider;
    if (st.checking) return html`<div class="app-line" role="status"><span class="breathe">${EK.icon("info", 20)}</span><div class="app-line-words"><span class="app-line-title">Asking the model…</span><span class="type-small ink-2">Up to a minute.</span></div></div>`;
    var row;
    if (!c) row = !hasKeys() && p !== "copilot-cli" && p !== "claude-cli" ? ["info", "No key yet: Pepper cannot answer.", ""] : ["info", "Not tested yet.", "Test asks one tiny question."];
    else if (c.ok) {
      var cost = p === "copilot-cli" ? "Cost: counted as Copilot requests in your plan, not in dollars."
        : c.usd != null ? "Answered in " + EK.num(c.seconds, 1) + " s for $" + EK.num(c.usd, 4) + "." : p === "claude-cli" ? "Answered in " + EK.num(c.seconds, 1) + " s." : "Cost not known for this model. Add its price below.";
      row = [c.usd == null && p !== "copilot-cli" && p !== "claude-cli" ? "warning" : "good", p === "copilot-cli" ? "Copilot CLI works. Pepper can answer." : "It works. Pepper can answer.", cost];
    } else if (c.state === "policy_blocked") row = ["critical", "Access denied by your organisation.", "An admin can turn on Copilot CLI for you."];
    else if (c.state === "not_signed_in" || c.state === "missing") row = ["warning", "Pepper cannot answer yet.", c.reason || "Sign in once; Pepper then uses it in the background."];
    else row = [/refus|401|403|invalid|unauthori/i.test(c.reason || "") ? "critical" : "warning", /refus|401|403|invalid|unauthori/i.test(c.reason || "") ? "The service refused the key." : "The model gave no answer.", c.reason || "Try again, or pick another model."];
    return html`<div class="app-line" role="${row[0] === "critical" ? "alert" : "status"}">${EK.icon(row[0], 20)}<div class="app-line-words"><span class="app-line-title">${row[1]}</span>
      ${row[2] ? html`<span class="type-small ink-2">${row[2]}</span>` : ""}</div>${EK.button("Test", { kind: "secondary", size: "s", act: "test", disabled: !hasKeys() && svc(p).keys.length > 0 })}</div>`;
  }
  function draw(main) {
    var p = st.provider, s = svc(p);
    var hint = p === "azure" ? "The deployment name on your Azure resource." : p === "openrouter" ? priceHint(st.models.assistant) : "Leave empty for " + (p === "copilot-cli" ? "Copilot’s" : "Claude Code’s") + " default model.";
    EK.mount(main, html`<div class="page"><div class="page-inner" style="max-width:784px">${EK.titleBlock("Settings · the AI assistant", head())}
      <form class="sub-section" data-submit="save" style="gap:22px">
        <div><h2 class="type-heading" style="margin-bottom:10px">Service</h2>${EK.serviceChoice(SERVICES, p, "service")}</div>
        ${account()}
        ${EK.textInput({ id: "model-assistant", label: "Model", value: st.models.assistant || "", mono: true, placeholder: s.model || (p === "copilot-cli" ? "Copilot’s default" : p === "claude-cli" ? "Claude Code’s default" : "deployment name"), hint: hint, input: "model" })}
        <div class="actions">${EK.button("Save", { type: "submit" })}${EK.button("Cancel", { kind: "quiet", act: "cancel" })}</div>
      </form>
      ${result()}
      <details class="expert"><summary>Models and prices</summary><div class="sub-section">
        ${EK.textInput({ id: "model-search", label: "Model for AI search help", value: st.models.search || "", mono: true, hint: "The model that proposes combinations, when AI help is on." })}
        ${p === "copilot-cli" ? html`<p class="type-small ink-2">Copilot counts requests in your plan, so no price is needed.</p>` : priceTable()}</div></details>
      <div class="sub-section"><h2 class="type-heading">This app</h2>
        <p class="type-body">The app runs on this computer until you stop it. Studies that are running carry on.</p>
        <div class="actions">${EK.button("Stop the app", { kind: "secondary", act: "stop-app" })}</div></div>
      <div class="link-row">${EK.link("Library folder", { act: "library" })}</div></div></div>`, acts(main));
    EK.companion.show({ phase: st.check && st.check.ok ? "cocoon" : "cocoon-asleep", say: head() + "." });
  }
  function priceHint(model) {
    var pr = st.prices[model];
    return pr ? "Price: $" + pr[0] + " in, $" + pr[1] + " out per million tokens." : "OpenRouter says what each answer cost.";
  }
  function priceTable() {
    var rows = Object.keys(st.prices).map(function (m, i) {
      var pr = st.prices[m];
      return html`<tr><td><input class="input mono" id="pm-${i}" value="${m}" aria-label="Model"></td><td><input class="input" id="pi-${i}" inputmode="decimal" value="${pr[0]}" aria-label="$ per million read"></td>
        <td><input class="input" id="po-${i}" inputmode="decimal" value="${pr[1]}" aria-label="$ per million written"></td><td>${EK.iconButton("close", "Remove the price of " + m, { act: "price-remove", arg: m })}</td></tr>`;
    });
    return html`<p class="type-small ink-2">For models whose cost the app does not know: dollars per million tokens.</p>
      <div class="cases-wrap"><table class="small-table"><thead><tr><th>Model</th><th>$ / M read</th><th>$ / M written</th><th><span class="sr-only">Remove</span></th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="actions">${EK.button("Add a model’s price", { kind: "secondary", size: "s", act: "price-add" })}${EK.button("Save prices", { kind: "secondary", size: "s", act: "save" })}</div>`;
  }
  function readPrices() {
    var out = {};
    Object.keys(st.prices).forEach(function (m, i) {
      var name = (document.getElementById("pm-" + i) || {}).value, a = EK.readNumber((document.getElementById("pi-" + i) || {}).value), b = EK.readNumber((document.getElementById("po-" + i) || {}).value);
      if (name && name.trim() && a != null && b != null) out[name.trim()] = [a, b];
    });
    return out;
  }
  function acts(main) {
    return {
      service: function (el) {
        st.provider = el.value;
        st.models = el.value === st.saved.provider ? Object.assign({}, st.saved.models) : { assistant: svc(el.value).model, search: svc(el.value).model };
        st.check = null;
        draw(main);
      },
      model: function (el) { st.models.assistant = el.value; },
      replace: function (el) { st.replacing[el.getAttribute("data-arg")] = true; draw(main); var i = document.getElementById("key-" + el.getAttribute("data-arg")); if (i) i.focus(); },
      "remove-key": function (el) { st.keys[el.getAttribute("data-arg")] = ""; draw(main); },
      cancel: function () { EK.rerender(); },
      save: function () {
        var keys = {};
        svc(st.provider).keys.forEach(function (k) {
          var el = document.getElementById("key-" + k[0]);
          if (el && el.value.trim()) keys[k[0]] = el.value.trim();
          else if (st.keys[k[0]] === "") keys[k[0]] = "";
        });
        var assistant = (document.getElementById("model-assistant") || {}).value, search = (document.getElementById("model-search") || {}).value;
        var body = { provider: st.provider, models: { assistant: (assistant == null ? st.models.assistant : assistant).trim(), search: (search == null ? st.models.search || "" : search).trim() }, keys: keys };
        if (st.provider !== "copilot-cli" && document.getElementById("pm-0") !== null) body.prices = readPrices();
        if (!body.models.search) body.models.search = body.models.assistant;
        EK.put("/api/settings", body).then(function (s) {
          st.saved = s; st.provider = s.provider; st.models = Object.assign({}, s.models); st.keys = {}; st.replacing = {}; st.prices = JSON.parse(JSON.stringify(s.prices || {}));
          EK.toast("done", "Saved");
          draw(main);
        }).catch(EK.fail);
      },
      test: function () {
        st.checking = true; draw(main);
        EK.post("/api/settings/check").then(function (c) { st.check = c; }, function (err) { st.check = { ok: false, reason: err.message }; }).then(function () { st.checking = false; draw(main); });
      },
      "price-add": function () { st.prices = readPrices(); st.prices[st.models.assistant || "model"] = [0, 0]; draw(main); },
      "price-remove": function (el) { st.prices = readPrices(); delete st.prices[el.getAttribute("data-arg")]; draw(main); },
      "how-login": function () {
        EK.openDrawer({ title: "Sign in to Copilot CLI", body: html`<ol class="type-body"><li>Install it: <span class="mono">npm install -g @github/copilot</span></li>
          <li>Open a terminal and run <span class="mono">copilot</span>.</li><li>Type <span class="mono">/login</span> and follow the steps.</li>
          <li>Come back here and press Test.</li></ol><p class="type-small ink-2">Enterprise accounts work too, when the organisation allows Copilot CLI.</p>` });
      },
      "how-admin": function () {
        EK.openDrawer({ title: "Ask your admin", body: html`<p class="type-body">Ask them to turn on Copilot CLI in your organisation’s Copilot policies.</p>
          <p class="type-small ink-2">Copilot CLI said: “Access denied by policy settings”.</p>` });
      },
      "stop-app": function () {
        return EK.post("/api/shutdown").then(function () {
          EK.mount(main, html`<div class="page"><div class="page-inner" style="max-width:784px">${EK.notice("info", stoppedWords(st.saved.frozen))}</div></div>`);
        }).catch(EK.fail);
      },
      library: function () {
        EK.openDrawer({ title: "Library folder", body: html`<p class="code">${st.saved.home || ""}</p><div class="actions">${EK.button("Copy the path", { kind: "secondary", act: "copy" })}</div>`,
          acts: { copy: function () { EK.copy(st.saved.home || "").then(function (ok) { EK.toast(ok ? "done" : "error", ok ? "Path copied" : "Could not copy"); }); } } });
      }
    };
  }

  EK.pages.settings = { render: render };
})();
