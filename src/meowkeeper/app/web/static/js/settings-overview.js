/* Settings overview: one plain-language status per area (ok / warn / alert / off). Reads existing APIs only. */
(() => {
  "use strict";
  if (!document.querySelector(".settings-overview")) return;
  const LABEL = { ok: "正常", warn: "注意", alert: "需要處理", off: "未啟用", pending: "確認中" };

  function setCard(key, state, label, detail) {
    const card = document.getElementById(`set-${key}`);
    if (!card) return;
    card.dataset.state = state;
    const chip = card.querySelector(".state-chip");
    chip.dataset.state = state;
    card.querySelector(".set-card-state").textContent = label || LABEL[state];
    card.querySelector(".set-card-detail").textContent = detail;
  }
  const gb = (bytes) => (bytes == null ? null : Math.round(bytes / 1e9 * 10) / 10);

  function fromStatus(payload) {
    const metrics = (payload && payload.metrics) || {};
    const source = metrics.source || {};
    const ai = metrics.ai || {};
    const telegram = metrics.telegram || {};
    const disk = payload.disk || metrics.disk || {};
    const gpu = metrics.gpu || {};

    const age = source.last_frame_at ? Date.now() - Date.parse(source.last_frame_at) : Infinity;
    const resolution = source.resolution ? `・${source.resolution}` : "";
    if (payload.status !== "running") setCard("camera", "off", "未監控", "監控沒有在執行；按右上角「啟動監控」開始。");
    else if (Number.isFinite(age) && age < 15000) setCard("camera", "ok", "有畫面", `正在收到攝影機畫面${resolution}`);
    else setCard("camera", "alert", "沒有畫面", "監控已啟動，但攝影機畫面中斷或還沒送到；請檢查來源設定。");

    if (!telegram.enabled) setCard("notify", "off", "未啟用", "還沒接上 Telegram；有事件時手機不會收到通知。");
    else if (Number(telegram.failure_count || 0) > 0 && telegram.status === "failed") setCard("notify", "alert", "沒送出", `最近傳送失敗；已成功送出 ${telegram.success_count || 0} 則。`);
    else setCard("notify", "ok", "送得到", `Telegram 已接上；這次啟動後已送出 ${telegram.success_count || 0} 則。`);

    const aiFps = Number(ai.fps || 0);
    const device = ai.device && ai.device !== "unknown" ? `${ai.device}・` : "";
    if (payload.status !== "running") setCard("ai", "off", "未監控", "監控啟動後才會開始找貓與錄影。");
    else if (aiFps > 0) setCard("ai", "ok", "運作中", `找貓：${device}每秒 ${aiFps} 張・事件錄影開啟`);
    else setCard("ai", "warn", "尚未分析", "畫面有了，但 AI 還沒開始找貓；可能在載入模型或因負載保護暫停。");

    const free = gb(disk.free_bytes);
    const temp = gpu.temperature_c == null ? "" : `・顯示卡 ${gpu.temperature_c}°C`;
    if (free == null) setCard("computer", "pending", "確認中", "正在讀取硬碟空間。");
    else if (free < 5) setCard("computer", "alert", "空間不足", `硬碟只剩 ${free} GB；事件影片可能無法保存。`);
    else if (free < 20) setCard("computer", "warn", "空間偏少", `硬碟還有 ${free} GB${temp}`);
    else setCard("computer", "ok", "正常", `硬碟還有 ${free} GB${temp}`);
  }

  async function getJson(url) {
    const response = await fetch(url, { cache: "no-store", credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(String(response.status));
    return response.json();
  }

  async function loadCats() {
    try {
      const pets = ((await getJson("/api/v1/pets")).items || []).filter((pet) => pet.enabled !== false);
      if (!pets.length) { setCard("cats", "warn", "還沒建立", "還沒有貓咪檔案；建立後事件裡才分得出是誰。"); return; }
      const few = pets.filter((pet) => Number(pet.reference_image_count || 0) < 3);
      const summary = pets.map((pet) => `${pet.display_name} ${pet.reference_image_count || 0} 張`).join("・");
      if (few.length) setCard("cats", "warn", "照片偏少", `${summary}；每隻至少 3 張照片會更準。`);
      else setCard("cats", "ok", `${pets.length} 隻`, summary);
    } catch (_error) {
      setCard("cats", "pending", "確認中", "暫時讀不到貓咪檔案。");
    }
  }

  async function loadRemote() {
    try {
      const data = await getJson("/api/v1/tailscale/status");
      const status = String(data.status || data.tailscale_status || "");
      if (["connected", "running", "online", "ok"].includes(status)) setCard("remote", "ok", "可遠端看", "已接上 Tailscale；出門也能用手機打開儀表板。");
      else if (["error", "failed"].includes(status)) setCard("remote", "alert", "連線異常", "Tailscale 回報錯誤；請到連線與安全查看。");
      else setCard("remote", "off", "未啟用", "目前只有這台電腦能開儀表板；要在外面用手機看，需要設定一次。");
    } catch (_error) {
      setCard("remote", "off", "未啟用", "目前只有這台電腦能開儀表板；要在外面用手機看，需要設定一次。");
    }
  }

  document.addEventListener("meowkeeper:status", (event) => { if (event.detail && event.detail.status) fromStatus(event.detail); });
  loadCats();
  loadRemote();
})();
