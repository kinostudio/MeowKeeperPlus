(function () {
  "use strict";
  const byId = id => document.getElementById(id);
  const status = byId("tailscale-runtime-status"), login = byId("tailscale-login-status");
  const ip = byId("tailscale-ip"), remote = byId("tailscale-remote-status");
  const remoteUrl = byId("tailscale-remote-url"), message = byId("tailscale-message");
  const button = byId("tailscale-check"), list = byId("tailscale-device-list");
  const summaryText = byId("tailscale-device-summary");
  if (!button) return;
  let busy = false, lastLoadedAt = 0;
  function csrfToken() {
    const value = document.cookie.split(";").map(item => item.trim()).find(item => item.startsWith("meowkeeper_csrf="));
    return value ? decodeURIComponent(value.substring("meowkeeper_csrf=".length)) : "";
  }
  function count(value) { return Number.isInteger(value) && value >= 0 ? `${value} 台` : "無法取得"; }
  function renderSummary(data) {
    let online = count(data.online_device_count);
    if (Number.isInteger(data.unknown_online_state_count) && data.unknown_online_state_count > 0 && Number.isInteger(data.online_device_count))
      online = `至少 ${data.online_device_count} 台（另 ${data.unknown_online_state_count} 台狀態未知）`;
    let active = count(data.active_device_count);
    if (Number.isInteger(data.unknown_activity_state_count) && data.unknown_activity_state_count > 0 && Number.isInteger(data.active_device_count))
      active = `至少 ${data.active_device_count} 台（另 ${data.unknown_activity_state_count} 台未知）`;
    const remoteDevices = count(data.live_viewer_device_count);
    const unknownViews = Number.isInteger(data.unidentified_live_viewer_connections) ? data.unidentified_live_viewer_connections : null;
    const connections = Number.isInteger(data.live_viewer_connection_count) ? `${data.live_viewer_connection_count} 條即時連線` : "即時連線數無法取得";
    const unknown = unknownViews ? `；另有 ${unknownViews} 條來源未能辨識` : "";
    const sessions = Number.isInteger(data.dashboard_session_count) ? `${data.dashboard_session_count} 個近期登入工作階段` : "Dashboard 使用狀態無法取得";
    const unidentifiedSessions = Number.isInteger(data.unidentified_dashboard_sessions) && data.unidentified_dashboard_sessions > 0
      ? `；另 ${data.unidentified_dashboard_sessions} 個來源未辨識` : "";
    if (summaryText) summaryText.textContent = `Tailnet 在線：${online}｜Tailscale 回報有活動：${active}｜近期使用 Dashboard：${sessions}${unidentifiedSessions}｜已確認即時畫面裝置：${remoteDevices}（${connections}${unknown}）。在線或活動不代表正在觀看。`;
  }
  function deviceState(device) {
    if (device.live_viewer_connections > 0) return `正在觀看即時畫面 · ${device.live_viewer_connections} 條連線`;
    if (device.is_using_dashboard) return "最近使用 Dashboard；未確認正在觀看";
    if (device.online === true) return "Tailscale 在線；未確認使用";
    if (device.online === false) return "離線";
    return "狀態未知";
  }
  function renderDevices(data, force) {
    if (!list || (!force && list.contains(document.activeElement))) return;
    list.replaceChildren();
    function appendUnknownViewers(summary) {
      const count = Number.isInteger(summary.unidentified_live_viewer_connections) ? summary.unidentified_live_viewer_connections : 0;
      if (!count) return;
      const sources = summary.unidentified_viewer_sources || {};
      const sourceLabels = { tailscale_unmatched: "Tailscale 來源未配對", lan_unidentified: "區網來源未辨識", local: "本機或代理來源未辨識", unknown: "連線來源未知" };
      const details = Object.entries(sources).filter(([, value]) => Number.isInteger(value) && value > 0)
        .map(([key, value]) => `${sourceLabels[key] || "來源未知"} ${value} 條`).join("、");
      const card = document.createElement("article"); card.className = "tailscale-device-card";
      const title = document.createElement("strong"); title.textContent = "未知裝置";
      const state = document.createElement("p"); state.className = "tailscale-device-state"; state.textContent = `${count} 條即時畫面連線，尚不能確認是哪台設備`;
      const detail = document.createElement("p"); detail.className = "muted"; detail.textContent = details || "來源資料不足以安全配對到 Tailnet 裝置。";
      card.append(title, state, detail); list.append(card);
    }
    if (!data.device_list_available) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "目前無法取得 Tailscale 裝置清單；貓咪監控不受影響。請確認官方 CLI 已登入且有權讀取狀態。";
      list.append(empty); appendUnknownViewers(data.summary || {}); return;
    }
    const devices = data.summary?.devices;
    if (!Array.isArray(devices)) { list.textContent = "裝置狀態目前無法取得；不會以 0 台代替未知資料。"; return; }
    if (!devices.length) { list.textContent = "Tailscale 尚未回報任何裝置。"; appendUnknownViewers(data.summary || {}); return; }
    for (const device of devices) {
      const card = document.createElement("article"); card.className = "tailscale-device-card";
      const header = document.createElement("div"); header.className = "tailscale-device-header";
      const name = document.createElement("strong"); name.textContent = device.name || "未知裝置";
      const os = document.createElement("small"); os.textContent = device.os || "作業系統未回報";
      header.append(name, os);
      const state = document.createElement("p"); state.className = "tailscale-device-state"; state.textContent = deviceState(device);
      const detail = document.createElement("p"); detail.className = "muted";
      const online = device.online === true ? "在線" : device.online === false ? "離線" : "在線狀態未知";
      const active = device.active === true ? "Tailscale 有活動" : device.active === false ? "未回報近期活動" : "活動狀態未知";
      detail.textContent = `${online}｜${active}${device.last_seen ? `｜最近狀態：${device.last_seen}` : ""}`;
      const form = document.createElement("form"); form.className = "tailscale-alias-form";
      const input = document.createElement("input"); input.type = "text"; input.maxLength = 40;
      input.value = device.alias || ""; input.placeholder = device.original_name || "輸入本機別名";
      input.setAttribute("aria-label", `${device.original_name || "裝置"} 的本機顯示別名`);
      const save = document.createElement("button"); save.type = "submit"; save.className = "button secondary";
      save.textContent = device.alias ? "儲存／清除別名" : "設定別名";
      form.append(input, save);
      form.addEventListener("submit", async event => {
        event.preventDefault(); save.disabled = true;
        if (message) message.textContent = "正在保存本機顯示別名…";
        try {
          const response = await fetch(`/api/v1/tailscale/devices/${encodeURIComponent(device.device_ref)}/alias`, {
            method: "PUT", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
            body: JSON.stringify({ alias: input.value.trim() || null })
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.detail || "別名未保存。");
          if (message) message.textContent = input.value.trim() ? "別名只保存在這台電腦，未更改 Tailscale 裝置名稱。" : "已還原 Tailscale 原始裝置名稱。";
          await load(true);
        } catch (error) { if (message) message.textContent = error.message || "本機別名保存失敗。"; }
        finally { save.disabled = false; }
      });
      card.append(header, state, detail, form); list.append(card);
    }
    appendUnknownViewers(data.summary || {});
  }
  async function load(force = false) {
    const now = Date.now();
    if (busy || (!force && now - lastLoadedAt < 10000)) return;
    busy = true; lastLoadedAt = now; button.disabled = true;
    if (message) message.textContent = "正在讀取官方 Tailscale 唯讀狀態…";
    try {
      const [statusResponse, deviceResponse] = await Promise.all([
        fetch("/api/v1/tailscale/status", { cache: "no-store", headers: { Accept: "application/json" } }),
        fetch("/api/v1/tailscale/devices", { cache: "no-store", headers: { Accept: "application/json" } })
      ]);
      const data = await statusResponse.json().catch(() => ({}));
      const devices = await deviceResponse.json().catch(() => ({}));
      if (!statusResponse.ok) throw new Error(data.detail || "Tailscale 狀態讀取失敗。");
      const remoteData = data.remote_dashboard || {};
      const labels = { ok: "正常", starting: "正在啟動", not_installed: "尚未安裝", not_logged_in: "尚未登入", needs_login: "尚未登入", needslogin: "尚未登入", no_state: "尚未就緒", nostate: "尚未就緒", permission_denied: "無權讀取", timeout: "檢查逾時", stopped: "服務未啟動", error: "狀態讀取失敗", not_checked: "尚未檢查" };
      if (status) status.textContent = labels[data.status] || data.status || "狀態未知";
      if (login) login.textContent = data.logged_in ? "已登入" : data.status === "not_installed" ? "未安裝" : "未登入或未啟動";
      if (ip) ip.textContent = data.tailscale_ip || "未取得";
      if (remote) remote.textContent = remoteData.status === "available" ? "可用" : remoteData.status === "not_configured" ? "未設定" : "不可用";
      if (remoteUrl) remoteUrl.textContent = remoteData.url || "未設定";
      if (message) message.textContent = data.message_zh_tw || "已完成唯讀檢查；未修改 Tailscale。";
      renderSummary(devices.summary || data.device_summary || {});
      renderDevices(devices, force);
      const updated = document.getElementById("tailscale-device-updated");
      if (updated) updated.textContent = data.checked_at ? `官方狀態檢查：${new Date(data.checked_at).toLocaleTimeString("zh-TW")}` : "狀態檢查時間未提供";
      document.dispatchEvent(new CustomEvent("meowkeeper:tailscale", { detail: { status: data, devices: devices.summary || {} } }));
    } catch (error) {
      if (status) status.textContent = "暫時無法更新";
      if (login) login.textContent = "尚未確認";
      if (remote) remote.textContent = "尚未確認";
      if (message) message.textContent = `${error.message || "Tailscale 狀態暫時無法讀取。"} 貓咪監控仍會正常運作。`;
      if (summaryText) summaryText.textContent = "裝置與觀看狀態無法取得；不會以 0 台表示。";
      if (list && !list.contains(document.activeElement)) list.textContent = "目前無法讀取裝置清單，請稍後重新整理。";
    } finally { busy = false; button.disabled = false; }
  }
  button.addEventListener("click", () => load(true));
  load(true);
  window.setInterval(() => { if (!document.hidden) load(false); }, 10000);
})();
