/* Only the two monitoring views share a document and one live-media session. */
(() => {
  "use strict";
  const views = new Map([...document.querySelectorAll("[data-monitor-view]")]
    .map(view => [view.dataset.monitorView, view]));
  if (!views.has("overview") || !views.has("live")) return;
  const scopeFor = path => path === "/live" ? "live"
    : ["/", "/dashboard"].includes(path) ? "overview" : null;
  let scope = scopeFor(location.pathname);

  function show(url, { push = false, focus = false } = {}) {
    const next = scopeFor(url.pathname);
    if (!next || url.origin !== location.origin || url.search) return false;
    const previous = scope;
    scope = next;
    for (const [key, view] of views) {
      view.hidden = key !== scope;
      view.inert = key !== scope;
    }
    document.querySelectorAll("#primary-nav a, .brand").forEach(link => {
      const active = scopeFor(new URL(link.href, location.href).pathname) === scope;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    document.title = `${scope === "live" ? "即時畫面" : "總覽"}｜喵喵守護者+`;
    if (push) history.pushState(null, "", url.pathname + url.hash);
    document.dispatchEvent(new CustomEvent("meowkeeper:monitor-view", {
      detail: { scope, previous, path: url.pathname }
    }));
    if (focus) document.getElementById("main-content")?.focus({ preventScroll: true });
    if (url.hash) {
      let id;
      try { id = decodeURIComponent(url.hash.slice(1)); } catch (_) { return true; }
      const target = document.getElementById(id);
      if (target && views.get(scope).contains(target)) target.scrollIntoView();
    } else if (focus) window.scrollTo({ top: 0, behavior: "instant" });
    return true;
  }

  document.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest?.("a[href]");
    if (!link || link.hasAttribute("download") || (link.target && link.target !== "_self")
        || link.getAttribute("href").startsWith("#")) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.search || !scopeFor(url.pathname)) return;
    event.preventDefault();
    show(url, { push: url.href !== location.href, focus: true });
  });
  window.addEventListener("popstate", () => show(new URL(location.href)));
  window.MeowKeeperMonitorNavigation = { activeScope: () => scope };
  show(new URL(location.href));
})();
