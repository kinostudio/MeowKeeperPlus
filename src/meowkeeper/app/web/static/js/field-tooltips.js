/* Field help as hover/focus/tap tooltips: keeps forms short, text stays linked for screen readers. */
(() => {
  "use strict";
  let sequence = 0;
  let pinned = null;

  function close(anchor) {
    if (!anchor) return;
    anchor.classList.remove("open");
    anchor.querySelector(".field-tip-button")?.setAttribute("aria-expanded", "false");
    if (pinned === anchor) pinned = null;
  }

  function open(anchor) {
    anchor.classList.add("open");
    anchor.querySelector(".field-tip-button")?.setAttribute("aria-expanded", "true");
    const tip = anchor.querySelector(".field-tip");
    if (!tip) return;
    // Keep the bubble inside the viewport on narrow screens.
    tip.style.removeProperty("--tip-shift");
    const box = tip.getBoundingClientRect();
    const overflowRight = box.right - (window.innerWidth - 8);
    const overflowLeft = 8 - box.left;
    if (overflowRight > 0) tip.style.setProperty("--tip-shift", `${-overflowRight}px`);
    else if (overflowLeft > 0) tip.style.setProperty("--tip-shift", `${overflowLeft}px`);
  }

  function enhance(root = document) {
    root.querySelectorAll("label > small, label > .field-help").forEach((help) => {
      if (help.dataset.tipReady === "1") return;
      const label = help.parentElement;
      const text = help.textContent.trim();
      if (!text || label.classList.contains("checkbox-line")) return;
      help.dataset.tipReady = "1";
      if (!help.id) help.id = `field-tip-${++sequence}`;
      const control = label.querySelector("input, select, textarea");
      if (control) {
        const ids = (control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);
        if (!ids.includes(help.id)) control.setAttribute("aria-describedby", [...ids, help.id].join(" "));
      }
      const anchor = document.createElement("span");
      anchor.className = "field-tip-anchor";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "field-tip-button";
      button.textContent = "i";
      button.setAttribute("aria-label", "這是什麼？");
      button.setAttribute("aria-expanded", "false");
      button.setAttribute("aria-controls", help.id);
      help.classList.add("field-tip");
      help.setAttribute("role", "tooltip");
      // Keep the label words and the ⓘ on one line: wrap the text before the first control.
      const title = document.createElement("span");
      title.className = "field-tip-title";
      const leading = [];
      for (const node of Array.from(label.childNodes)) {
        if (node === help) continue;
        if (node.nodeType === Node.ELEMENT_NODE && node.matches("input, select, textarea, output, .seg, .slider-row, div, fieldset")) break;
        leading.push(node);
      }
      label.insertBefore(title, leading[0] || label.firstChild);
      leading.forEach((node) => title.append(node));
      title.append(anchor);
      anchor.append(button, help);
      anchor.addEventListener("mouseenter", () => open(anchor));
      anchor.addEventListener("mouseleave", () => { if (pinned !== anchor) close(anchor); });
      button.addEventListener("focus", () => open(anchor));
      button.addEventListener("blur", () => { if (pinned !== anchor) close(anchor); });
      button.addEventListener("click", (event) => {
        // A button inside <label> must not toggle the label's control.
        event.preventDefault();
        event.stopPropagation();
        if (pinned === anchor) { close(anchor); return; }
        close(pinned);
        pinned = anchor;
        open(anchor);
      });
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && pinned) { const anchor = pinned; close(anchor); anchor.querySelector("button")?.focus(); }
  });
  document.addEventListener("click", (event) => {
    if (pinned && !pinned.contains(event.target)) close(pinned);
  });
  const start = () => {
    enhance();
    // Forms rendered later by page scripts get the same treatment.
    new MutationObserver((records) => {
      if (records.some((record) => record.addedNodes.length)) enhance();
    }).observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
  else start();
  window.MeowKeeperFieldTips = { enhance };
})();
