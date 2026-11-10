(() => {
  "use strict";
  const check = document.getElementById("update-check"), prepare = document.getElementById("update-prepare");
  if (!check || !prepare) return;
  const apply = document.getElementById("update-apply");
  const prepareFull = document.getElementById("update-prepare-full");
  const autoApply = document.getElementById("update-auto-apply");
  const dialog = document.getElementById("update-apply-dialog");
  const confirmApply = document.getElementById("update-apply-confirm");
  const message = document.getElementById("update-message"), command = document.getElementById("update-command");
  const progressRegion = document.getElementById("update-progress-region");
  const progress = document.getElementById("update-progress");
  const progressLabel = document.getElementById("update-progress-label");
  let timer, applyRequested = false, autoApplyArmed = false, lastStep = null, offlineSince = null;

  function formatBytes(value) {
    if (!Number.isFinite(value) || value < 0) return "—";
    if (value < 1000) return `${Math.round(value)} B`;
    const units = ["KB", "MB", "GB", "TB"];
    let amount = value / 1000, unit = 0;
    while (amount >= 1000 && unit < units.length - 1) { amount /= 1000; unit += 1; }
    return `${amount.toFixed(amount >= 100 ? 0 : amount >= 10 ? 1 : 2)} ${units[unit]}`;
  }

  function stepText(step) {
    return step ? `第 ${step.step}／${step.step_total} 步：${step.step_name_zh_tw}` : "";
  }

  function showApplyProgress(data) {
    // Install/switch steps reported by the update worker: "step n of 8 + name".
    if (!progressRegion || !progress || !progressLabel) return;
    const step = Number.isInteger(data.step) && Number.isInteger(data.step_total) && data.step_total > 0 ? data : lastStep;
    progressRegion.hidden = !step;
    if (!step) return;
    progress.max = step.step_total;
    progress.value = data.status === "applied" ? step.step_total : Math.max(0, step.step - 0.5);
    const pieces = [stepText(step)];
    if (offlineSince) pieces.push(`Dashboard 換版中暫時離線 ${Math.round((Date.now() - offlineSince) / 1000)} 秒`);
    if (Array.isArray(data.steps) && ["applied", "failed", "rollback_failed"].includes(data.status)) {
      const timeline = data.steps.map((item, index) => {
        const next = data.steps[index + 1];
        const seconds = next ? Math.max(0, Math.round((Date.parse(next.started_at) - Date.parse(item.started_at)) / 1000)) : null;
        return `${item.step}. ${item.name_zh_tw}${seconds === null ? "" : `（${seconds} 秒）`}`;
      });
      pieces.push(timeline.join(" → "));
    }
    progressLabel.textContent = pieces.join(" · ");
  }

  function showProgress(data) {
    if (["applying", "applied", "failed", "rollback_failed"].includes(data.status) && (Number.isInteger(data.step) || lastStep)) {
      showApplyProgress(data); return;
    }
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
      offlineSince = null;
      if (Number.isInteger(data.step) && data.status === "applying") lastStep = {step: data.step, step_total: data.step_total, step_name_zh_tw: data.step_name_zh_tw};
      message.textContent = data.message_zh_tw + (data.detail_zh_tw ? `（${data.detail_zh_tw}）` : "")
        + (data.status === "available" && Number.isFinite(data.download_bytes) ? ` 下載量約 ${formatBytes(data.download_bytes)}。` : "");
      showProgress(data);
      const fullOnly = data.status === "available" && data.package_kind === "full";
      prepare.disabled = data.status !== "available" || fullOnly;
      if (prepareFull) {
        prepareFull.hidden = !fullOnly;
        prepareFull.disabled = !fullOnly;
        if (fullOnly && Number.isFinite(data.download_bytes)) prepareFull.textContent = `改下載完整安裝包（${formatBytes(data.download_bytes)}）`;
      }
      if (autoApplyArmed && data.status === "prepared" && data.apply_available === true && !applyRequested) {
        autoApplyArmed = false;
        act("apply");
        return;
      }
      if (["failed", "rollback_failed"].includes(data.status)) autoApplyArmed = false;
      check.disabled = ["checking", "downloading", "applying"].includes(data.status);
      if (apply) {
        apply.hidden = data.status !== "prepared" || data.apply_available !== true;
        apply.disabled = data.status !== "prepared";
      }
      command.hidden = !data.install_command;
      command.textContent = data.install_command || "";
      if (data.status === "applying") applyRequested = true;
      if (check.disabled || autoApplyArmed || applyRequested && !["applied", "failed", "rollback_failed"].includes(data.status)) timer = setTimeout(load, 2000);
      if (["applied", "failed", "rollback_failed"].includes(data.status)) { applyRequested = false; lastStep = null; }
    } catch (_) {
      if (applyRequested && offlineSince === null) offlineSince = Date.now();
      message.textContent = applyRequested
        ? `更新正在套用，Dashboard 暫時離線；最後回報：${stepText(lastStep) || "已啟動更新工作"}。請稍候自動重新連線，不要關機或重複操作。`
        : "更新狀態讀取失敗，請確認已登入。";
      if (applyRequested && lastStep) showApplyProgress({status: "applying"});
      else if (progressRegion) progressRegion.hidden = true;
      if (applyRequested) timer = setTimeout(load, 2000);
    }
  }
  async function act(action) {
    clearTimeout(timer); check.disabled = true; prepare.disabled = true; if (prepareFull) prepareFull.disabled = true;
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
  let pendingPrepare = null;
  function startPrepare(action) {
    // Auto-apply still asks once, up front, whether a short monitoring pause is OK.
    if (autoApply && autoApply.checked && dialog && confirmApply) { pendingPrepare = action; dialog.showModal(); return; }
    act(action);
  }
  check.addEventListener("click", () => act("check"));
  prepare.addEventListener("click", () => startPrepare("prepare"));
  if (prepareFull) prepareFull.addEventListener("click", () => startPrepare("prepare-full"));
  if (dialog) dialog.addEventListener("close", () => { if (dialog.returnValue === "cancel") pendingPrepare = null; });
  if (apply && dialog && confirmApply) {
    apply.addEventListener("click", () => { pendingPrepare = null; dialog.showModal(); });
    confirmApply.addEventListener("click", () => {
      dialog.close();
      if (pendingPrepare) { autoApplyArmed = true; const action = pendingPrepare; pendingPrepare = null; act(action); }
      else act("apply");
    });
  }
  window.addEventListener("pagehide", () => clearTimeout(timer));
  load();
})();
