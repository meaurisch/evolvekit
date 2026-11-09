/* evolvekit app: the components of the design (hand-over pack, round 12), one
   function each, named as in the pack. Each returns markup (EK.html); the
   page wires behaviour with data-act attributes (EK.bind). Colours are CSS
   variables only, so every component works in both themes. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html, raw = EK.raw;

  // ------------------------------------------------------------------ icons
  var FILLED = {
    info: '<circle cx="8" cy="8" r="7" style="fill:var(--surface-page);stroke:var(--ink-2)" stroke-width="1.4"/><path d="M8 7v4.5M8 4.6v.1" style="stroke:var(--ink-2)" stroke-width="1.8" stroke-linecap="round"/>',
    good: '<circle cx="8" cy="8" r="8" style="fill:var(--good-tint)"/><path d="M4.5 8.2l2.3 2.3 4.7-5" fill="none" style="stroke:var(--good)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    warning: '<circle cx="8" cy="8" r="8" style="fill:var(--warning-tint)"/><path d="M8 4v5M8 11.6v.1" style="stroke:var(--warning)" stroke-width="2" stroke-linecap="round"/>',
    critical: '<circle cx="8" cy="8" r="8" style="fill:var(--critical-tint)"/><path d="M5.2 5.2l5.6 5.6M10.8 5.2l-5.6 5.6" style="stroke:var(--critical)" stroke-width="2" stroke-linecap="round"/>'
  };
  /** The four filled state icons (round 11): info, good, warning, critical. */
  EK.icon = function (kind, size) {
    var s = size || 20;
    return raw('<svg class="ic" width="' + s + '" height="' + s + '" viewBox="0 0 16 16" aria-hidden="true" focusable="false">' + (FILLED[kind] || FILLED.info) + "</svg>");
  };
  var STROKE = {
    more: '<circle cx="4" cy="10" r="1.7"/><circle cx="10" cy="10" r="1.7"/><circle cx="16" cy="10" r="1.7"/>',
    close: '<path d="M5 5l10 10M15 5L5 15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    menu: '<path d="M3 5h14M3 10h14M3 15h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    plus: '<path d="M10 4v12M4 10h12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    folder: '<path d="M2.5 5.5h5l1.5 2h8.5v8h-15z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    drop: '<path d="M10 3v9M6 8.5l4 4 4-4M3.5 13.5v3h13v-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    check: '<path d="M4.5 10.5l3.5 3.5 7.5-8" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
    gear: '<circle cx="10" cy="10" r="2.6" fill="none" stroke="currentColor" stroke-width="1.6"/><path d="M10 2.5v2.2M10 15.3v2.2M2.5 10h2.2M15.3 10h2.2M4.7 4.7l1.6 1.6M13.7 13.7l1.6 1.6M4.7 15.3l1.6-1.6M13.7 6.3l1.6-1.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
    chev: '<path d="M7.5 4.5l5.5 5.5-5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    back: '<path d="M12.5 4.5L7 10l5.5 5.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    file: '<path d="M5 2.5h6.5l3.5 3.5v11.5H5z M11.5 2.5V6H15" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/>',
    send: '<path d="M3 10h12M10.5 5.5L15 10l-4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/>',
    undo: '<path d="M6 7.5H12a4 4 0 0 1 0 8H8M6 7.5l3-3M6 7.5l3 3" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>',
    info: '<circle cx="10" cy="10" r="7.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10 9v5M10 6.2v.1" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>'
  };
  /** The small stroke icons (round 12), in currentColor. */
  EK.gl = function (kind, size) {
    var s = size || 20;
    return raw('<svg class="ic" width="' + s + '" height="' + s + '" viewBox="0 0 20 20" aria-hidden="true" focusable="false"' +
      (kind === "more" ? ' fill="currentColor"' : "") + ">" + (STROKE[kind] || "") + "</svg>");
  };

  // ------------------------------------------------------------------ attributes
  function attrs(o) {
    if (!o) return "";
    var out = "";
    Object.keys(o).forEach(function (k) {
      var v = o[k];
      if (v == null || v === false) return;
      out += " " + k + (v === true ? "" : '="' + EK.esc(v) + '"');
    });
    return raw(out);
  }
  EK.attrs = attrs;
  function act(o) {
    return { "data-act": o.act, "data-arg": o.arg, "data-arg2": o.arg2 };
  }

  // ------------------------------------------------------------------ controls
  /** Button: primary, secondary, quiet; size m (44 px) or s (36 px, 44 on phones); disabled; on the accent fill. */
  EK.button = function (text, o) {
    o = o || {};
    var cls = "btn btn-" + (o.kind || "primary") + (o.size === "s" ? " btn-s" : "") + (o.onAccent ? " on-accent" : "") + (o.cls ? " " + o.cls : "");
    var a = Object.assign({ type: o.type || "button", class: cls, disabled: !!o.disabled, "aria-describedby": o.describedby,
      "aria-label": o.label, "aria-haspopup": o.haspopup, "aria-expanded": o.expanded, id: o.id, form: o.form }, act(o), o.attrs || {});
    return html`<button${attrs(a)}>${o.icon || ""}<span>${text}</span></button>`;
  };
  /** A link that looks like the design's quiet text link; `href` or an action. */
  EK.link = function (text, o) {
    o = o || {};
    var a = Object.assign({ href: o.href || "#", class: "link" + (o.cls ? " " + o.cls : ""), target: o.target, rel: o.target ? "noopener" : null,
      download: o.download }, o.href ? {} : act(o), o.attrs || {});
    return html`<a${attrs(a)}>${o.icon || ""}${text}</a>`;
  };
  EK.iconButton = function (kind, label, o) {
    o = o || {};
    var a = Object.assign({ type: "button", class: "icon-btn" + (o.cls ? " " + o.cls : ""), "aria-label": label,
      "aria-haspopup": o.haspopup, "aria-expanded": o.expanded }, act(o), o.attrs || {});
    return html`<button${attrs(a)}>${EK.gl(kind, o.size || 20)}</button>`;
  };

  /** Notice: info, warning, error; one line and an optional action. */
  EK.notice = function (kind, text, action) {
    var k = kind === "error" ? "critical" : kind;
    return html`<div class="notice notice-${kind}" role="${kind === "error" ? "alert" : "status"}">${EK.icon(k, 20)}
      <span class="notice-text">${text}</span>${action ? EK.button(action.text, Object.assign({ kind: "quiet", size: "s" }, action)) : ""}</div>`;
  };

  /** TextInput: label, value, hint or error. */
  EK.textInput = function (o) {
    var id = o.id || ("in-" + Math.random().toString(36).slice(2, 8));
    var msg = o.invalid ? html`<span class="field-msg is-error" id="${id}-msg">${EK.icon("critical", 16)}${o.invalid}</span>`
      : o.hint ? html`<span class="field-msg" id="${id}-msg">${o.hint}</span>` : "";
    var tag = o.multiline ? "textarea" : "input";
    var a = { id: id, name: o.name, type: o.multiline ? null : (o.type || "text"), class: "input" + (o.mono ? " mono" : ""),
      placeholder: o.placeholder, "aria-invalid": o.invalid ? "true" : null, "aria-describedby": msg ? id + "-msg" : null,
      autocomplete: o.autocomplete || "off", spellcheck: o.mono ? "false" : null, inputmode: o.inputmode,
      "data-change": o.change, "data-input": o.input, "data-arg": o.arg, rows: o.rows, required: o.required, maxlength: o.maxlength };
    var control = o.multiline ? html`<textarea${attrs(a)}>${o.value || ""}</textarea>` : html`<input${attrs(Object.assign(a, { value: o.value == null ? "" : o.value }))}>`;
    void tag;
    return html`<div class="field"${attrs({ style: o.width ? "max-width:" + o.width + "px" : null })}>
      <label class="field-label" for="${id}">${o.label}</label>${control}${msg}</div>`;
  };

  /** NumberWithUnit: a number in either decimal mark, its unit beside it. */
  EK.numberUnit = function (o) {
    var id = o.id || ("nu-" + Math.random().toString(36).slice(2, 8));
    var msg = o.invalid ? html`<span class="field-msg is-error" id="${id}-msg">${EK.icon("critical", 16)}${o.invalid}</span>`
      : o.hint ? html`<span class="field-msg" id="${id}-msg">${o.hint}</span>` : "";
    return html`<div class="field number-unit"${attrs({ style: o.width ? "width:" + o.width + "px" : null })}>
      <label class="field-label" for="${id}">${o.label}</label>
      <span class="nu-box${o.invalid ? " is-error" : ""}"><input${attrs({ id: id, type: "text", inputmode: "decimal", value: o.value == null ? "" : o.value,
        "aria-invalid": o.invalid ? "true" : null, "aria-describedby": msg ? id + "-msg" : null, "data-change": o.change, "data-arg": o.arg, name: o.name })}>
      <span class="nu-unit">${o.unit}</span></span>${msg}</div>`;
  };

  EK.label = function (text, cls) { return html`<span class="type-label${cls ? " " + cls : ""}">${text}</span>`; };

  /** The page title block: kicker, the title, an optional line, an optional thing on the right. */
  EK.titleBlock = function (kicker, title, sub, right) {
    return html`<div class="title-block"><div class="title-main">${kicker ? EK.label(kicker) : ""}
      <h1 class="type-display" id="page-title" tabindex="-1">${EK.figures(title)}</h1>${sub ? html`<p class="type-body ink-2">${sub}</p>` : ""}</div>${right || ""}</div>`;
  };

  // ------------------------------------------------------------------ the verdict mark
  /** VerdictMark: one shape per answer, so colour is never the only cue: use, keep, worse, none, open, error. */
  EK.verdictMark = function (kind, size, onAccent) {
    var ink = onAccent ? "var(--on-accent)" : "var(--ink-1)", g;
    if (kind === "use") g = '<circle cx="20" cy="20" r="18" style="fill:' + ink + '"/><path d="M11.5 20.5l5.5 5.5 11.5-12" fill="none" style="stroke:var(--accent)" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>';
    else if (kind === "keep") g = '<circle cx="20" cy="20" r="17" fill="none" style="stroke:' + ink + '" stroke-width="2.6"/><path d="M12 16h16M12 24h16" style="stroke:' + ink + '" stroke-width="2.8" stroke-linecap="round"/>';
    else if (kind === "worse") g = '<circle cx="20" cy="20" r="17" style="fill:var(--surface-page);stroke:' + ink + '" stroke-width="2.6"/><path d="M13 13l14 14M27 13L13 27" style="stroke:' + ink + '" stroke-width="3" stroke-linecap="round"/>';
    else if (kind === "none") g = '<circle cx="20" cy="20" r="17" fill="none" style="stroke:' + ink + '" stroke-width="2.6" stroke-dasharray="4 4"/><circle cx="20" cy="20" r="3.5" style="fill:' + ink + '"/>';
    else if (kind === "error") g = '<circle cx="20" cy="20" r="18" style="fill:var(--critical-tint)"/><path d="M13 13l14 14M27 13L13 27" style="stroke:var(--critical)" stroke-width="3" stroke-linecap="round"/>';
    else g = '<circle cx="20" cy="20" r="17" fill="none" style="stroke:' + ink + '" stroke-width="2.6"/><path d="M20 10v10l6 4" fill="none" style="stroke:' + ink + '" stroke-width="2.6" stroke-linecap="round"/>';
    var s = size || 40;
    return raw('<svg class="verdict-mark" width="' + s + '" height="' + s + '" viewBox="0 0 40 40" aria-hidden="true" focusable="false">' + g + "</svg>");
  };

  // ------------------------------------------------------------------ run timeline
  /** RunTimeline: rounds as segments, the final check last, filling while it adds runs.
      o: {rounds, done, running, check: [done, planned], stoppedAt, failed, queued, width} */
  EK.runTimeline = function (o) {
    var w = o.width || 420, rounds = Math.max(1, o.rounds || 1), gap = 3, ckW = w * 0.22;
    var seg = (w - ckW - gap * rounds) / rounds, x = 0, out = [];
    for (var r = 1; r <= rounds; r++) {
      var st;
      if (r <= (o.done || 0)) st = "fill:var(--ink-1)";
      else if (r === o.running) st = "fill:var(--accent);stroke:var(--ink-1);stroke-width:1.5";
      else st = "fill:none;stroke:var(--line-edge);stroke-width:1.2";
      if (o.failed && r === o.running) st = "fill:var(--critical-tint);stroke:var(--critical);stroke-width:1.5";
      if (o.stoppedAt && r > o.stoppedAt) st = "fill:none;stroke:var(--line-rule);stroke-width:1.2";
      out.push('<rect x="' + (x + 0.75).toFixed(1) + '" y="6.75" width="' + Math.max(1, seg - 1.5).toFixed(1) + '" height="10.5" rx="1" style="' + st + '"' +
        (r === o.running && !o.failed ? ' class="breathe"' : "") + "/>");
      x += seg + gap;
    }
    out.push('<rect x="' + (x + 0.75).toFixed(1) + '" y="6.75" width="' + (ckW - 1.5).toFixed(1) + '" height="10.5" rx="1" style="fill:none;stroke:var(--line-edge);stroke-width:1.2"/>');
    if (o.check && o.check[1]) {
      out.push('<rect x="' + (x + 0.75).toFixed(1) + '" y="6.75" width="' + ((ckW - 1.5) * Math.min(1, o.check[0] / o.check[1])).toFixed(1) + '" height="10.5" rx="1" style="fill:var(--ink-1)"/>');
    }
    out.push('<text x="' + x.toFixed(1) + '" y="34" style="fill:var(--ink-2);font:500 12px var(--sans)">final check</text>');
    if (o.queued) out.push('<text x="0" y="34" style="fill:var(--ink-2);font:500 12px var(--sans)">queued</text>');
    var aria = (o.queued ? "Queued; " : "") + (o.done || 0) + " of " + rounds + " rounds done" + (o.running ? ", round " + o.running + " running" : "") +
      (o.check ? ", final check " + o.check[0] + " of " + o.check[1] + " runs" : "");
    return raw('<svg class="run-timeline" width="100%" height="38" viewBox="0 0 ' + w + ' 38" preserveAspectRatio="none" role="img" aria-label="' + EK.esc(aria) + '" style="max-width:' + w + 'px">' + out.join("") + "</svg>");
  };

  // ------------------------------------------------------------------ the likely range
  /** IntervalStrip (RangeStrip): the range as a band through today, the estimate as a dot,
      BOTH ends of the range marked "low end" and "high end" with the same weight. The range is the
      90 % range (`o.name`, one decimal: its low end is the verdict's "at least" / "at worst", decision 15);
      a comparison written before A1 passes its own `{ name: "95 % range", digits: 2 }`.
      Values are improvements in percent: positive is better. */
  EK.intervalStrip = function (mean, lo, hi, o) {
    o = o || {};
    var name = o.name || EK.RANGE_NAME, digits = o.digits == null ? 1 : o.digits;
    var w = o.width || 440, ink = o.onAccent ? "var(--on-accent)" : "var(--ink-1)", sub = o.onAccent ? "var(--on-accent)" : "var(--ink-2)";
    var has = lo != null && hi != null;
    var vals = [mean].concat(has ? [lo, hi] : []);
    var a = Math.floor(Math.min(-2, Math.min.apply(null, vals) - 0.6)), b = Math.ceil(Math.max(3, Math.max.apply(null, vals) + 0.6));
    var l = 14, r = 14, y = 34;
    function X(v) { return l + (v - a) / (b - a) * (w - l - r); }
    var out = ['<line x1="' + l + '" y1="' + y + '" x2="' + (w - r) + '" y2="' + y + '" style="stroke:' + sub + '" stroke-width="1" opacity=".6"/>'];
    var step = b - a <= 8 ? 1 : b - a <= 16 ? 2 : b - a <= 40 ? 5 : 10;
    for (var v = a; v <= b; v++) {
      if (v % step) continue;
      out.push('<line x1="' + X(v).toFixed(1) + '" y1="' + (y - 3) + '" x2="' + X(v).toFixed(1) + '" y2="' + (y + 3) + '" style="stroke:' + sub + '" stroke-width="1"/>');
      if (v !== 0) out.push('<text x="' + X(v).toFixed(1) + '" y="' + (y + 18) + '" text-anchor="middle" style="fill:' + sub + ';font:500 11px var(--mono)">' + (v > 0 ? "+" : "−") + Math.abs(v) + "</text>");
    }
    out.push('<line x1="' + X(0).toFixed(1) + '" y1="' + (y - 16) + '" x2="' + X(0).toFixed(1) + '" y2="' + (y + 8) + '" style="stroke:' + ink + '" stroke-width="2"/>' +
      '<text x="' + X(0).toFixed(1) + '" y="' + (y + 20) + '" text-anchor="middle" style="fill:' + ink + ';font:700 11px var(--sans)">today</text>');
    if (has) {
      out.push('<rect x="' + X(lo).toFixed(1) + '" y="' + (y - 7) + '" width="' + Math.max(1, X(hi) - X(lo)).toFixed(1) + '" height="14" rx="2" style="fill:' + ink + ';fill-opacity:.16;stroke:' + ink + '" stroke-width="1.5"/>');
      var close = X(hi) - X(lo) < 44;
      [[lo, "low end", close ? "end" : "middle"], [hi, "high end", close ? "start" : "middle"]].forEach(function (e) {
        out.push('<path d="M' + X(e[0]).toFixed(1) + " " + (y - 9) + ' l-5 -8 h10 z" style="fill:' + ink + '"/>' +
          '<text x="' + X(e[0]).toFixed(1) + '" y="' + (y - 21) + '" text-anchor="' + e[2] + '" style="fill:' + ink + ';font:700 11px var(--sans)">' + e[1] + "</text>");
      });
    }
    if (mean != null) out.push('<circle cx="' + X(mean).toFixed(1) + '" cy="' + y + '" r="6" style="fill:' + ink + ";stroke:" + (o.onAccent ? "var(--accent)" : "var(--surface-panel)") + '" stroke-width="2"/>');
    var aria = mean == null ? "No estimate yet" : "Estimate " + EK.better(mean, digits) +
      (has ? ", " + name + " from " + EK.better(lo, digits) + " to " + EK.better(hi, digits) : ", no range from one case");
    return html`<div class="interval-strip${o.onAccent ? " on-accent" : ""}"${attrs({ style: "max-width:" + w + "px" })}>
      ${raw('<svg width="100%" height="58" viewBox="0 0 ' + w + ' 58" role="img" aria-label="' + EK.esc(aria) + '" style="display:block;overflow:visible">' + out.join("") + "</svg>")}
      ${o.compact ? "" : html`<div class="strip-caption type-small"><span>← worse</span><span>better →</span></div>`}</div>`;
  };
  /** The range in one sentence, both ends, to one decimal as the verdict's sentence: "90 % range from
      4.5 % better to 8.0 % better", its low end the sentence's "at least" / "at worst" to the digit
      (decision 15). A comparison written before A1 passes its own name and digits ("95 % range", 2). */
  EK.rangeWords = function (lo, hi, name, digits) {
    if (lo == null || hi == null) return "One case: a number, no range.";
    var d = digits == null ? 1 : digits;
    return (name || EK.RANGE_NAME) + " from " + EK.better(lo, d) + " to " + EK.better(hi, d);
  };
  /** What a person is shown of a final check's range: `range90` (A1's 90 % range) as
      `[mean, low, high, name, digits]`; a comparison written before A1, which has only its 95 %
      interval (`ci95`), keeps it in its own words; null without a range. */
  EK.RANGE_NAME = "90 % range";
  EK.shownRange = function (f) {
    if (!f || f.mean == null) return null;
    if (f.range90) return [f.mean, f.range90[0], f.range90[1], EK.RANGE_NAME, 1];
    if (f.ci95) return [f.mean, f.ci95[0], f.ci95[1], "95 % range", 2];
    return null;
  };
  /** The strip of a `shownRange` array, in its own name and digits. */
  EK.rangeStrip = function (r, o) {
    return EK.intervalStrip(r[0], r[1], r[2], Object.assign({ name: r[3], digits: r[4] }, o || {}));
  };

  // ------------------------------------------------------------------ cards and menus
  /** A menu of buttons (role=menu), opened by a 44 px "more" button. items: [{text, act, arg, danger}] or null (separator). */
  EK.menu = function (label, items, id) {
    var out = items.map(function (it) {
      if (!it) return raw('<div role="separator" class="menu-sep"></div>');
      return html`<button type="button" role="menuitem" class="menu-item${it.danger ? " danger" : ""}"${attrs(act(it))}>${it.text}</button>`;
    });
    return html`<div class="menu" role="menu" aria-label="${label}"${attrs({ id: id })} hidden>${out}</div>`;
  };

  /** StudyCard: name, state and its mark; Pepper shows the phase; the menu holds the study's actions.
      c: {slug, name, kicker, kickerTone, head, sub, mark, pepper, timeline, range, actions, href, menu, archived} */
  EK.studyCard = function (c) {
    var menuId = "menu-" + c.slug;
    var vis = "";
    if (c.timeline) vis = html`<div class="card-vis">${EK.runTimeline(Object.assign({ width: 300 }, c.timeline))}</div>`;
    if (c.range) vis = html`<div class="card-vis">${EK.rangeStrip(c.range, { width: 334, compact: true })}</div>`;
    var actions = (c.actions || []).length ? html`<div class="card-actions">${c.actions.map(function (a) {
      return EK.button(a.text, Object.assign({ kind: a.kind || "secondary", size: "s" }, a));
    })}</div>` : "";
    return html`<article class="study-card${c.archived ? " is-archived" : ""}" data-slug="${c.slug}">
      <div class="card-top">${c.mark ? EK.verdictMark(c.mark, 32) : ""}
        <div class="card-names"><span class="type-label${c.kickerTone ? " tone-" + c.kickerTone : ""}">${c.kicker}</span>
          <h2 class="type-heading"><a class="card-link" href="${c.href}">${c.name}</a></h2></div>
        ${c.menu ? html`<div class="menu-wrap">${EK.iconButton("more", "More for " + c.name, { act: "menu", arg: menuId, haspopup: "menu", expanded: "false", cls: "card-more" })}
          ${EK.menu("Study", c.menu, menuId)}</div>` : ""}</div>
      <div class="card-mid"><div class="card-words"><p class="type-body card-head">${EK.figures(c.head || "")}</p>
        ${c.why ? html`<p class="type-small card-why">${c.why}</p>` : ""}
        ${c.sub ?html`<p class="type-small ink-2">${c.sub}</p>` : ""}
        ${c.note ? html`<p class="type-small card-note">${EK.figures(c.note)}</p>` : ""}</div>
        ${c.pepper ? html`<div class="card-pepper">${EK.pepper(c.pepper, 48)}</div>` : ""}</div>${vis}${actions}</article>`;
  };

  // ------------------------------------------------------------------ Start a study
  /** DropZone: empty, dragging, reading, read, refused. o: {id, title, sub, state, accept, multiple, folder, act} */
  EK.dropZone = function (o) {
    var state = o.state || "empty", inner;
    if (state === "reading") {
      inner = html`<p class="type-body strong">${o.title}</p><div class="progress" aria-hidden="true"><div class="progress-fill breathe" style="width:${Math.round((o.frac || 0.3) * 100)}%"></div></div>`;
    } else if (state === "refused") {
      inner = html`${EK.icon("critical", 24)}<p class="type-body strong">${o.title}</p><p class="type-small ink-2">${o.sub || ""}</p>`;
    } else if (state === "read") {
      inner = html`<span class="tone-good">${EK.gl("check", 24)}</span><p class="type-body strong">${o.title}</p><p class="type-small ink-2">${o.sub || ""}</p>`;
    } else {
      inner = html`<span class="ink-2">${EK.gl("drop", 28)}</span><p class="type-body strong">${o.title}</p><p class="type-small ink-2">${o.sub || ""}</p>`;
    }
    return html`<div class="drop-zone is-${state}" role="group" aria-label="${o.label || o.title}"${attrs({ id: o.id, "data-drop": o.drop })}>${inner}
      <input type="file" class="sr-only" tabindex="-1"${attrs({ accept: o.accept, multiple: !!o.multiple, "data-change": o.change, id: o.id ? o.id + "-file" : null, "aria-hidden": "true" })}></div>`;
  };

  /** CaseStrip: one bar per case, taller for a larger case; held-back cases filled; unreadable ones dashed. */
  EK.caseStrip = function (cases, o) {
    o = o || {};
    var n = cases.length;
    if (!n) return "";
    var w = o.width || 700, phone = !!o.phone, gap = n > 30 ? 2 : phone ? 6 : 10;
    var bw = Math.max(2, (w - gap * (n - 1)) / n);
    var sizes = cases.map(function (c) { return c.size || 0; }), lo = Math.min.apply(null, sizes), hi = Math.max.apply(null, sizes);
    var out = [], held = 0;
    cases.forEach(function (c, i) {
      var h = hi > lo ? 18 + (c.size - lo) / (hi - lo) * 34 : 34, x = i * (bw + gap), st;
      if (c.refused) st = 'style="fill:var(--critical-tint);stroke:var(--critical)" stroke-width="1.5" stroke-dasharray="3 2"';
      else if (c.held) { st = 'style="fill:var(--ink-1)"'; held++; }
      else st = 'style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.4"';
      out.push('<rect x="' + x.toFixed(1) + '" y="' + (56 - h).toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" ' + st + "><title>" + EK.esc(c.name) + "</title></rect>");
      if (n <= 16 && (!phone || c.held || c.refused)) {
        out.push('<text x="' + (x + bw / 2).toFixed(1) + '" y="72" text-anchor="middle" style="fill:var(--ink-2);font:500 ' + (phone ? 10 : 11) + 'px var(--mono)">' + EK.esc(c.short || c.name.slice(0, 3)) + "</text>");
      }
    });
    var aria = n + " cases by size; " + held + " held back for the final check";
    return html`${raw('<svg class="case-strip" width="100%" height="76" viewBox="0 0 ' + w + ' 76" preserveAspectRatio="none" role="img" aria-label="' + EK.esc(aria) + '" style="max-width:' + w + 'px">' + out.join("") + "</svg>")}
      <div class="legend type-small ink-2"><span><svg width="10" height="14" aria-hidden="true"><rect x="1" y="1" width="8" height="12" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.4"/></svg>learns from</span>
      <span><svg width="10" height="14" aria-hidden="true"><rect x="0" y="0" width="10" height="14" style="fill:var(--ink-1)"/></svg>held back</span>${o.sized ? html`<span>taller: larger case</span>` : ""}</div>`;
  };

  /** A case's key among the views: its file name without folder or extension (the Tries digest
      names a case `c0-20`, the study's case rows `c0-20.json`, a run `cases/c0-20.json`). */
  function caseKey(n) { return String(n).replace(/\\/g, "/").split("/").pop().replace(/\.[A-Za-z0-9]+$/, ""); }
  EK.caseKey = caseKey;
  /** The group of each case, by `caseKey`: from `d.by_case` (rows with `name` and `group`: a
      per-group run's), else `d.case_rows` (the study's case rows, whose `group` is groups.json's). */
  EK.caseGroups = function (d) {
    var out = {}, base = caseKey;
    ((d && d.by_case) || []).forEach(function (r) { if (r && r.name && r.group) out[base(r.name)] = String(r.group); });
    ((d && d.case_rows) || []).forEach(function (r) { if (r && r.name && r.group && !(base(r.name) in out)) out[base(r.name)] = String(r.group); });
    return out;
  };
  /** CasesBadges: the part of casesView that draws each case's group, a small pill before its row.
      o: {cases (the rows' names, in order), Y (a row's middle), x (the pill's left), w (its width), phone}.
      Without `o`, one pill per case that has a group, stacked: what the badges say. Empty without groups. */
  EK.casesBadges = function (d, o) {
    var groups = EK.caseGroups(d), names = Object.keys(groups);
    if (!names.length) return "";
    o = o || {};
    var cases = o.cases || names, phone = !!o.phone, w = o.w || (phone ? 48 : 78), x = o.x || 4;
    var Y = o.Y || function (i) { return 12 + i * 22; }, most = phone ? 6 : 11, out = [];
    cases.forEach(function (cs, i) {
      var g = groups[caseKey(cs)];
      if (!g) return;
      var text = g.length > most ? g.slice(0, most - 1) + "…" : g, y = Y(i);
      out.push('<g class="case-badge"><rect x="' + x + '" y="' + (y - 9).toFixed(1) + '" width="' + w + '" height="18" rx="9" style="fill:var(--surface-wash);stroke:var(--line-edge)" stroke-width="1"/>' +
        '<text x="' + (x + w / 2) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="middle" style="fill:var(--ink-2);font:600 ' + (phone ? 10 : 11) + 'px var(--sans)">' + EK.esc(text) + "<title>" + EK.esc("Kind of case: " + g) + "</title></text></g>");
    });
    return out.join("");
  };

  /** KindChoice: a radio group drawn as cards. options: [{value, title, sub, disabled}] */
  EK.kindChoice = function (name, legend, options, selected, change, tight) {
    var out = options.map(function (op) {
      var on = op.value === selected;
      return html`<label class="choice-card${on ? " is-on" : ""}${op.disabled ? " is-disabled" : ""}">
        <input type="radio" class="sr-only" name="${name}" value="${op.value}"${attrs({ checked: on, disabled: !!op.disabled, "data-change": change })}>
        <svg class="radio-dot" width="20" height="20" aria-hidden="true"><circle cx="10" cy="10" r="8.5" fill="none" style="stroke:var(--ink-1)" stroke-width="1.8"/>${on ? raw('<circle cx="10" cy="10" r="4.5" style="fill:var(--ink-1)"/>') : ""}</svg>
        <span class="choice-words"><span class="choice-title">${op.title}</span>${op.sub ? html`<span class="type-small ink-2">${op.sub}</span>` : ""}</span></label>`;
    });
    return html`<fieldset class="choice-group${tight ? " is-tight" : ""}"><legend class="sr-only">${legend}</legend>${out}</fieldset>`;
  };

  /** A numbered block of Start a study: number, title, its control. */
  EK.fieldRow = function (num, title, body, aside) {
    return html`<section class="field-row"><span class="field-num" aria-hidden="true">${num}</span>
      <div class="field-row-head"><h2 class="type-heading">${title}</h2>${aside || ""}</div><span></span><div class="field-row-body">${body}</div></section>`;
  };

  // ------------------------------------------------------------------ Set up
  /** SummaryRow: complete, missing or open. o: {kicker, value, sub, state, action, act, arg} */
  EK.summaryRow = function (o) {
    var state = o.state || "complete", mk, btn;
    if (state === "missing") { mk = EK.icon("critical", 20); btn = EK.button(o.action || "Change", { kind: "primary", size: "s", act: o.act, arg: o.arg }); }
    else if (state === "open") { mk = html`<span class="accent-ink">${EK.gl("chev", 20)}</span>`; btn = EK.button(o.action || "Done", { kind: "secondary", size: "s", act: o.act, arg: o.arg }); }
    else if (state === "warning") { mk = EK.icon("warning", 20); btn = EK.button(o.action || "Change", { kind: "secondary", size: "s", act: o.act, arg: o.arg }); }
    else { mk = EK.icon("good", 20); btn = EK.button(o.action || "Change", { kind: "secondary", size: "s", act: o.act, arg: o.arg, label: (o.action || "Change") + " " + o.kicker.toLowerCase() }); }
    return html`<div class="summary-row is-${state}"><div class="summary-mark">${mk}</div>
      <div class="summary-words"><span class="type-label ink-2">${o.kicker}</span><p class="type-body strong">${EK.figures(o.value)}</p>
      ${o.sub ? html`<p class="type-small ink-2">${o.sub}</p>` : ""}</div>${btn}</div>`;
  };

  /** PlanBar: the whole time split into search and final check, rounds ticked off. Fits, thin, impossible.
      o: {state, rounds, searchH, checkH, head, left, right, aria} */
  EK.planBar = function (o) {
    var w = 1000, state = o.state || "fits", svg, overlay = null;
    if (state === "impossible") {
      svg = '<rect x="0.75" y="8.75" width="' + (w - 1.5) + '" height="18.5" rx="2" style="fill:var(--critical-tint);stroke:var(--critical)" stroke-width="1.5" stroke-dasharray="5 3"/>' +
        "";
      overlay = { left: 0, width: 100, text: o.barText || "No room for one full look", critical: true };
    } else {
      var total = Math.max(0.001, (o.searchH || 1) + (o.checkH || 0)), rounds = Math.max(1, o.rounds || 1);
      var sw = (w - 6) * (o.searchH || 1) / total, seg = (sw - 3 * (rounds - 1)) / rounds, parts = [];
      for (var r = 0; r < rounds; r++) {
        parts.push('<rect x="' + (r * (seg + 3) + 0.75).toFixed(1) + '" y="8.75" width="' + Math.max(1, seg - 1.5).toFixed(1) + '" height="18.5" rx="1" style="fill:var(--surface-page);stroke:var(--ink-1)" stroke-width="1.5"/>');
      }
      var cx = sw + 6;
      if (o.checkH) parts.push('<rect x="' + (cx + 0.75).toFixed(1) + '" y="8.75" width="' + Math.max(1, w - cx - 1.5).toFixed(1) + '" height="18.5" rx="1" style="fill:var(--ink-1)"/>');
      svg = parts.join("");
      var overlay = state === "thin" && o.barText ? { left: 0, width: sw / w * 100, text: o.barText } : null;
    }
    var ic = state === "fits" ? "" : EK.icon(state === "thin" ? "warning" : "critical", 20);
    return html`<div class="plan-bar is-${state}"><div class="plan-head">${ic}<span class="plan-title">${EK.figures(o.head || "")}</span></div>
      <div class="plan-track">${raw('<svg width="100%" height="34" viewBox="0 0 ' + w + ' 34" preserveAspectRatio="none" role="img" aria-label="' + EK.esc(o.aria || o.head || "") + '">' + svg + "</svg>")}
        ${overlay ? html`<span class="plan-over${overlay.critical ? " is-critical" : ""}" aria-hidden="true" style="left:${overlay.left}%;width:${overlay.width.toFixed(1)}%"><span>${overlay.text}</span></span>` : ""}</div>
      <div class="plan-caption type-small ink-2"><span>${o.left || ""}</span><span>${o.right || ""}</span></div></div>`;
  };

  /** InfoTip: the setting's plain help from its harness; its technical text behind More detail. */
  EK.infoTip = function (s, id, today, tries) {
    return html`<div class="info-tip" role="note" id="${id}" hidden><span class="info-title">${s.label || s.name}</span>
      ${s.help ? html`<span class="type-small">${s.help}</span>` : ""}
      <span class="type-small ink-2">Today: ${today}${tries ? " · tries " + tries : ""}</span>
      <span class="type-small ink-2">Its own name: <span class="mono">${s.name}</span></span>
      ${s.explain ? html`<details class="more-detail"><summary>More detail</summary><p class="type-small">${s.explain}</p></details>` : ""}</div>`;
  };

  /** SettingRow: name, its (i) right after it, recommended mark, the Tune · Fixed · Default switch, and its detail line. */
  EK.settingRow = function (s, mode, o) {
    o = o || {};
    var tipId = "tip-" + s.name.replace(/[^A-Za-z0-9_-]/g, "-");
    var opts = [["tune", "Tune"], ["fixed", "Fixed"], ["default", "Default"]];
    var seg = opts.map(function (op) {
      var on = op[0] === mode;
      return html`<button type="button" role="radio" class="seg-btn${on ? " is-on" : ""}" aria-checked="${on ? "true" : "false"}"
        data-act="mode" data-arg="${s.name}" data-arg2="${op[0]}" tabindex="${on ? "0" : "-1"}">${op[1]}</button>`;
    });
    var rec = o.recommended ? html`<span class="rec type-small"><svg width="8" height="8" aria-hidden="true"><circle cx="4" cy="4" r="4" style="fill:var(--accent)"/></svg>recommended</span>` : "";
    return html`<div class="setting-row" data-setting="${s.name}"><div class="setting-words">
      <span class="setting-name-line"><span class="setting-name" id="name-${tipId}">${s.label || s.name}</span>
        <span class="info-wrap"><button type="button" class="info-btn" aria-label="What is “${s.label || s.name}”?" aria-expanded="false" aria-controls="${tipId}" data-act="info" data-arg="${tipId}">
          <span class="info-ring">${EK.gl("info", 18)}</span></button>${EK.infoTip(s, tipId, o.today, o.tries)}</span>${rec}</span>
      <span class="type-small ink-2">${o.detail || ""}</span>${o.extra || ""}</div>
      <div class="seg" role="radiogroup" aria-labelledby="name-${tipId}" data-key="segkey">${seg}</div></div>`;
  };

  /** ServiceChoice: the AI services as radio cards. */
  EK.serviceChoice = function (services, selected, change) {
    return EK.kindChoice("service", "Service", services, selected, change, true);
  };

  /** A drawer docked on the right (560 px for Set up, 420 px for Ask Pepper), full screen on a phone with Back. */
  EK.drawer = function (o) {
    return html`<div class="drawer-head">${EK.iconButton("back", o.backLabel || "Back to the page", { act: "drawer-close", cls: "phone-only" })}
      ${o.figure || ""}<div class="drawer-titles"><h2 class="type-heading" id="drawer-title" tabindex="-1">${o.title}</h2>${o.sub ? html`<span class="type-small ink-2">${o.sub}</span>` : ""}</div>
      ${EK.iconButton("close", o.closeLabel || "Close", { act: "drawer-close", cls: "wide-only" })}</div>
      <div class="drawer-body"${attrs({ "aria-live": o.live ? "polite" : null })}>${o.body}</div>${o.foot ? html`<div class="drawer-foot">${o.foot}</div>` : ""}`;
  };

  // ------------------------------------------------------------------ Study
  /** ViewTabs: Tries · Cases · Settings, or Tries · Cases · Code for a code study (`keys`). */
  EK.viewTabs = function (current, counts, keys) {
    var out = (keys || ["tries", "cases", "settings"]).map(function (k) {
      var on = k === current, word = { tries: "Tries", cases: "Cases", settings: "Settings", code: "Code" }[k];
      return html`<button type="button" role="tab" class="tab${on ? " is-on" : ""}" id="tab-${k}" aria-selected="${on ? "true" : "false"}" aria-controls="view-panel"
        tabindex="${on ? "0" : "-1"}" data-act="view" data-arg="${k}" data-key="tabkey">${word}${counts && counts[k] != null ? html`<span class="tab-count">${counts[k]}</span>` : ""}</button>`;
    });
    return html`<div class="tabs" role="tablist" aria-label="Views">${out}</div>`;
  };

  /** ChosenChip: the one chosen combination, carried across the views. */
  /* The figure as the report says it: one decimal, the measure's direction (o.direction), and
     o.pct in place of the combination's own when the page has the verdict document's figure. */
  EK.chosenChip = function (c, o) {
    if (!c) return html`<span class="type-small ink-3 chip-hint">Click a dot to follow it</span>`;
    o = o || {};
    var pct = o.pct != null ? o.pct : c.pct;
    var said = c.failed || pct == null ? "did not finish" : EK.change(pct, o.direction);
    // After the final check's decision (o.after): its word on the checked one, no figure on any.
    var text = o.after != null ? html`${o.after ? " · " + o.after : ""}`
      : html` · ${said}<span class="wide-only"> on the cases it learned from, not checked</span>`;
    return html`<div class="chip"><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.5" style="fill:var(--accent);stroke:var(--ink-1)" stroke-width="1.5"/></svg>
      <span class="chip-text"><span class="wide-only">Combination </span><span class="phone-only">No. </span>${c.n}${text}</span>
      <button type="button" class="chip-close" aria-label="Stop following combination ${c.n}" data-act="unchoose">${EK.gl("close", 14)}</button></div>`;
  };

  /** HoverCard: the combination under the pointer (or focus, or tap). */
  EK.hoverCard = function (lines, o) {
    o = o || {};
    return html`<div class="hover-card" role="tooltip" id="hover-card"><span class="hc-head">${lines[0]}</span><span class="type-body">${lines[1] || ""}</span>
      ${lines[2] ? html`<span class="type-small ink-2">${lines[2]}</span>` : ""}
      ${o.hint ? html`<span class="type-small accent-ink">${o.hint}</span>` : ""}${o.close ? EK.button("Close", { kind: "quiet", size: "s", act: "hover-close" }) : ""}</div>`;
  };

  /** RunList: the study's runs with their verdicts, the current one marked. */
  EK.runList = function (runs, current, hrefFor) {
    return html`<ul class="run-list">${runs.map(function (r) {
      var on = r.id === current;
      return html`<li><a class="run-row${on ? " is-on" : ""}" href="${hrefFor(r)}"${attrs({ "aria-current": on ? "true" : null })}>
        ${EK.verdictMark(r.mark || "open", 28)}<span class="run-when">${EK.when(r.started_at) || r.id}</span><span class="type-small ink-2">${r.line || r.phase || ""}${r.history ? html`<span class="run-history">${r.history}</span>` : ""}</span>
        ${on ? html`<span class="type-label accent-ink">this one</span>` : ""}</a></li>`;
    })}</ul>`;
  };

  // ------------------------------------------------------------------ Ask Pepper
  EK.chatBubble = function (text, mine) {
    return html`<p class="bubble type-small${mine ? " is-mine" : ""}">${mine ? html`<span class="sr-only">You: </span>` : html`<span class="sr-only">Pepper: </span>`}${text}</p>`;
  };
  EK.thinkingRow = function () {
    var dots = [0, 1, 2].map(function (i) { return '<circle cx="' + (6 + i * 12) + '" cy="6" r="4" style="fill:var(--ink-2)" class="breathe"/>'; }).join("");
    return html`<div class="thinking" role="status">${raw('<svg width="36" height="12" aria-hidden="true">' + dots + "</svg>")}
      <span class="type-small ink-2">Thinking… up to a minute</span>${EK.button("Cancel", { kind: "quiet", size: "s", act: "ask-cancel" })}</div>`;
  };

  /** ProposalCard: proposed, edited, applied, dismissed, not expressible. */
  EK.proposalCard = function (o) {
    if (o.state === "dismissed") {
      return html`<div class="proposal is-dismissed"><span class="type-small ink-2">Dismissed: ${o.title}</span>
        ${EK.button("Show", { kind: "quiet", size: "s", act: "card-show", arg: o.key })}</div>`;
    }
    var top = o.state === "applied"
      ? html`<span class="proposal-top"><span class="type-label ink-2">${o.kindWord}</span><span class="type-small tone-good strong applied">${EK.icon("good", 18)}Applied</span></span>`
      : html`<span class="type-label ink-2">${o.kindWord}</span>`;
    var check = o.check ? html`<p class="type-small proposal-check">${EK.icon(o.problem ? "warning" : "good", 18)}<span>${o.check}</span></p>` : "";
    var acts;
    if (o.state === "edited") acts = [EK.button("Apply edit", { size: "s", act: "card-apply", arg: o.key }), EK.button("Cancel", { kind: "quiet", size: "s", act: "card-edit", arg: o.key })];
    else if (o.state === "applied") acts = [];
    else if (o.problem) acts = [EK.button("Edit", { kind: "secondary", size: "s", act: "card-edit", arg: o.key }), EK.button("Dismiss", { kind: "quiet", size: "s", act: "card-dismiss", arg: o.key })];
    else acts = [EK.button("Apply", { size: "s", act: "card-apply", arg: o.key }), EK.button("Edit", { kind: "secondary", size: "s", act: "card-edit", arg: o.key }),
      EK.button("Dismiss", { kind: "quiet", size: "s", act: "card-dismiss", arg: o.key })];
    return html`<article class="proposal is-${o.state || "proposed"}" data-card="${o.key}">${top}<p class="type-body strong">${EK.figures(o.title)}</p>
      ${o.state === "edited" ? o.editor : html`<p class="type-small ink-2">${o.detail || ""}</p>`}${check}
      ${acts.length ? html`<div class="proposal-acts">${acts}</div>` : ""}</article>`;
  };

  // ------------------------------------------------------------------ Compare
  /** CompareView row: two columns aligned by a label; on a phone A then B under it. */
  EK.compareView = EK.compareRow = function (label, a, b, note) {
    return html`<section class="compare-row"><h3 class="type-label ink-2">${label}</h3>
      <div class="cmp-side"><span class="cmp-tag phone-only" aria-hidden="true">A</span><div>${a}</div></div>
      <div class="cmp-side"><span class="cmp-tag phone-only" aria-hidden="true">B</span><div>${b}</div></div>${note ? html`<div class="cmp-note">${note}</div>` : ""}</section>`;
  };
  EK.verdictLine = function (kind, head, sub) {
    return html`<div class="verdict-line">${EK.verdictMark(kind, 36)}<div><p class="type-heading">${EK.figures(head)}</p><p class="type-small ink-2">${sub || ""}</p></div></div>`;
  };
  /** CasePair: shared held-back cases, A's dot and B's square on one axis. rows: [[name, a, b]] (improvements in %). */
  EK.casePair = function (rows, o) {
    o = o || {};
    var w = o.width || 1000, l = 64, r = 16, rh = 30;
    var all = []; rows.forEach(function (x) { if (x[1] != null) all.push(x[1]); if (x[2] != null) all.push(x[2]); });
    var A = Math.floor(Math.min(-1, Math.min.apply(null, all.concat([0])) - 0.5)), B = Math.ceil(Math.max(3, Math.max.apply(null, all.concat([0])) + 0.5));
    function X(v) { return l + (v - A) / (B - A) * (w - l - r); }
    var H = rows.length * rh + 30, out = [];
    var step = B - A <= 8 ? 1 : B - A <= 20 ? 2 : 5;
    for (var v = A; v <= B; v++) {
      if (v % step || v === 0) continue;
      out.push('<line x1="' + X(v).toFixed(1) + '" y1="4" x2="' + X(v).toFixed(1) + '" y2="' + (rows.length * rh + 6) + '" style="stroke:var(--line-rule)" stroke-width="1"/>' +
        '<text x="' + X(v).toFixed(1) + '" y="' + (rows.length * rh + 22) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + (v > 0 ? "+" : "−") + Math.abs(v) + " %</text>");
    }
    out.push('<line x1="' + X(0).toFixed(1) + '" y1="4" x2="' + X(0).toFixed(1) + '" y2="' + (rows.length * rh + 6) + '" style="stroke:var(--ink-1)" stroke-width="1.5"/>' +
      '<text x="' + X(0).toFixed(1) + '" y="' + (rows.length * rh + 22) + '" text-anchor="middle" style="fill:var(--ink-1);font:700 11px var(--sans)">today</text>');
    rows.forEach(function (x, i) {
      var y = 6 + i * rh + rh / 2;
      out.push('<text x="' + (l - 10) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" style="fill:var(--ink-1);font:500 12px var(--mono)">' + EK.esc(x[0]) + "</text>");
      if (x[1] != null && x[2] != null) out.push('<line x1="' + X(Math.min(x[1], x[2])).toFixed(1) + '" y1="' + y + '" x2="' + X(Math.max(x[1], x[2])).toFixed(1) + '" y2="' + y + '" style="stroke:var(--line-edge)" stroke-width="2"/>');
      if (x[1] != null) out.push('<circle cx="' + X(x[1]).toFixed(1) + '" cy="' + y + '" r="6" style="fill:var(--ink-1)"/>');
      if (x[2] != null) out.push('<rect x="' + (X(x[2]) - 5.5).toFixed(1) + '" y="' + (y - 5.5).toFixed(1) + '" width="11" height="11" style="fill:var(--surface-page);stroke:var(--ink-1)" stroke-width="2"/>');
    });
    var aria = "Held-back cases joined by name: " + rows.map(function (x) { return x[0] + ": A " + EK.better(x[1]) + ", B " + EK.better(x[2]); }).join("; ");
    return html`${raw('<svg class="case-pair" width="100%" viewBox="0 0 ' + w + " " + H + '" role="img" aria-label="' + EK.esc(aria) + '">' + out.join("") + "</svg>")}
      <div class="legend type-small ink-2"><span><svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5.5" style="fill:var(--ink-1)"/></svg>A</span>
      <span><svg width="12" height="12" aria-hidden="true"><rect x="1" y="1" width="10" height="10" style="fill:var(--surface-page);stroke:var(--ink-1)" stroke-width="2"/></svg>B</span><span>right: better than today</span></div>`;
  };

  // ------------------------------------------------------------------ the locked page
  EK.lockedPage = function () {
    return html`<div class="locked"><div class="locked-card">${EK.pepper("cocoon-asleep", 96)}
      <h1 class="type-title">Open the app from its own address</h1>
      <p class="type-body">Open the app from the address printed in the window it runs in.</p></div></div>`;
  };
})();
