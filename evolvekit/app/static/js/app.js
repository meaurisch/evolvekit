/* evolvekit app: start-up, the app bar, the router.
   Routes (hash):  #/  ·  #/start  ·  #/start/<slug>  ·  #/setup/<slug>  ·  #/study/<slug>[?run=&view=]
                   #/compare?a=&b=  ·  #/settings
   The old page's routes still work: #/new, #/study/<slug>/<1-7>, …/running, …/results. */
(function () {
  "use strict";
  var EK = window.EK, html = EK.html;
  EK.pages = EK.pages || {};
  var LAST_STUDY = "evolvekit.study";

  // ------------------------------------------------------------------ theme
  function shade() {
    var forced = document.documentElement.getAttribute("data-theme");
    var dark = forced ? forced === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-shade", dark ? "dark" : "light");
  }
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", shade);

  // ------------------------------------------------------------------ the route
  function parse() {
    var hash = location.hash.replace(/^#\/?/, "");
    var at = hash.indexOf("?");
    var path = (at >= 0 ? hash.slice(0, at) : hash).split("/").filter(Boolean).map(decodeURIComponent);
    var query = {};
    new URLSearchParams(at >= 0 ? hash.slice(at + 1) : "").forEach(function (v, k) { query[k] = v; });
    return { path: path, query: query };
  }
  /** Old routes to new ones, so bookmarks keep working. */
  function legacy(r) {
    var p = r.path;
    if (p[0] === "new") return "#/start";
    if (p[0] === "study" && p.length >= 3) {
      var slug = EK.q(p[1]), q = r.query.run ? "?run=" + EK.q(r.query.run) : "";
      if (/^[1-3]$/.test(p[2])) return "#/start/" + slug;
      if (/^[4-7]$/.test(p[2])) return "#/setup/" + slug;
      if (p[2] === "running" || p[2] === "results") return "#/study/" + slug + (p[3] ? "?run=" + EK.q(p[3]) : q);
    }
    return null;
  }
  EK.go = function (hash) {
    if (location.hash === hash) route();
    else location.hash = hash;
  };
  EK.studyHref = function (slug, run) { return "#/study/" + EK.q(slug) + (run ? "?run=" + EK.q(run) : ""); };
  EK.currentStudy = function () { try { return sessionStorage.getItem(LAST_STUDY) || ""; } catch (e) { return ""; } };
  EK.rememberStudy = function (slug) { try { sessionStorage.setItem(LAST_STUDY, slug); } catch (e) { /* not kept */ } };

  // ------------------------------------------------------------------ the app bar
  var NAV = [["home", "Home", "#/"], ["start", "Start a study", "#/start"], ["study", "Study", null]];
  function appBar(current) {
    var bar = document.getElementById("bar");
    var study = EK.currentStudy();
    var links = NAV.map(function (n) {
      var href = n[0] === "study" ? (study ? EK.studyHref(study) : null) : n[2];
      if (!href) return "";
      var on = n[0] === current;
      return html`<a class="nav-link${on ? " is-on" : ""}" href="${href}"${EK.attrs({ "aria-current": on ? "page" : null })}>${n[1]}</a>`;
    });
    var on = current === "settings";
    var settings = html`<a class="nav-link nav-settings${on ? " is-on" : ""}" href="#/settings"${EK.attrs({ "aria-current": on ? "page" : null })}>${EK.gl("gear", 20)}Settings</a>`;
    var here = { home: "Home", start: "Start a study", setup: "Set up", study: "Study", compare: "Compare", settings: "Settings" }[current] || "";
    EK.mount(bar, html`<a class="brand" href="#/">${EK.mark(24)}<span class="brand-word">evolvekit</span></a>
      <nav class="nav wide-only" aria-label="Pages">${links}${settings}</nav>
      <span class="type-small ink-2 bar-here phone-only">${here}</span>
      <div class="menu-wrap phone-only">${EK.iconButton("menu", "Pages", { act: "menu", arg: "pages-menu", haspopup: "menu", expanded: "false" })}
        <div class="menu" role="menu" aria-label="Pages" id="pages-menu" hidden>
          ${NAV.map(function (n) {
            var href = n[0] === "study" ? (study ? EK.studyHref(study) : null) : n[2];
            return href ? html`<a role="menuitem" class="menu-item" href="${href}">${n[1]}</a>` : "";
          })}<a role="menuitem" class="menu-item" href="#/settings">Settings</a></div></div>`, { menu: EK.toggleMenu });
  }

  // ------------------------------------------------------------------ menus: one open at a time, Escape closes, focus returns
  EK.toggleMenu = function (btn) {
    var menu = document.getElementById(btn.getAttribute("data-arg"));
    if (!menu) return;
    var open = menu.hidden;
    EK.closeMenus();
    if (!open) return;
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    menu._opener = btn;
    var first = menu.querySelector("[role=menuitem]");
    if (first) first.focus();
  };
  EK.closeMenus = function (restore) {
    EK.$$(".menu:not([hidden])").forEach(function (m) {
      m.hidden = true;
      if (m._opener) {
        m._opener.setAttribute("aria-expanded", "false");
        if (restore) m._opener.focus();
      }
    });
  };
  document.addEventListener("click", function (ev) {
    if (!ev.target.closest(".menu-wrap")) EK.closeMenus();
  });
  document.addEventListener("keydown", function (ev) {
    var menu = ev.target.closest && ev.target.closest(".menu");
    if (ev.key === "Escape") {
      if (document.querySelector(".menu:not([hidden])")) { EK.closeMenus(true); ev.preventDefault(); return; }
      if (EK.onEscape && EK.onEscape()) ev.preventDefault();
      return;
    }
    if (menu && (ev.key === "ArrowDown" || ev.key === "ArrowUp" || ev.key === "Home" || ev.key === "End")) {
      var items = EK.$$("[role=menuitem]", menu), i = items.indexOf(document.activeElement);
      var next = ev.key === "Home" ? 0 : ev.key === "End" ? items.length - 1 : (i + (ev.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
      items[next].focus();
      ev.preventDefault();
    }
    if (menu && ev.key === "Tab") EK.closeMenus();
  });

  // ------------------------------------------------------------------ the locked page
  EK.onLocked = function () {
    EK.clearTimers();
    document.getElementById("bar").innerHTML = String(html`<span class="brand">${EK.mark(24)}<span class="brand-word">evolvekit</span></span>`);
    EK.mount(document.getElementById("main"), EK.lockedPage());
    document.getElementById("companion").innerHTML = "";
    var drawer = document.getElementById("drawer");
    drawer.hidden = true;
    document.title = "evolvekit";
  };

  // ------------------------------------------------------------------ routing
  var generation = 0;
  function route() {
    if (!EK.hasToken()) return EK.onLocked();
    var r = parse(), moved = legacy(r);
    if (moved) { location.replace(moved); return; }
    EK.clearTimers();
    EK.hideUndo();
    EK.closeMenus();
    EK.onStudyChanged = null;
    EK.hoverEscape = null;
    if (EK.closeDrawer) EK.closeDrawer({ keepAssistant: true, quiet: true });
    var name = r.path[0] || "home";
    var page = { "": "home", home: "home", start: "start", setup: "setup", study: "study", compare: "compare", settings: "settings" }[name] || "home";
    var slug = r.path[1] || "";
    if (slug && (page === "study" || page === "setup" || page === "start")) EK.rememberStudy(slug);
    var main = document.getElementById("main");
    var mine = ++generation;
    EK.route = { page: page, slug: slug, query: r.query, generation: mine };
    appBar(page === "setup" ? "start" : page);
    main.setAttribute("aria-busy", "true");
    var handler = EK.pages[page];
    var done = function () {
      if (mine !== generation) return;
      main.removeAttribute("aria-busy");
      var title = EK.$("#page-title", main);
      if (EK._moveFocus !== false) (title || main).focus({ preventScroll: false });
      EK._moveFocus = true;
    };
    Promise.resolve().then(function () { return handler.render(main, slug, r.query, mine); }).then(done, function (err) {
      if (mine !== generation) return;
      main.removeAttribute("aria-busy");
      if (err && err.status === 403) return;
      EK.mount(main, html`<div class="page"><div class="page-inner">${EK.titleBlock("", "Something went wrong")}
        ${EK.notice("error", (err && err.message) || String(err))}<p>${EK.link("Back to your studies", { href: "#/" })}</p></div></div>`);
    });
    if (EK.companion) EK.companion.onRoute(page, slug);
  }
  /** True while the page that asked is still the one on screen. */
  EK.isCurrent = function (gen) { return gen === generation; };
  EK.rerender = function () { EK._moveFocus = false; route(); };

  window.addEventListener("hashchange", route);
  document.addEventListener("DOMContentLoaded", function () {
    shade();
    if (!location.hash) history.replaceState(null, "", location.pathname + location.search + "#/");
    if (EK.companion) EK.companion.start();
    route();
  });
})();
