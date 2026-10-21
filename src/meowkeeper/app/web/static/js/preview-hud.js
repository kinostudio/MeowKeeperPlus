/* Reveal viewing controls on hover; keep keyboard and touch access available. */
(() => {
  "use strict";
  function mount(stage) {
    let timer = 0, hovered = false, closed = false;
    const touch = window.matchMedia("(hover: none)");
    const keyboardFocused = () => stage.contains(document.activeElement)
      && document.activeElement.matches(":focus-visible");
    stage.classList.add("preview-hud-auto-hide");
    const cancel = () => { clearTimeout(timer); timer = 0; };
    function show() {
      cancel();
      stage.dataset.previewHudVisible = "true";
    }
    function scheduleHide() {
      cancel();
      if (closed) return;
      if (touch.matches || hovered || keyboardFocused()) return;
      timer = setTimeout(() => {
        timer = 0;
        if (!closed && !hovered && !keyboardFocused()) {
          stage.dataset.previewHudVisible = "false";
        }
      }, 2000);
    }
    function enter() { hovered = true; show(); }
    function leave() { hovered = false; scheduleHide(); }
    function blur() { queueMicrotask(scheduleHide); }
    function deviceChanged() { if (touch.matches) show(); else scheduleHide(); }
    stage.addEventListener("pointerenter", enter);
    stage.addEventListener("pointerleave", leave);
    stage.addEventListener("focusin", show);
    stage.addEventListener("focusout", blur);
    touch.addEventListener("change", deviceChanged);
    hovered = stage.matches(":hover");
    stage.dataset.previewHudVisible = String(touch.matches || hovered || keyboardFocused());
    return { close() {
      closed = true;
      cancel();
      stage.removeEventListener("pointerenter", enter);
      stage.removeEventListener("pointerleave", leave);
      stage.removeEventListener("focusin", show);
      stage.removeEventListener("focusout", blur);
      touch.removeEventListener("change", deviceChanged);
      stage.classList.remove("preview-hud-auto-hide");
      delete stage.dataset.previewHudVisible;
    } };
  }
  window.MeowKeeperHud = { mount };
})();
