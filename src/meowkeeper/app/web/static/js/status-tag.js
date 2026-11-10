/* Top-right runtime status tag + first-use guide. Uses the status payload app.js already polls; no extra requests. */
(() => {
  "use strict";
  const FRESH_MS = 15000;

  // Four states only: ok 正常 / warn 注意 / alert 需要處理 / off 未啟用.
  function classify(payload) {
    if (!payload || !payload.status) return { state: "alert", label: "無法連線服務", detail: "頁面暫時讀不到背景服務狀態；請確認程式仍在執行。" };
    const metrics = payload.metrics || {};
    const source = metrics.source || {};
    const ai = metrics.ai || {};
    if (payload.status === "starting") return { state: "warn", label: "正在啟動", detail: "監控正在啟動，稍候會開始接收畫面。" };
    if (payload.status !== "running") return { state: "off", label: "未監控", detail: "目前沒有在監控；按「啟動監控」開始。" };
    const checked = Date.parse(payload.checked_at || "");
    const age = source.last_frame_at ? (Number.isFinite(checked) ? checked : Date.now()) - Date.parse(source.last_frame_at) : Infinity;
    const fresh = ["running", "streaming", "connected"].includes(source.status) && Number.isFinite(age) && age >= 0 && age < FRESH_MS;
    if (!fresh) return { state: "alert", label: "沒有收到畫面", detail: "監控已啟動，但攝影機畫面中斷或尚未送達；請到「來源與攝影機」檢查。" };
    if (!(Number(ai.fps || 0) > 0)) return { state: "warn", label: "AI 尚未分析", detail: "畫面正常，但 AI 尚未開始辨識；可能仍在載入模型或因負載保護暫停。" };
    return { state: "ok", label: "監控中", detail: `畫面與 AI 都在運作（AI ${ai.fps} FPS）。` };
  }

  function renderTag(payload) {
    const tag = document.getElementById("runtime-status-tag");
    if (!tag) return;
    const result = classify(payload);
    tag.dataset.state = result.state;
    tag.querySelector(".status-tag-text").textContent = result.label;
    tag.title = result.detail;
    tag.setAttribute("aria-label", `目前運作狀態：${result.label}。${result.detail}`);
    const hero = document.getElementById("home-status-hero");
    if (hero) {
      hero.dataset.state = result.state;
      const heroLabel = { ok: "一切正常，正在守護", warn: "需要留意", alert: "需要處理", off: "目前未啟用監控" }[result.state];
      document.getElementById("home-status-label").textContent = `${heroLabel}・${result.label}`;
      document.getElementById("home-status-detail").textContent = result.detail;
    }
  }

  const GUIDE_KEY = "meowkeeper.guide.dismissed";
  function storage(action, value) {
    try {
      if (action === "get") return window.localStorage.getItem(GUIDE_KEY);
      if (action === "set") window.localStorage.setItem(GUIDE_KEY, value);
      if (action === "clear") window.localStorage.removeItem(GUIDE_KEY);
    } catch (_error) { /* Storage can be unavailable; the guide simply stays visible. */ }
    return null;
  }

  function guideChecks(payload) {
    const metrics = (payload && payload.metrics) || {};
    const source = metrics.source || {};
    return {
      camera: Boolean(source.last_frame_at) || ["running", "streaming", "connected"].includes(source.status),
      monitor: payload && payload.status === "running",
      notify: Boolean((metrics.telegram || {}).enabled),
    };
  }

  function renderGuide(payload) {
    const guide = document.getElementById("home-guide");
    if (!guide) return;
    const checks = guideChecks(payload);
    let done = 0;
    guide.querySelectorAll("[data-guide-check]").forEach((step) => {
      const ok = Boolean(checks[step.dataset.guideCheck]);
      step.dataset.done = String(ok);
      if (ok) done += 1;
    });
    const counter = guide.querySelector("#home-guide-progress");
    const total = guide.querySelectorAll("[data-guide-check]").length;
    if (counter) counter.textContent = `已完成 ${done}／${total} 項自動檢查`;
  }

  function wireGuide() {
    const guide = document.getElementById("home-guide");
    if (new URLSearchParams(window.location.search).has("guide")) storage("clear");
    if (guide && storage("get") === "1") guide.hidden = true;
    document.getElementById("home-guide-dismiss")?.addEventListener("click", () => {
      storage("set", "1");
      if (guide) guide.hidden = true;
    });
    document.querySelectorAll("[data-guide-reopen]").forEach((button) => button.addEventListener("click", (event) => {
      storage("clear");
      if (guide) {
        event.preventDefault();
        guide.hidden = false;
        guide.scrollIntoView({ behavior: "smooth", block: "start" });
        guide.querySelector("h3")?.focus();
      }
    }));
  }

  document.addEventListener("meowkeeper:status", (event) => {
    const payload = event.detail && event.detail.status ? event.detail : null;
    renderTag(payload);
    renderGuide(payload);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wireGuide, { once: true });
  else wireGuide();
  window.MeowKeeperStatusTag = { classify };
})();
