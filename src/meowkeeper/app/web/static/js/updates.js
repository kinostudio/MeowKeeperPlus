(() => {
  "use strict";
  const check = document.getElementById("update-check"), prepare = document.getElementById("update-prepare");
  if (!check || !prepare) return;
  const message = document.getElementById("update-message"), command = document.getElementById("update-command");
  const progressRegion = document.getElementById("update-progress-region");
  const progress = document.getElementById("update-progress");
  const progressLabel = document.getElementById("update-progress-label");
  let timer;

  function formatBytes(value) {
    if (!Number.isFinite(value) || value < 0) return "—";
    if (value < 1000) return `${Math.round(value)} B`;
    const units = ["MB", "GB", "TB"];
    let amount = value / 1000, unit = 0;
    while (amount >= 1000 && unit < units.length - 1) { amount /= 1000; unit += 1; }
    return `${amount.toFixed(amount >= 100 ? 0 : amount >= 10 ? 1 : 2)} ${units[unit]}`;
  }

  function showProgress(data) {
    const active = data.status === "downloading";
    if (progressRegion) progressRegion.hidden = !active;
    if (!active || !progress || !progressLabel) return;

    const percent = Number.isFinite(data.progress_percent) ? data.progress_percent : null;
    progress.max = 100;
    if (percent === null) progress.removeAttribute("value");
    else progress.value = percent;

    const pieces = [data.stage_label_zh_tw || "準備更新"];
    if (percent !== null) pieces.push(`${percent.toFixed(percent % 1 ? 1 : 0)}%`);
    if (Number.isFinite(data.downloaded_bytes) && Number.isFinite(data.total_bytes) && data.total_bytes > 0) {
      pieces.push(`已下載 ${formatBytes(data.downloaded_bytes)} / ${formatBytes(data.total_bytes)}`);
    }
    if (Number.isInteger(data.part_index) && Number.isInteger(data.part_count)) {
      pieces.push(`第 ${data.part_index} / ${data.part_count} 卷`);
    }
    pieces.push("監控持續");
    progressLabel.textContent = pieces.join(" · ");
  }

  async function load() {
    try {
      const response = await fetch("/api/v1/updates", {cache: "no-store"});
      if (!response.ok) throw new Error();
      const data = await response.json();
      message.textContent = data.message_zh_tw;
      showProgress(data);
      prepare.disabled = data.status !== "available";
      check.disabled = ["checking", "downloading"].includes(data.status);
      command.hidden = !data.install_command;
      command.textContent = data.install_command || "";
      if (check.disabled) timer = setTimeout(load, 2000);
    } catch (_) {
      message.textContent = "更新狀態讀取失敗，請確認已登入。";
      if (progressRegion) progressRegion.hidden = true;
    }
  }
  async function act(action) {
    clearTimeout(timer); check.disabled = true; prepare.disabled = true;
    const cookie = document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("meowkeeper_csrf="));
    try {
      const response = await fetch(`/api/v1/updates/${action}`, {method: "POST", headers: {"X-MeowKeeper-CSRF": cookie ? decodeURIComponent(cookie.substring("meowkeeper_csrf=".length)) : ""}});
      if (!response.ok) throw new Error();
      message.textContent = "正在處理更新要求…";
      timer = setTimeout(load, 1000);
    } catch (_) { message.textContent = "更新要求未完成，請重新登入後再試。"; check.disabled = false; }
  }
  check.addEventListener("click", () => act("check"));
  prepare.addEventListener("click", () => act("prepare"));

  const hotfixBtn = document.getElementById("update-hotfix-github");
  if (hotfixBtn) {
    hotfixBtn.addEventListener("click", async () => {
      if (!window.confirm("確定從 GitHub 拉取最新原始碼並平滑重載嗎？\n更新僅需數秒，Dashboard 會短暫離線重啟。")) return;
      hotfixBtn.disabled = true;
      const cookie = document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("meowkeeper_csrf="));
      const csrf = cookie ? decodeURIComponent(cookie.substring("meowkeeper_csrf=".length)) : "";
      try {
        message.textContent = "正在發送 GitHub 熱修復指令…";
        const response = await fetch("/api/v1/updates/hotfix-github", {
          method: "POST",
          headers: { "X-MeowKeeper-CSRF": csrf, "Content-Type": "application/json" },
          body: JSON.stringify({ reauth: true })
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || "伺服器回應錯誤");
        }
        message.textContent = (data.message_zh_tw || "熱修復已啟動！") + " 正在重新整理連線…";
        setTimeout(() => {
          let attempts = 0;
          const poll = setInterval(async () => {
            attempts++;
            try {
              const res = await fetch("/api/v1/health", { cache: "no-store" });
              if (res.ok) {
                clearInterval(poll);
                message.textContent = "✅ 熱修復已成功套用，服務已重啟！正在重載頁面…";
                setTimeout(() => window.location.reload(), 1500);
              }
            } catch (_) {
              if (attempts > 30) {
                clearInterval(poll);
                message.textContent = "服務重啟耗時較長，請稍候手動重新整理頁面。";
                hotfixBtn.disabled = false;
              }
            }
          }, 1500);
        }, 3000);
      } catch (err) {
        message.textContent = "GitHub 熱修復啟動失敗：" + (err.message || "未知錯誤");
        hotfixBtn.disabled = false;
      }
    });
  }

  window.addEventListener("pagehide", () => clearTimeout(timer));
  load();
})();
