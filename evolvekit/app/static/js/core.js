/* evolvekit app: the core every other script uses.
   - EK.html`...` builds markup; every interpolated value is escaped unless it
     is EK.raw(...) or another EK.html result, so text from a study or a
     harness can never become markup.
   - EK.api(...) talks to the app's JSON API with this launch's key.
   - Timers, toasts, the undo bar, and the words for numbers. */
(function () {
  "use strict";
  var EK = (window.EK = window.EK || {});
  EK.pages = EK.pages || {};

  // ------------------------------------------------------------------ markup
  function Raw(text) { this.__html = String(text); }
  Raw.prototype.toString = function () { return this.__html; };
  function raw(text) { return text instanceof Raw ? text : new Raw(text == null ? "" : text); }
  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function part(value) {
    if (value == null || value === false) return "";
    if (value instanceof Raw) return value.__html;
    if (Array.isArray(value)) return value.map(part).join("");
    return esc(value);
  }
  /** Tagged template: EK.html`<p>${text}</p>`. */
  function html(strings) {
    var out = strings[0];
    for (var i = 1; i < arguments.length; i++) out += part(arguments[i]) + strings[i];
    return new Raw(out);
  }
  EK.raw = raw;
  EK.esc = esc;
  EK.html = html;
  EK.join = function (items, sep) { return raw(items.map(part).join(sep == null ? "" : part(sep))); };

  // ------------------------------------------------------------------ DOM
  EK.$ = function (sel, root) { return (root || document).querySelector(sel); };
  EK.$$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  /** Put markup into an element and wire its [data-act] (click), [data-change], [data-input] and [data-submit] handlers. */
  EK.mount = function (el, markup, acts) {
    el.innerHTML = part(markup);
    if (acts) EK.bind(el, acts);
    return el;
  };
  EK.bind = function (el, acts) {
    el._acts = acts;
    if (el._bound) return;
    el._bound = true;
    function run(attr, ev) {
      var target = ev.target.closest && ev.target.closest("[" + attr + "]");
      if (!target || !el.contains(target)) return;
      var fn = el._acts && el._acts[target.getAttribute(attr)];
      if (!fn) return;
      if (attr === "data-act" && target.tagName === "A") ev.preventDefault();
      if (attr === "data-submit") ev.preventDefault();
      if (target.disabled || target.getAttribute("aria-disabled") === "true") return;
      var done = fn(target, ev);
      if (done && typeof done.then === "function" && target.tagName === "BUTTON") {
        target.disabled = true;
        target.setAttribute("aria-busy", "true");
        var settle = function () { target.disabled = false; target.removeAttribute("aria-busy"); };
        done.then(settle, settle);
      }
    }
    el.addEventListener("click", function (ev) { run("data-act", ev); });
    el.addEventListener("change", function (ev) { run("data-change", ev); });
    el.addEventListener("input", function (ev) { run("data-input", ev); });
    el.addEventListener("submit", function (ev) { run("data-submit", ev); });
    el.addEventListener("keydown", function (ev) { run("data-key", ev); });
  };

  // ------------------------------------------------------------------ the key and the API
  var TOKEN_KEY = "evolvekit.token";
  (function takeToken() {
    var url = new URL(location.href);
    var given = url.searchParams.get("token");
    if (given) {
      try { sessionStorage.setItem(TOKEN_KEY, given); } catch (e) { /* private mode: keep it in memory */ }
      EK._token = given;
      url.searchParams.delete("token");
      history.replaceState(null, "", url.pathname + url.search + url.hash);
    } else {
      try { EK._token = sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { EK._token = ""; }
    }
  })();
  EK.hasToken = function () { return !!EK._token; };
  EK.forgetToken = function () {
    EK._token = "";
    try { sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* nothing kept */ }
  };
  /** A URL that opens in a new tab or downloads: it carries the key in its query. */
  EK.withToken = function (url) {
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(EK._token);
  };

  function ApiError(status, message, body) {
    this.status = status;
    this.message = message;
    this.body = body;
  }
  ApiError.prototype = Object.create(Error.prototype);
  EK.ApiError = ApiError;

  /** One API call. body: an object (JSON), a Blob/ArrayBuffer (raw bytes), or nothing. */
  EK.api = function (method, path, body, opts) {
    opts = opts || {};
    var headers = { "X-Evolvekit-Token": EK._token };
    var payload;
    if (body instanceof Blob || body instanceof ArrayBuffer) {
      payload = body;
      headers["Content-Type"] = "application/octet-stream";
    } else if (body !== undefined) {
      payload = JSON.stringify(body);
      headers["Content-Type"] = "application/json";
    }
    return fetch(path, { method: method, headers: headers, body: payload, signal: opts.signal }).then(function (res) {
      var kind = res.headers.get("Content-Type") || "";
      var read = kind.indexOf("application/json") === 0 ? res.json() : res.text();
      return read.then(function (data) {
        if (res.ok) return data;
        var message = (data && data.error) || (typeof data === "string" && data) || ("The app answered " + res.status);
        if (res.status === 403 && /carries its key/.test(message)) {
          EK.forgetToken();
          if (EK.onLocked) EK.onLocked();
        }
        throw new ApiError(res.status, message, data);
      });
    });
  };
  EK.get = function (path, opts) { return EK.api("GET", path, undefined, opts); };
  EK.post = function (path, body) { return EK.api("POST", path, body === undefined ? {} : body); };
  EK.patch = function (path, body) { return EK.api("PATCH", path, body); };
  EK.put = function (path, body) { return EK.api("PUT", path, body); };
  EK.del = function (path) { return EK.api("DELETE", path); };
  EK.q = encodeURIComponent;
  EK.studyPath = function (slug, rest) { return "/api/studies/" + encodeURIComponent(slug) + (rest || ""); };
  /** The Study page of a study (and a run of it): "#/study/<slug>?run=<id>". */
  EK.studyHref = function (slug, run) { return "#/study/" + EK.q(slug) + (run ? "?run=" + EK.q(run) : ""); };

  // ------------------------------------------------------------------ timers: every one ends with its page
  var timers = [];
  EK.every = function (ms, fn) { var id = setInterval(fn, ms); timers.push(["i", id]); return id; };
  EK.later = function (ms, fn) { var id = setTimeout(fn, ms); timers.push(["t", id]); return id; };
  EK.clearTimers = function () {
    timers.forEach(function (t) { (t[0] === "i" ? clearInterval : clearTimeout)(t[1]); });
    timers = [];
  };
  EK.reducedMotion = function () { return window.matchMedia("(prefers-reduced-motion: reduce)").matches; };
  EK.isPhone = function () { return window.matchMedia("(max-width: 759.98px)").matches; };

  // ------------------------------------------------------------------ words for numbers
  EK.num = function (v, digits) {
    if (v == null || !isFinite(v)) return "";
    var d = digits == null ? (Math.abs(v) >= 1000 ? 0 : Math.abs(v) >= 100 ? 1 : 2) : digits;
    return Number(v).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  /** An integer with thousands separators. */
  EK.int = function (v) { return v == null ? "" : Math.round(v).toLocaleString("en-GB"); };
  /** A signed percentage as "1.23 %". */
  EK.pct = function (v, digits) { return EK.num(Math.abs(v), digits == null ? 2 : digits) + " %"; };
  /** An improvement in percent (positive = better) in words: "1.23 % better", "0.54 % worse", "about the same". */
  EK.better = function (v, digits) {
    if (v == null || !isFinite(v)) return "did not finish";
    var d = digits == null ? 2 : digits;
    if (Math.abs(v) < Math.pow(10, -d) / 2) return "about the same";
    return EK.pct(v, d) + (v >= 0 ? " better" : " worse");
  };
  /** A change in percent of the starting point (positive = a gain) as the verdict and the report
      write it: one decimal and the measure's own direction, "4.8 % lower", "0.3 % higher", "the same". */
  EK.change = function (v, direction) {
    if (v == null || !isFinite(v)) return "-";
    if (Math.abs(v) < 0.05) return "the same";
    var toward = direction === "higher" ? "higher" : "lower", away = toward === "higher" ? "lower" : "higher";
    return Math.abs(v).toFixed(1) + " % " + (v > 0 ? toward : away);
  };
  /** The chance of a gain in words; under the rule's 19 in 20 it never rounds up to read like a pass.
      A comparison written before A1 (`posterior === false`) keeps its own words: under its 9 in 10. */
  EK.chanceWords = function (p, posterior) {
    if (p == null || !isFinite(p)) return "";
    if (p >= 0.995) return "almost certain";
    if (posterior === false) { if (p < 0.9 && Math.round(p * 20) >= 18) return "chance under 9 in 10"; }
    else if (p < 0.95 && Math.round(p * 20) >= 19) return "chance under 19 in 20";
    if (p < 0.05) return "chance less than 1 in 20";
    if (Math.round(p * 20) >= 20) return "chance better than 19 in 20";
    return "chance about " + Math.round(p * 20) + " in 20";
  };
  EK.plural = function (n, one, many) { return EK.int(n) + " " + (n === 1 ? one : (many || one + "s")); };
  EK.duration = function (seconds) {
    if (seconds == null || !isFinite(seconds)) return "";
    var s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + " s";
    var m = Math.round(s / 60);
    if (m < 60) return m + " min";
    var h = Math.floor(m / 60), rest = m % 60;
    if (h >= 10 || rest === 0) return (h >= 10 ? Math.round(m / 60) : h) + " h";
    return h + " h " + rest + " min";
  };
  EK.hours = function (h) {
    if (h == null) return "";
    return h < 1 ? EK.duration(h * 3600) : (Math.round(h * 10) / 10).toLocaleString("en-GB") + " h";
  };
  EK.when = function (stamp) {
    if (!stamp) return "";
    var t = typeof stamp === "number" ? new Date(stamp * 1000) : new Date(stamp);
    if (isNaN(t)) return String(stamp);
    var now = new Date();
    var time = t.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    if (t.toDateString() === now.toDateString()) return "today " + time;
    var y = new Date(now); y.setDate(now.getDate() - 1);
    if (t.toDateString() === y.toDateString()) return "yesterday " + time;
    return t.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + time;
  };
  /** A number typed with either decimal mark: "0,9" and "0.9" both read 0.9. */
  EK.readNumber = function (text) {
    var s = String(text == null ? "" : text).trim().replace(/\s/g, "");
    if (!s) return null;
    if (/^-?\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, "");
    else s = s.replace(",", ".");
    var v = Number(s);
    return isFinite(v) ? v : null;
  };
  EK.capital = function (s) { s = String(s || ""); return s.charAt(0).toUpperCase() + s.slice(1); };
  /** Figures inside a serif heading switch to Overpass 700 (the design's rule).
      The figures are found in the plain text and each piece escaped once, so an
      entity's digits (the 39 of &#39;) are never wrapped. */
  EK.figures = function (text) {
    return raw(String(text == null ? "" : text).split(/(\d[\d.,]*\s?%?)/).map(function (bit, i) {
      return i % 2 ? '<span class="fig">' + esc(bit) + "</span>" : esc(bit);
    }).join(""));
  };

  // ------------------------------------------------------------------ toasts and the undo bar
  EK.toast = function (kind, text) {
    var box = document.getElementById("toasts");
    if (!box) return;
    var el = document.createElement("div");
    el.className = "toast";
    el.setAttribute("role", kind === "error" ? "alert" : "status");
    el.innerHTML = part(html`${EK.icon(kind === "error" ? "critical" : "good", 20)}<span class="toast-text">${text}</span>
      <button type="button" class="icon-btn" aria-label="Dismiss">${EK.gl("close", 16)}</button>`);
    el.querySelector("button").onclick = function () { el.remove(); };
    box.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "error" ? 8000 : 5000);
  };
  /** Report a failed call: its sentence in a toast. */
  EK.fail = function (err) {
    if (err && err.status === 403 && /carries its key/.test(err.message || "")) return;
    EK.toast("error", (err && err.message) || String(err));
  };

  var undoTimer = null;
  EK.UNDO_S = 10;
  /** One line and Undo for ten seconds; onUndo runs if pressed. */
  EK.undo = function (text, onUndo) {
    var bar = document.getElementById("undo");
    if (!bar) return;
    clearInterval(undoTimer);
    var left = EK.UNDO_S;
    bar.hidden = false;
    bar.innerHTML = part(html`<span class="undo-text">${text}</span><span class="undo-secs" aria-hidden="true">${left} s</span>
      <button type="button" class="undo-btn">Undo</button><span class="undo-line" aria-hidden="true"></span>`);
    var secs = bar.querySelector(".undo-secs"), line = bar.querySelector(".undo-line");
    line.style.width = "100%";
    function close() { clearInterval(undoTimer); bar.hidden = true; bar.innerHTML = ""; }
    bar.querySelector("button").onclick = function () {
      close();
      Promise.resolve(onUndo()).catch(EK.fail);
    };
    undoTimer = setInterval(function () {
      left -= 1;
      if (left <= 0) return close();
      secs.textContent = left + " s";
      line.style.width = (left / EK.UNDO_S * 100) + "%";
    }, 1000);
    EK.hideUndo = close;
  };
  /** UndoBar: the pack's name for EK.undo. */
  EK.undoBar = function (text, onUndo) { return EK.undo(text, onUndo); };
  EK.hideUndo = function () {
    var bar = document.getElementById("undo");
    if (bar) { bar.hidden = true; bar.innerHTML = ""; }
    clearInterval(undoTimer);
  };

  /** Copy text; true when it worked. */
  EK.copy = function (text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text).then(function () { return true; }, function () { return fallback(); });
    }
    return Promise.resolve(fallback());
    function fallback() {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      area.remove();
      return ok;
    }
  };

  /** Read the names of the files in a zip from its central directory (to tell a study zip from a harness zip). */
  EK.zipNames = function (buffer) {
    var view = new DataView(buffer), names = [];
    for (var i = buffer.byteLength - 22; i >= Math.max(0, buffer.byteLength - 65557); i--) {
      if (view.getUint32(i, true) !== 0x06054b50) continue;
      var count = view.getUint16(i + 10, true), at = view.getUint32(i + 16, true);
      var decoder = new TextDecoder();
      for (var k = 0; k < count && at + 46 <= buffer.byteLength; k++) {
        if (view.getUint32(at, true) !== 0x02014b50) break;
        var nameLen = view.getUint16(at + 28, true), extra = view.getUint16(at + 30, true), comment = view.getUint16(at + 32, true);
        names.push(decoder.decode(new Uint8Array(buffer, at + 46, nameLen)));
        at += 46 + nameLen + extra + comment;
      }
      break;
    }
    return names;
  };
})();
