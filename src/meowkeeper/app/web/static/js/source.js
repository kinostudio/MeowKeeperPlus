(function () {
  "use strict";
  const form = document.getElementById("source-config-form");
  if (!form) return;
  const type = document.getElementById("source-type");
  const url = document.getElementById("source-url");
  const height = document.getElementById("youtube-height");
  const video = document.getElementById("youtube-video");
  const audio = document.getElementById("youtube-audio");
  const urlLabel = document.getElementById("source-url-label");
  const heightLabel = document.getElementById("youtube-height-label");
  const message = document.getElementById("source-message");
  const runtime = document.getElementById("source-runtime-status");
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  let rememberedSources = {};
  let previousType = type.value;
  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };
  const request = async (path, options = {}) => {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "來源操作失敗。");
    return data;
  };
  function renderFields() {
    const selected = type.value;
    const needsUrl = selected !== "mock";
    urlLabel.hidden = !needsUrl;
    url.required = needsUrl;
    const isYoutube = selected === "youtube";
    heightLabel.hidden = !isYoutube;
    video.closest("label").hidden = !isYoutube;
    audio.closest("label").hidden = !isYoutube;
    const optionsHelp = document.getElementById("youtube-options-help");
    if (optionsHelp) optionsHelp.hidden = !isYoutube;
    if (selected === "rtsp") url.placeholder = "rtsp://192.168.x.x:554/串流路徑（帳密請用環境變數）";
    else if (selected === "video") url.placeholder = "C:\\path\\to\\test.mp4";
    else url.placeholder = "https://www.youtube.com/watch?v=…";
  }
  async function refresh() {
    const [config, status] = await Promise.all([
      request("/api/v1/source/config", { headers: { Accept: "application/json" } }),
      request("/api/v1/status", { headers: { Accept: "application/json" } }),
    ]);
    const source = config.source || {};
    rememberedSources = source.remembered_sources || {};
    type.value = source.source_type || "mock";
    previousType = type.value;
    url.value = source.url || "";
    height.value = String(source.max_height || 720);
    video.checked = source.video !== false;
    audio.checked = source.audio === true;
    renderFields();
    const runtimeSource = status.metrics && status.metrics.source ? status.metrics.source : {};
    runtime.textContent = `核心：${status.status || "unknown"}｜來源：${runtimeSource.status || "not_checked"}｜連線：${runtimeSource.connections || 0}`;
    return status;
  }
  type.addEventListener("change", () => {
    // Preserve the unsaved form too, so browsing options never loses input.
    rememberedSources[previousType] = {
      url: url.value, max_height: Number(height.value), video: video.checked, audio: audio.checked,
    };
    const remembered = rememberedSources[type.value] || {};
    url.value = remembered.url || "";
    if (type.value === "youtube") {
      height.value = String(remembered.max_height || 720);
      video.checked = remembered.video !== false;
      audio.checked = remembered.audio === true;
    }
    previousType = type.value;
    renderFields();
  });
  async function saveConfig() {
    const data = await request("/api/v1/source/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
      body: JSON.stringify({ source_type: type.value, url: url.value, max_height: Number(height.value), video: video.checked, audio: audio.checked, reauth: true }),
    });
    // Saving is the critical operation. A transient status refresh failure
    // must not prevent the following start request from reaching the Host.
    try { await refresh(); } catch (_) { /* the next runtime poll will retry */ }
    return data;
  }
  async function waitForRuntime() {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      const status = await refresh();
      const source = status.metrics && status.metrics.source ? status.metrics.source : {};
      if (Number(source.connections || 0) > 0 || ["error", "not_configured"].includes(source.status)) return;
      await sleep(1000);
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await saveConfig();
      message.textContent = data.message_zh_tw || "來源設定已保存；請開始監控。";
    } catch (error) { message.textContent = `來源設定未保存：${error.message || "請確認登入狀態。"}`; }
  });
  document.getElementById("start-source").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await saveConfig();
      const data = await request("/api/v1/monitor/start", { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({}) });
      message.textContent = data.message_zh_tw || "監控啟動中；來源解析可能需要數秒。";
      waitForRuntime().catch(() => {});
    } catch (error) { message.textContent = `監控未啟動：${error.message || "來源設定未保存。"}`; }
    finally { button.disabled = false; }
  });
  document.getElementById("stop-source").addEventListener("click", async () => {
    if (!window.confirm("確認停止目前監控？停止後不會建立新的來源連線。")) return;
    try {
      const data = await request("/api/v1/monitor/stop", { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ confirm: true }) });
      message.textContent = data.message_zh_tw || "監控已停止。";
      await refresh();
    } catch (error) { message.textContent = `監控未停止：${error.message || "請確認登入狀態。"}`; }
  });
  renderFields();
  refresh().catch((error) => { message.textContent = error.message || "需要登入後才能讀取來源設定。"; });
})();
