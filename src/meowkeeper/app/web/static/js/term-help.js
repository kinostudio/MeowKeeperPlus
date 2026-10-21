(() => {
  let seq = 0;
  const closeTimers = new WeakMap();
  function cardOf(root) { return root.querySelector(".term-help-card"); }
  function buttonOf(root) { return root.querySelector(".term-help-trigger"); }
  function clearTimer(root) {
    const t = closeTimers.get(root);
    if (t) { clearTimeout(t); closeTimers.delete(root); }
  }
  function closeAll(except = null) {
    document.querySelectorAll(".term-help").forEach((root) => {
      if (root === except) return;
      clearTimer(root);
      const button = buttonOf(root), card = cardOf(root);
      if (button) button.setAttribute("aria-expanded", "false");
      if (card) card.hidden = true;
    });
  }
  function openRoot(root) {
    clearTimer(root);
    const button = buttonOf(root), card = cardOf(root);
    if (!button || !card) return;
    closeAll(root);
    button.setAttribute("aria-expanded", "true");
    card.hidden = false;
  }
  function scheduleClose(root) {
    clearTimer(root);
    closeTimers.set(root, setTimeout(() => {
      const button = buttonOf(root), card = cardOf(root);
      if (button) button.setAttribute("aria-expanded", "false");
      if (card) card.hidden = true;
    }, 140));
  }
  document.querySelectorAll(".term-help").forEach((root) => {
    const button = buttonOf(root), card = cardOf(root);
    if (!button || !card) return;
    seq += 1;
    card.id = `term-help-card-${seq}`;
    button.setAttribute("aria-controls", card.id);
    root.addEventListener("mouseenter", () => openRoot(root));
    root.addEventListener("mouseleave", () => scheduleClose(root));
    button.addEventListener("focus", () => openRoot(root));
    button.addEventListener("blur", () => scheduleClose(root));
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = card.hidden;
      if (open) openRoot(root);
      else {
        clearTimer(root);
        button.setAttribute("aria-expanded", "false");
        card.hidden = true;
      }
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".term-help")) closeAll();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    closeAll();
  });
})();
