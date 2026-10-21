(function () {
  "use strict";
  const form = document.getElementById("lan-settings-form");
  if (!form) return;
  const enabled = document.getElementById("lan-access-enabled");
  const port = document.getElementById("dashboard-port");
  const urls = document.getElementById("lan-url-list");
  const message = document.getElementById("lan-restart-message");
  const firewallStatus = document.getElementById("lan-firewall-status");
  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };
  let latest = null;
  function render(data) {
    latest = data;
    enabled.checked = Boolean(data.lan_access_enabled);
    port.value = data.configured_port || data.port || 8000;
    const values = Array.isArray(data.urls) ? data.urls : [];
    urls.textContent = values.length ? `可用區網網址：${values.join("、")}` : `目前僅本機：${data.local_url || "未設定"}`;
    const firewall = data.firewall || {};
    firewallStatus.textContent = `防火牆：${firewall.message_zh_tw || firewall.status || "尚未確認"}`;
    message.textContent = data.restart_required ? "設定已保存；請重新啟動 Dashboard 才會套用新的 bind 或 Port。" : (data.message_zh_tw || "");
  }
  async function load() {
    const response = await fetch("/api/v1/network/dashboard", { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error("需要登入後才能讀取區網設定。");
    render(await response.json());
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.confirm("確認保存區網 Dashboard 設定？套用 bind 或 Port 必須重新啟動 Dashboard。")) return;
    const response = await fetch("/api/v1/network/dashboard", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
      body: JSON.stringify({ lan_access_enabled: enabled.checked, dashboard_port: Number(port.value), reauth: true }),
    });
    const data = await response.json();
    if (!response.ok) { message.textContent = data.detail || "區網設定未保存。"; return; }
    render(data.network || {});
    message.textContent = data.status === "restart_required" ? "已保存；請重新啟動 Dashboard 後讓手機或平板連線。" : "設定已套用。";
  });
  document.querySelectorAll("[data-firewall-action]").forEach((button) => button.addEventListener("click", async () => {
    if (!window.confirm("確認變更 Windows Private Profile 防火牆規則？")) return;
    const response = await fetch("/api/v1/network/firewall", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
      body: JSON.stringify({ action: button.dataset.firewallAction, reauth: true }),
    });
    const data = await response.json();
    firewallStatus.textContent = (data.firewall && data.firewall.message_zh_tw) || data.message_zh_tw || data.status || "尚未確認";
    await load().catch(() => {});
  }));
  document.getElementById("copy-lan-url").addEventListener("click", async () => {
    const value = latest && Array.isArray(latest.urls) && latest.urls[0];
    if (!value) { message.textContent = "目前沒有可複製的區網網址。"; return; }
    try { await navigator.clipboard.writeText(value); message.textContent = "已複製區網網址。"; }
    catch (_) { message.textContent = `請手動複製：${value}`; }
  });
  load().catch((error) => { message.textContent = error.message; });
})();

(function () {
  "use strict";
  const password = document.getElementById("full-backup-password");
  const fileInput = document.getElementById("full-backup-file");
  const message = document.getElementById("full-backup-message");
  if (!password || !fileInput || !message) return;
  const progressRegion = document.getElementById("full-backup-progress-region");
  const progress = document.getElementById("full-backup-progress");
  const progressLabel = document.getElementById("full-backup-progress-label");
  const itemList = document.getElementById("full-backup-progress-items");
  const exportButton = document.getElementById("export-full-backup");
  const importButton = document.getElementById("import-full-backup");
  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };
  const delay = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));
  const formatBytes = (value) => {
    if (!Number.isFinite(Number(value)) || Number(value) < 0) return "";
    const mb = Number(value) / (1024 * 1024);
    return `${mb.toLocaleString("zh-TW", { maximumFractionDigits: 1 })} MB`;
  };
  function renderProgress(data) {
    progressRegion.hidden = false;
    const percent = Math.max(0, Math.min(100, Number(data.percent) || 0));
    progress.value = percent;
    const count = data.total > 0 ? ` · ${data.completed || 0}/${data.total} 項` : "";
    const item = data.current_item_label ? ` · 目前：${data.current_item_label}` : "";
    const byteCount = data.bytes_total > 0
      ? ` · ${formatBytes(data.bytes_done || 0)} / ${formatBytes(data.bytes_total)}` : "";
    progressLabel.textContent = `${data.phase_zh_tw || "正在處理…"}${count}${item}${byteCount} · ${percent}%`;

    const items = Array.isArray(data.items) ? data.items : [];
    const fragment = document.createDocumentFragment();
    const omitted = Number(data.items_omitted) || 0;
    if (omitted > 0 || items.length > 40) {
      const summary = document.createElement("li");
      const hiddenCount = omitted + Math.max(0, items.length - 40);
      summary.textContent = `前面另有 ${hiddenCount} 個已完成項目；下方顯示最近 40 項。`;
      fragment.appendChild(summary);
    }
    items.slice(-40).forEach((entry) => {
      const row = document.createElement("li");
      const countText = entry.total > 0 ? ` ${entry.completed}/${entry.total}` : "";
      row.textContent = `${entry.phase_zh_tw || "已完成"} · ${entry.label || "資料項目"}${countText}`;
      fragment.appendChild(row);
    });
    itemList.replaceChildren(fragment);
    if (data.status === "failed") {
      progressLabel.textContent = `失敗階段：${data.failed_during_zh_tw || data.phase_zh_tw || "備份處理"}；原因：${data.error_reason_zh_tw || "未能分類的錯誤，請檢查本機診斷記錄。"}`;
    }
  }
  function setBusy(value) {
    exportButton.disabled = value;
    importButton.disabled = value;
  }
  function validPassword() {
    if (password.value.length >= 6) return true;
    message.textContent = "請輸入至少 6 個字元的備份密碼。";
    return false;
  }
  async function startJob(url, options) {
    const response = await fetch(url, { ...options, cache: "no-store" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "無法開始備份工作；請確認登入狀態及磁碟空間。 ");
    if (!data.job_id) throw new Error("伺服器未提供備份工作編號，請查看本機診斷記錄。");
    return data.job_id;
  }
  async function waitForJob(jobId) {
    let transientFailures = 0;
    while (true) {
      await delay(500);
      try {
        const response = await fetch(`/api/v1/settings/backup/jobs/${encodeURIComponent(jobId)}`, {
          headers: { Accept: "application/json" }, cache: "no-store",
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || "備份進度暫時無法讀取。");
        transientFailures = 0;
        renderProgress(data);
        if (data.status === "completed") return data;
        if (data.status === "failed") {
          throw new Error(`階段：${data.failed_during_zh_tw || "備份處理"}；${data.error_reason_zh_tw || "請檢查本機診斷記錄。"}`);
        }
      } catch (error) {
        transientFailures += 1;
        if (transientFailures >= 4) throw error;
      }
    }
  }
  function uploadImport(url, body, file) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", url, true);
      xhr.setRequestHeader("X-MeowKeeper-CSRF", csrfToken());
      xhr.responseType = "json";
      xhr.upload.onprogress = (event) => {
        if (!event.lengthComputable) {
          renderProgress({ status: "running", percent: 0, phase_zh_tw: "正在上傳備份 ZIP" });
          return;
        }
        const percent = Math.min(8, Math.round((event.loaded / event.total) * 8));
        renderProgress({
          status: "running", percent, phase_zh_tw: "正在上傳備份 ZIP",
          bytes_done: Math.min(event.loaded, file.size), bytes_total: file.size,
        });
      };
      xhr.onload = () => {
        const data = xhr.response || {};
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(data.detail || "備份上傳失敗；請確認網路、檔案及本機磁碟空間。"));
          return;
        }
        resolve(data);
      };
      xhr.onerror = () => reject(new Error("上傳連線中斷；請確認 Dashboard 仍在執行後重試。"));
      xhr.onabort = () => reject(new Error("備份上傳已中止；尚未開始還原。"));
      xhr.send(body);
    });
  }
  async function downloadWithProgress(jobId) {
    const response = await fetch(`/api/v1/settings/backup/jobs/${encodeURIComponent(jobId)}/download`, {
      headers: { Accept: "application/zip" }, cache: "no-store",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.detail || "備份 ZIP 下載失敗；請重新匯出。 ");
    }
    const total = Number(response.headers.get("Content-Length")) || 0;
    let blob;
    if (response.body && response.body.getReader) {
      const reader = response.body.getReader();
      const chunks = [];
      let received = 0;
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        chunks.push(part.value);
        received += part.value.byteLength;
        const percent = total > 0 ? 98 + Math.min(2, (received / total) * 2) : 98;
        renderProgress({
          status: "running", percent, phase_zh_tw: "正在下載加密 ZIP",
          bytes_done: received, bytes_total: total || null,
        });
      }
      blob = new Blob(chunks, { type: "application/zip" });
    } else {
      blob = await response.blob();
      renderProgress({ status: "running", percent: 100, phase_zh_tw: "ZIP 下載完成" });
    }
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = "MeowKeeperPlus-full-backup.zip";
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  }
  exportButton.addEventListener("click", async () => {
    if (!validPassword()) return;
    setBusy(true);
    progressRegion.hidden = false;
    itemList.replaceChildren();
    progress.value = 0;
    progressLabel.textContent = "正在排入完整備份工作…";
    message.textContent = "匯出期間可留在此頁；逐項進度會顯示在下方。";
    try {
      const jobId = await startJob("/api/v1/settings/backup/full-export", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ password: password.value }),
      });
      const result = await waitForJob(jobId);
      renderProgress(result);
      message.textContent = "備份已完成，正在下載 ZIP…";
      await downloadWithProgress(jobId);
      progress.value = 100;
      progressLabel.textContent = "匯出完成 · ZIP 已下載";
      message.textContent = "完整資料 ZIP 已下載；請分開保存備份檔與密碼。";
    } catch (error) {
      progressRegion.hidden = false;
      progressLabel.textContent = `匯出失敗原因：${error.message}`;
      message.textContent = error.message;
    } finally { password.value = ""; setBusy(false); }
  });
  importButton.addEventListener("click", async () => {
    if (!validPassword()) return;
    const file = fileInput.files && fileInput.files[0];
    if (!file || !file.name.toLowerCase().endsWith(".zip")) { message.textContent = "請選擇完整資料 ZIP 備份。"; return; }
    if (!window.confirm("匯入會還原設定、SQLite 回饋、寵物資料與事件檔案。確定繼續？")) return;
    setBusy(true);
    progressRegion.hidden = false;
    itemList.replaceChildren();
    progress.value = 0;
    progressLabel.textContent = `準備上傳 ZIP · ${formatBytes(file.size)}`;
    message.textContent = "上傳後會逐項解壓、校驗及套用資料；請保持此頁開啟。";
    try {
      const body = new FormData();
      body.append("password", password.value);
      body.append("confirm", "true");
      body.append("file", file, file.name);
      const queued = await uploadImport("/api/v1/settings/backup/full-import", body, file);
      if (!queued.job_id) throw new Error("伺服器未提供匯入工作編號，請查看本機診斷記錄。");
      const result = await waitForJob(queued.job_id);
      progress.value = 100;
      progressLabel.textContent = result.message_zh_tw || "匯入完成。";
      message.textContent = result.message_zh_tw || "完整資料已還原；請重新啟動 Host。";
    } catch (error) {
      progressRegion.hidden = false;
      progressLabel.textContent = `匯入失敗原因：${error.message}`;
      message.textContent = error.message;
    } finally { password.value = ""; fileInput.value = ""; setBusy(false); }
  });
})();
