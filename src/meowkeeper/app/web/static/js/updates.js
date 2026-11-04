(() => {
  "use strict";
  const check = document.getElementById("update-check"), prepare = document.getElementById("update-prepare");
  if (!check || !prepare) return;
  const apply = document.getElementById("update-apply");
  const dialog = document.getElementById("update-apply-dialog");
  const confirmApply = document.getElementById("update-apply-confirm");
  const message = document.getElementById("update-message"), command = document.getElementById("update-command");
  const progressRegion = document.getElementById("update-progress-region");
  const progress = document.getElementById("update-progress");
  const progressLabel = document.getElementById("update-progress-label");
  let timer, applyRequested = false;

  function formatBytes(value) {
    if (!Number.isFinite(value) || value < 0) return "—";
    if (value < 1000) return `${Math.round(value)} B`;
    const units = ["KB", "MB", "GB", "TB"];
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
      check.disabled = ["checking", "downloading", "applying"].includes(data.status);
      if (apply) {
        apply.hidden = data.status !== "prepared" || data.apply_available !== true;
        apply.disabled = data.status !== "prepared";
      }
      command.hidden = !data.install_command;
      command.textContent = data.install_command || "";
      if (data.status === "applying") applyRequested = true;
      if (check.disabled || applyRequested && !["applied", "failed", "rollback_failed"].includes(data.status)) timer = setTimeout(load, 2000);
      if (["applied", "failed", "rollback_failed"].includes(data.status)) applyRequested = false;
    } catch (_) {
      message.textContent = applyRequested
        ? "更新正在套用，Dashboard 暫時離線；請稍候自動重新連線，不要關機或重複操作。"
        : "更新狀態讀取失敗，請確認已登入。";
      if (progressRegion) progressRegion.hidden = true;
      if (applyRequested) timer = setTimeout(load, 2000);
    }
  }
  async function act(action) {
    clearTimeout(timer); check.disabled = true; prepare.disabled = true;
    const cookie = document.cookie.split(";").map(x => x.trim()).find(x => x.startsWith("meowkeeper_csrf="));
    try {
      const response = await fetch(`/api/v1/updates/${action}`, {method: "POST", headers: {"X-MeowKeeper-CSRF": cookie ? decodeURIComponent(cookie.substring("meowkeeper_csrf=".length)) : ""}});
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "更新要求未完成。");
      }
      if (action === "apply") applyRequested = true;
      message.textContent = action === "apply" ? "更新工作已啟動；Dashboard 暫時離線屬正常情況。" : "正在處理更新要求…";
      timer = setTimeout(load, 1000);
    } catch (error) { message.textContent = error.message || "更新要求未完成，請重新登入後再試。"; check.disabled = false; if (apply) apply.disabled = false; }
  }
  check.addEventListener("click", () => act("check"));
  prepare.addEventListener("click", () => act("prepare"));
  if (apply && dialog && confirmApply) {
    apply.addEventListener("click", () => dialog.showModal());
    confirmApply.addEventListener("click", () => { dialog.close(); act("apply"); });
  }
  window.addEventListener("pagehide", () => clearTimeout(timer));
  load();
})();
