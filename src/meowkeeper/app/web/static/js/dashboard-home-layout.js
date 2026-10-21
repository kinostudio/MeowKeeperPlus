/* Keep the full shared camera preview beside the hardware summary on desktop. */
(() => {
  "use strict";
  const hero = document.querySelector(".dashboard-hero-grid");
  const hardware = document.querySelector(".dashboard-system");
  const preview = hero?.querySelector(".dashboard-preview-panel");
  const events = hero?.querySelector(".dashboard-events-panel");
  if (!hero || !hardware || !preview) return;

  if (!hero.contains(hardware)) {
    if (events) hero.insertBefore(hardware, events);
    else hero.append(hardware);
  }
  hero.dataset.layoutState = "ready";
})();
