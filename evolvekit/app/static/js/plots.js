/* evolvekit app: the Study page's three views, drawn from GET …/tries.
   triesPlot   every try by when it finished and how much better than today;
               the best-so-far line (full looks only); rings for tries
               running now; the chosen combination and its family.
   casesView   a row per study case, a dot per try, the tally of wins.
   settingsPanel  one panel per tuned setting: the value tried across, the
               result up; the band of good values; today's value dashed.
   Each returns {svg, points}: points are the dots, in the SVG's own
   coordinates, for hover cards, taps and the keyboard. */
(function () {
  "use strict";
  var EK = window.EK;
  function f(x) { return (Math.round(x * 10) / 10).toString(); }
  function esc(s) { return EK.esc(s); }

  var sharedPrefix = "";
  /** Tell the short names which cases they are among: a prefix all of them share is left out. */
  EK.caseNames = function (names) {
    var list = (names || []).map(function (n) { return String(n).replace(/\.[A-Za-z0-9]+$/, ""); });
    sharedPrefix = "";
    if (list.length < 2) return;
    var p = list[0];
    list.forEach(function (n) { while (p && n.indexOf(p) !== 0) p = p.slice(0, -1); });
    var cut = Math.max(p.lastIndexOf("-"), p.lastIndexOf("_"), p.lastIndexOf("."), p.lastIndexOf(" "));
    sharedPrefix = cut >= 0 ? p.slice(0, cut + 1) : "";
  };
  /** A case's short name (t01, 03): its first word, or what is left after the shared prefix. */
  EK.shortCase = function (name) {
    var n = String(name).replace(/\.[A-Za-z0-9]+$/, "");
    if (sharedPrefix && n.indexOf(sharedPrefix) === 0 && n.length > sharedPrefix.length) n = n.slice(sharedPrefix.length);
    var first = n.split(/[-_.\s]/)[0];
    return first.length >= 1 && first.length <= 6 ? first : n.slice(0, 6);
  };
  EK.caseLabel = function (name, phone) { return phone ? EK.shortCase(name) : String(name).length > 22 ? String(name).slice(0, 21) + "…" : String(name); };

  function axisHours(end, W, X, H, phone) {
    var out = [];
    if (end * 3600 < 120) {
      var secs = end * 3600, ss = secs <= 15 ? 2 : secs <= 40 ? 5 : secs <= 80 ? 10 : 20;
      if (phone) ss *= 2;
      for (var t = 0; t <= secs + 1e-9; t += ss) out.push('<text x="' + f(X(t / 3600)) + '" y="' + (H - 10) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + t + " s</text>");
      return out.join("");
    }
    if (end < 1.5) {
      var mins = end * 60, step = mins <= 20 ? 5 : mins <= 45 ? 10 : 15;
      if (phone) step *= 2;
      for (var m = 0; m <= mins + 1e-9; m += step) out.push('<text x="' + f(X(m / 60)) + '" y="' + (H - 10) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + m + " min</text>");
      return out.join("");
    }
    var hs = end <= 4 ? 0.5 : end <= 10 ? 1 : end <= 30 ? 3 : end <= 72 ? 6 : 12;
    if (phone) hs *= 2;
    for (var h = 0; h <= end + 1e-9; h += hs) out.push('<text x="' + f(X(h)) + '" y="' + (H - 10) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + (Math.round(h * 10) / 10) + " h</text>");
    return out.join("");
  }

  function lineage(byId, id, base) {
    var out = [], seen = {};
    while (id && byId[id] && !seen[id]) { out.push(byId[id]); seen[id] = 1; id = byId[id].parent; }
    if (base && byId[base] && !seen[base]) out.push(byId[base]);
    return out;
  }

  /** o: {W, H, phone, chosen, family, keepWord} */
  EK.triesPlot = function (d, o) {
    var W = o.W, H = o.H, phone = !!o.phone, done = d.done;
    var l = phone ? 44 : 56, r = done ? (phone ? 20 : 24) : (phone ? 36 : 70), top = phone ? 22 : 26, b = phone ? 30 : 34;
    var combos = d.combinations.filter(function (c) { return !c.base; });
    var hi = 5, lo = -6;
    combos.forEach(function (c) { if (c.pct != null && c.full) hi = Math.max(hi, Math.ceil(c.pct + 1)); });
    hi = Math.min(hi, 50);
    var end = Math.max(d.end_h, done ? 0 : o.planH || 0, 1 / 3600); // while it runs, the axis spans the planned time
    function X(h) { return l + h / end * (W - l - r); }
    function Y(p) { return top + (hi - Math.max(lo, Math.min(hi, p))) / (hi - lo) * (H - top - b); }
    var base = H - b, out = [], points = [];
    var rounds = d.rounds || [];
    rounds.forEach(function (rd, i) {
      if (rd.round % 2 === 0) return;
      var e = i + 1 < rounds.length ? rounds[i + 1].start_h : end;
      out.push('<rect x="' + f(X(rd.start_h)) + '" y="' + top + '" width="' + f(Math.max(0, X(e) - X(rd.start_h))) + '" height="' + f(base - top) + '" style="fill:var(--surface-panel)" opacity=".7"/>');
    });
    var grid = [];
    var gstep = hi - lo > 20 ? 5 : 2;
    for (var g = Math.floor(lo / gstep) * gstep; g <= hi; g += gstep) if (g !== 0 && g >= lo) grid.push(g);
    if (phone) grid = grid.filter(function (g) { return g % (gstep * 2) === 0; });
    grid.forEach(function (p) {
      out.push('<line x1="' + l + '" y1="' + f(Y(p)) + '" x2="' + (W - r) + '" y2="' + f(Y(p)) + '" style="stroke:var(--line-rule)" stroke-width="1"/>' +
        '<text x="' + (l - 8) + '" y="' + f(Y(p) + 4) + '" text-anchor="end" style="fill:var(--ink-2);font:500 11px var(--mono)">' + (p > 0 ? "+" : "−") + Math.abs(p) + " %</text>");
    });
    out.push('<line x1="' + l + '" y1="' + f(Y(0)) + '" x2="' + (W - r) + '" y2="' + f(Y(0)) + '" style="stroke:var(--ink-1)" stroke-width="1.5"/>' +
      '<text x="' + (l - 8) + '" y="' + f(Y(0) + 4) + '" text-anchor="end" style="fill:var(--ink-2);font:500 11px var(--mono)">0</text>' +
      '<text x="' + (l + 6) + '" y="' + f(Y(0) + 15) + '" style="fill:var(--ink-1);font:700 10px var(--sans);letter-spacing:.12em">' + esc((o.keepWord || "TODAY’S SETTINGS").toUpperCase()) + "</text>");
    out.push(axisHours(end, W, X, H, phone));
    out.push('<text x="' + l + '" y="12" style="fill:var(--ink-2);font:700 10px var(--sans);letter-spacing:.12em">% BETTER THAN TODAY</text>');
    (d.restarts || []).forEach(function (h) {
      out.push('<line x1="' + f(X(h)) + '" y1="' + (base - 16) + '" x2="' + f(X(h)) + '" y2="' + base + '" style="stroke:var(--ink-2)" stroke-width="1.2"/>' +
        (phone ? "" : '<text x="' + f(X(h) + 4) + '" y="' + (base - 6) + '" style="fill:var(--ink-2);font:600 10px var(--sans)">restarted</text>'));
    });
    var byId = {};
    d.combinations.forEach(function (c) { byId[c.id] = c; });
    if (o.family && o.chosen && byId[o.chosen]) {
      var fl = lineage(byId, o.chosen, d.base).map(function (c) { return f(X(c.end_h)) + "," + f(Y(c.base ? 0 : c.pct || 0)); });
      out.push('<polyline points="' + fl.join(" ") + '" fill="none" style="stroke:var(--ink-1)" stroke-width="1.5" stroke-dasharray="5 4"/>');
    }
    if ((d.best || []).length) {
      var pts = [], prev = null;
      d.best.forEach(function (s) {
        if (prev != null) pts.push(f(X(s.h)) + "," + f(Y(prev)));
        pts.push(f(X(s.h)) + "," + f(Y(s.pct)));
        prev = s.pct;
      });
      pts.push(f(X(done ? end : d.now_h)) + "," + f(Y(prev)));
      out.push('<polyline points="' + pts.join(" ") + '" fill="none" style="stroke:var(--accent-line)" stroke-width="3" stroke-linejoin="round"/>');
    }
    var rr = phone ? 0.8 : 1;
    combos.forEach(function (c) {
      var x = X(c.end_h);
      if (c.failed || c.pct == null) {
        out.push('<path d="M' + f(x - 3.5) + " " + f(base - 9.5) + 'l7 7m0-7l-7 7" style="stroke:var(--ink-2)" stroke-width="1.4"/>');
        points.push({ id: c.id, x: x, y: base - 6 });
        return;
      }
      if (c.pct < lo) {
        out.push('<path d="M' + f(x - 4) + " " + f(base - 10) + 'h8l-4 7z" style="fill:var(--ink-2)"/>');
        points.push({ id: c.id, x: x, y: base - 7 });
        return;
      }
      var y = Y(c.pct);
      if (c.best) out.push('<circle cx="' + f(x) + '" cy="' + f(y) + '" r="' + f(4.6 * rr) + '" style="fill:var(--ink-1);stroke:var(--surface-page)" stroke-width="1.2"/>');
      else if (c.full) out.push('<circle cx="' + f(x) + '" cy="' + f(y) + '" r="' + f(3.6 * rr) + '" style="fill:var(--ink-1)"/>');
      else out.push('<circle cx="' + f(x) + '" cy="' + f(y) + '" r="' + f(3.2 * rr) + '" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.4"/>');
      points.push({ id: c.id, x: x, y: y });
    });
    if (o.chosen && byId[o.chosen] && !byId[o.chosen].base) {
      var c = byId[o.chosen], cy = c.pct == null ? base - 6 : Y(c.pct);
      out.push('<circle cx="' + f(X(c.end_h)) + '" cy="' + f(cy) + '" r="5.5" style="fill:var(--accent);stroke:var(--ink-1)" stroke-width="1.5"/>' +
        '<circle cx="' + f(X(c.end_h)) + '" cy="' + f(cy) + '" r="10" fill="none" style="stroke:var(--ink-1)" stroke-width="2"/>');
    }
    if (!done) {
      var nx = X(d.now_h);
      out.push('<line x1="' + f(nx) + '" y1="' + top + '" x2="' + f(nx) + '" y2="' + base + '" style="stroke:var(--ink-1)" stroke-width="1" stroke-dasharray="2 3"/>' +
        '<text x="' + f(nx) + '" y="' + (top - 8) + '" text-anchor="middle" style="fill:var(--ink-1);font:700 10px var(--sans);letter-spacing:.12em">NOW</text>');
      var R = phone ? 5.5 : 7, step = phone ? 15 : 21, circ = 2 * Math.PI * R;
      (d.running || []).slice(0, Math.floor((base - top - 12) / step)).forEach(function (it, i) {
        var cx = nx + (phone ? 13 : 20), cy2 = top + 12 + i * step, frac = Math.min(1, it.done / Math.max(1, it.need));
        out.push('<circle cx="' + f(cx) + '" cy="' + f(cy2) + '" r="' + R + '" fill="none" style="stroke:var(--line-rule)" stroke-width="3"/>' +
          '<circle cx="' + f(cx) + '" cy="' + f(cy2) + '" r="' + R + '" fill="none" style="stroke:var(--accent-line)" stroke-width="3" class="breathe" stroke-dasharray="' +
          f(frac * circ) + " " + f(circ) + '" transform="rotate(-90 ' + f(cx) + " " + f(cy2) + ')"/>');
      });
    }
    var n = d.cases.length;
    var aria = "Every try so far, by time and by how much better than today; the orange line is the best so far on all " + n + " study cases" +
      (done ? "" : "; rings at now are tries still running") + ". Use the arrow keys to go from try to try, Enter to follow one.";
    return { svg: '<svg class="plot" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" tabindex="0" aria-label="' + esc(aria) + '" data-plot="tries">' + out.join("") + "</svg>", points: points };
  };

  EK.triesLegend = function (d, o) {
    var it = [["line", "best so far"], ["full", d.cases.length + " cases"], ["quick", (d.quick_cases || "few") + " cases"], ["x", "did not finish"]];
    if (!d.done) it.push(["ring", "running"]);
    if (o && o.family) it.push(["fam", "its family"]);
    var g = {
      line: '<svg width="22" height="8" aria-hidden="true"><path d="M0 4h22" style="stroke:var(--accent-line)" stroke-width="3"/></svg>',
      full: '<svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.6" style="fill:var(--ink-1)"/></svg>',
      quick: '<svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="3.2" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.4"/></svg>',
      x: '<svg width="10" height="10" aria-hidden="true"><path d="M1.5 1.5l7 7m0-7l-7 7" style="stroke:var(--ink-2)" stroke-width="1.4"/></svg>',
      ring: '<svg width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" style="stroke:var(--line-rule)" stroke-width="3"/><path d="M8 2a6 6 0 0 1 6 6" fill="none" style="stroke:var(--accent-line)" stroke-width="3"/></svg>',
      fam: '<svg width="22" height="8" aria-hidden="true"><path d="M0 4h22" style="stroke:var(--ink-1)" stroke-width="1.5" stroke-dasharray="5 4"/></svg>'
    };
    return EK.raw('<div class="plot-legend">' + it.map(function (x) { return "<span>" + g[x[0]] + esc(x[1]) + "</span>"; }).join("") + "</div>");
  };

  /** The latest result per (try, case): a full look replaces a quick look. */
  EK.caseResults = function (d) {
    var out = {}, full = d.full_stage;
    d.combinations.forEach(function (c) {
      if (c.base) return;
      Object.keys(c.cases || {}).forEach(function (stage) {
        var per = c.cases[stage];
        Object.keys(per).forEach(function (cs) {
          var v = per[cs];
          if (v == null) return;
          var k = c.id + "|" + cs;
          if (out[k] && out[k].full && stage !== full) return;
          out[k] = { c: c, cs: cs, pct: v, full: stage === full };
        });
      });
    });
    return out;
  };

  EK.casesView = function (d, o) {
    var W = o.W, phone = !!o.phone, rowH = phone ? 30 : 34, l = phone ? 64 : 200, r = phone ? 54 : 120, top = 34;
    var cases = d.cases, n = cases.length, H = top + n * rowH + 26;
    var res = EK.caseResults(d), A = -6, B = 6;
    // As drawn: worse than -6 % is pinned to the left edge; the better side grows with the best result, up to +20 %.
    Object.keys(res).forEach(function (k) { if (res[k].full) B = Math.max(B, Math.min(20, Math.ceil(res[k].pct))); });
    function X(v) { return l + (Math.max(A, Math.min(B, v)) - A) / (B - A) * (W - l - r); }
    function Y(i) { return top + i * rowH + rowH / 2; }
    var out = [], points = [];
    out.push('<line x1="' + f(X(0)) + '" y1="' + (top - 6) + '" x2="' + f(X(0)) + '" y2="' + (top + n * rowH) + '" style="stroke:var(--ink-1)" stroke-width="1.5"/>' +
      '<text x="' + f(X(0)) + '" y="' + (top - 12) + '" text-anchor="middle" style="fill:var(--ink-1);font:700 10px var(--sans);letter-spacing:.12em">TODAY</text>' +
      '<text x="' + l + '" y="' + (top - 12) + '" style="fill:var(--ink-2);font:700 10px var(--sans);letter-spacing:.12em">← WORSE</text>' +
      '<text x="' + (W - r) + '" y="' + (top - 12) + '" text-anchor="end" style="fill:var(--ink-2);font:700 10px var(--sans);letter-spacing:.12em">BETTER →</text>' +
      (phone ? "" : '<text x="' + (W - r + 16) + '" y="' + (top - 12) + '" style="fill:var(--ink-2);font:700 10px var(--sans);letter-spacing:.12em">BETTER IN</text>'));
    var step = B - A > 24 ? 5 : 2;
    for (var v = Math.ceil(A / step) * step; v <= B; v += step) {
      if (v === 0) continue;
      out.push('<line x1="' + f(X(v)) + '" y1="' + top + '" x2="' + f(X(v)) + '" y2="' + (top + n * rowH) + '" style="stroke:var(--line-rule)" stroke-width="1"/>' +
        '<text x="' + f(X(v)) + '" y="' + (H - 6) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + (v > 0 ? "+" : "−") + Math.abs(v) + " %</text>");
    }
    var tallies = {};
    cases.forEach(function (cs, i) {
      out.push('<line x1="' + l + '" y1="' + f(top + (i + 1) * rowH) + '" x2="' + (W - r) + '" y2="' + f(top + (i + 1) * rowH) + '" style="stroke:var(--line-rule)" stroke-width="1"/>' +
        '<text x="' + (l - 12) + '" y="' + f(Y(i) + 4) + '" text-anchor="end" style="fill:var(--ink-1);font:500 12px var(--mono)">' + esc(EK.caseLabel(cs, phone)) + "<title>" + esc(cs) + "</title></text>");
      var full = Object.keys(res).map(function (k) { return res[k]; }).filter(function (p) { return p.cs === cs && p.full; });
      var k2 = full.filter(function (p) { return p.pct > 0; }).length;
      tallies[cs] = [k2, full.length];
      out.push('<text x="' + (W - r + 16) + '" y="' + f(Y(i) + 4) + '" style="fill:var(--ink-1);font:600 12px var(--mono)">' + (full.length ? k2 + " of " + full.length : "–") + "</text>");
    });
    Object.keys(res).forEach(function (k) {
      var p = res[k], i = cases.indexOf(p.cs);
      if (i < 0) return;
      var jit = ((p.c.n * 37) % 13 - 6) * (rowH / 34) * 1.3, x = X(p.pct), y = Y(i) + jit;
      out.push(p.full ? '<circle cx="' + f(x) + '" cy="' + f(y) + '" r="3.2" style="fill:var(--ink-1)" opacity=".85"/>'
        : '<circle cx="' + f(x) + '" cy="' + f(y) + '" r="3" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.3"/>');
      points.push({ id: p.c.id, x: x, y: y, cs: p.cs });
    });
    if (o.chosen) {
      var pts = Object.keys(res).map(function (k) { return res[k]; }).filter(function (p) { return p.c.id === o.chosen && cases.indexOf(p.cs) >= 0; })
        .map(function (p) { return [X(p.pct), Y(cases.indexOf(p.cs))]; }).sort(function (a, b) { return a[1] - b[1]; });
      if (pts.length) {
        out.push('<polyline points="' + pts.map(function (q) { return f(q[0]) + "," + f(q[1]); }).join(" ") + '" fill="none" style="stroke:var(--accent-line)" stroke-width="2.5" stroke-linejoin="round"/>');
        pts.forEach(function (q) { out.push('<circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="5" style="fill:var(--accent);stroke:var(--ink-1)" stroke-width="1.5"/>'); });
      }
    }
    var aria = "One row per study case; each dot is one try on that case, left worse, right better than today. Use the arrow keys to go from try to try.";
    return { svg: '<svg class="plot" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" tabindex="0" aria-label="' + esc(aria) + '" data-plot="cases">' + out.join("") + "</svg>", points: points, tallies: tallies };
  };

  function valueText(p, v) {
    if (v == null) return "";
    if (p.type === "bool") return v ? "on" : "off";
    if (typeof v === "number" && !Number.isInteger(v)) return Math.abs(v) >= 1000 ? EK.int(v) : EK.num(v, 2);
    if (typeof v === "number") return EK.int(v);
    return String(v);
  }
  EK.valueText = valueText;
  /** How much a setting moved the result: the spread of the mean result across its values (full looks). */
  EK.settingWeight = function (d, p) {
    var tried = d.combinations.filter(function (c) { return !c.base && c.full && c.pct != null && c.params && c.params[p.name] != null; });
    if (tried.length < 3) return 0;
    if (p.type === "bool" || p.type === "choice") {
      var groups = {};
      tried.forEach(function (c) { var k = String(c.params[p.name]); (groups[k] = groups[k] || []).push(c.pct); });
      var means = Object.keys(groups).filter(function (k) { return groups[k].length >= 2; }).map(function (k) { return groups[k].reduce(function (a, b) { return a + b; }, 0) / groups[k].length; });
      return means.length > 1 ? Math.max.apply(null, means) - Math.min.apply(null, means) : 0;
    }
    var xs = tried.map(function (c) { var v = c.params[p.name]; return p.log && v > 0 ? Math.log(v) : v; }), ys = tried.map(function (c) { return c.pct; });
    var mx = xs.reduce(function (a, b) { return a + b; }, 0) / xs.length, my = ys.reduce(function (a, b) { return a + b; }, 0) / ys.length;
    var sxy = 0, sxx = 0, syy = 0;
    xs.forEach(function (x, i) { sxy += (x - mx) * (ys[i] - my); sxx += (x - mx) * (x - mx); syy += (ys[i] - my) * (ys[i] - my); });
    return sxx && syy ? Math.abs(sxy / Math.sqrt(sxx * syy)) * Math.sqrt(syy / ys.length) : 0;
  };

  EK.settingsPanel = function (d, p, o) {
    var W = o.W, H = o.H || 236, l = 40, r = 12, top = 12, b = 28;
    var hi = 4, lo = -6;
    d.combinations.forEach(function (c) { if (c.pct != null && c.full) hi = Math.max(hi, Math.min(40, Math.ceil(c.pct + 1))); });
    function PY(v) { return top + (hi - Math.max(lo, Math.min(hi, v))) / (hi - lo) * (H - top - b); }
    var choices = p.type === "choice" ? (p.choices || []).map(String) : null;
    var PX;
    if (p.type === "bool") PX = function (v) { return l + (W - l - r) * (v ? 0.72 : 0.28); };
    else if (choices) PX = function (v) { var i = choices.indexOf(String(v)); return l + (W - l - r) * ((i + 0.5) / Math.max(1, choices.length)); };
    else if (p.log && p.low > 0) PX = function (v) { return l + (Math.log10(Math.max(v, 1e-12)) - Math.log10(p.low)) / (Math.log10(p.high) - Math.log10(p.low)) * (W - l - r); };
    else PX = function (v) { return l + (v - p.low) / ((p.high - p.low) || 1) * (W - l - r); };
    var out = [], points = [], g0 = null, g1 = null, pts = [];
    for (var v = hi; v >= lo; v -= 2) {
      out.push('<line x1="' + l + '" y1="' + f(PY(v)) + '" x2="' + (W - r) + '" y2="' + f(PY(v)) + '" style="stroke:' + (v === 0 ? "var(--ink-1)" : "var(--line-rule)") + '" stroke-width="' + (v === 0 ? 1.2 : 1) + '"/>' +
        '<text x="' + (l - 6) + '" y="' + f(PY(v) + 4) + '" text-anchor="end" style="fill:var(--ink-2);font:500 11px var(--mono)">' + (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v) + "</text>");
    }
    d.combinations.forEach(function (c) {
      if (c.base || c.pct == null || !c.params || c.params[p.name] == null) return;
      var val = c.params[p.name];
      if (p.type !== "bool" && !choices && c.full && c.pct > 1) { g0 = g0 == null ? val : Math.min(g0, val); g1 = g1 == null ? val : Math.max(g1, val); }
      var jit = p.type === "bool" || choices ? ((c.n * 53) % 21 - 10) * 3.2 : 0;
      pts.push([PX(val) + jit, PY(c.pct), c]);
    });
    if (g0 != null) out.unshift('<rect x="' + f(PX(g0) - 3) + '" y="' + top + '" width="' + f(Math.max(6, PX(g1) - PX(g0) + 6)) + '" height="' + f(H - top - b) + '" style="fill:var(--accent-band)"/>');
    if (p.default != null) out.push('<line x1="' + f(PX(p.default)) + '" y1="' + top + '" x2="' + f(PX(p.default)) + '" y2="' + (H - b) + '" style="stroke:var(--ink-1)" stroke-width="1.4" stroke-dasharray="3 3"/>');
    var ticks;
    if (p.type === "bool") ticks = [[PX(false), "off"], [PX(true), "on"]];
    else if (choices) ticks = choices.map(function (c) { return [PX(c), c]; });
    else {
      ticks = [];
      if (p.log && p.low > 0) {
        for (var e = Math.ceil(Math.log10(p.low)); e <= Math.floor(Math.log10(p.high)); e++) ticks.push([PX(Math.pow(10, e)), valueText(p, Math.pow(10, e))]);
        if (ticks.length < 2) ticks = [[PX(p.low), valueText(p, p.low)], [PX(p.high), valueText(p, p.high)]];
      } else {
        for (var k = 0; k <= 4; k++) { var tv = p.low + (p.high - p.low) * k / 4; ticks.push([PX(tv), valueText(p, p.type === "int" ? Math.round(tv) : tv)]); }
      }
    }
    ticks.forEach(function (t) {
      out.push('<line x1="' + f(t[0]) + '" y1="' + (H - b) + '" x2="' + f(t[0]) + '" y2="' + (H - b + 4) + '" style="stroke:var(--ink-2)" stroke-width="1"/>' +
        '<text x="' + f(t[0]) + '" y="' + (H - b + 17) + '" text-anchor="middle" style="fill:var(--ink-2);font:500 11px var(--mono)">' + esc(t[1]) + "</text>");
    });
    pts.forEach(function (q) {
      if (q[2].id === o.chosen) return;
      out.push(q[2].full ? '<circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="3.4" style="fill:var(--ink-1)" opacity=".85"/>'
        : '<circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="3" style="fill:var(--surface-page);stroke:var(--line-edge)" stroke-width="1.3"/>');
      points.push({ id: q[2].id, x: q[0], y: q[1], setting: p.name });
    });
    pts.forEach(function (q) {
      if (q[2].id !== o.chosen) return;
      out.push('<circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="5.5" style="fill:var(--accent);stroke:var(--ink-1)" stroke-width="1.5"/><circle cx="' + f(q[0]) + '" cy="' + f(q[1]) + '" r="10" fill="none" style="stroke:var(--ink-1)" stroke-width="2"/>');
      points.push({ id: q[2].id, x: q[0], y: q[1], setting: p.name });
    });
    var label = o.label || p.name;
    var aria = label + ": each dot is a try, across the value it used, up by how much better; dashed is today’s value";
    var good = g0 != null ? "Good: " + valueText(p, g0) + " to " + valueText(p, g1) : "";
    return { svg: '<svg class="plot" viewBox="0 0 ' + W + " " + H + '" width="100%" role="img" tabindex="0" aria-label="' + esc(aria) + '" data-plot="setting" data-setting="' + esc(p.name) + '">' + out.join("") + "</svg>", points: points, good: good };
  };
})();
