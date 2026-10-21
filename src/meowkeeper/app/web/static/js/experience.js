(() => {
  const nav = document.getElementById("primary-nav");
  const toggle = document.getElementById("nav-toggle");
  const collapseBtn = document.getElementById("sidebar-collapse");
  const layout = document.getElementById("app-layout") || document.querySelector(".layout");
  const SIDEBAR_STORAGE_KEY = "meowkeeper-sidebar-collapsed";

  function applySidebarCollapsed(collapsed) {
    if (!layout || !collapseBtn) return;
    layout.classList.toggle("sidebar-collapsed", collapsed);
    collapseBtn.setAttribute("aria-expanded", String(!collapsed));
    collapseBtn.setAttribute("aria-label", collapsed ? "展開側邊導覽列" : "收合側邊導覽列");
    const iconSpan = collapseBtn.querySelector("span");
    if (iconSpan) iconSpan.textContent = collapsed ? "»" : "«";
  }

  if (collapseBtn && layout) {
    let savedCollapsed = false;
    try { savedCollapsed = localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true"; } catch (_) {}
    applySidebarCollapsed(savedCollapsed);
    collapseBtn.addEventListener("click", () => {
      const next = !layout.classList.contains("sidebar-collapsed");
      applySidebarCollapsed(next);
      try { localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next)); } catch (_) {}
    });
  }

  if (nav && toggle) {
    const mq = window.matchMedia("(max-width: 900px)");
    const syncNav = () => {
      if (mq.matches) {
        toggle.hidden = false;
        const expanded = toggle.getAttribute("aria-expanded") !== "false";
        nav.hidden = !expanded;
        toggle.textContent = expanded ? "收合選單" : "展開選單";
      } else {
        toggle.hidden = true;
        nav.hidden = false;
        toggle.setAttribute("aria-expanded", "true");
      }
    };
    toggle.setAttribute("aria-expanded", mq.matches ? "false" : "true");
    syncNav();
    toggle.addEventListener("click", () => {
      const next = toggle.getAttribute("aria-expanded") !== "true";
      toggle.setAttribute("aria-expanded", String(next));
      syncNav();
    });
    mq.addEventListener?.("change", syncNav);
  }

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const submit = form.querySelector('button[type="submit"]:not([disabled])');
      if (!submit || submit.dataset.busyApplied) return;
      submit.dataset.busyApplied = "true";
      submit.dataset.originalLabel = submit.textContent;
      submit.textContent = "處理中…";
      window.setTimeout(() => {
        submit.textContent = submit.dataset.originalLabel || submit.textContent;
        delete submit.dataset.busyApplied;
      }, 2500);
    });
  });
})();
