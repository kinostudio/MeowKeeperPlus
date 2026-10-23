(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const statusPill = $("service-state");
  const statusLabels = { running: "運作中", starting: "啟動中", waiting: "等待來源", waiting_for_source: "等待設定來源", not_configured: "尚未設定來源", stopped: "已停止", disabled: "未啟用", configured: "已設定，待傳送驗證", enabled: "已啟用", connected: "已連線", ready: "已就緒", ok: "正常", failed: "發生錯誤", error: "發生錯誤", reconnecting: "正在重新連線", ended: "播放已結束", unsupported: "來源不支援", offline: "未連線", unknown: "尚未確認" };
  const statusText = (value) => statusLabels[value] || "尚待確認";

  function setPill(text, className) {
    if (!statusPill) return;
    statusPill.textContent = text;
    statusPill.className = "status-pill " + className;
  }

  function csrfToken() {
    const value = document.cookie.split(";").map((item) => item.trim()).find((item) => item.startsWith("meowkeeper_csrf="));
    return value ? decodeURIComponent(value.substring("meowkeeper_csrf=".length)) : "";
  }

  function displayStatus(payload) {
    const monitorToggle = $("monitor-toggle"), enabled = ["running","starting"].includes(payload.status);
    if (monitorToggle) {
      monitorToggle.dataset.enabled = String(enabled); monitorToggle.disabled = false;
      monitorToggle.textContent = enabled ? "停止監控" : "啟動監控";
      monitorToggle.setAttribute("aria-pressed", String(enabled));
    }
    const metrics = payload.metrics || {};
    const source = metrics.source || {};
    const ffmpeg = metrics.ffmpeg || {};
    const ai = metrics.ai || {};
    const ring = metrics.ring_buffer || {};
    const telegram = metrics.telegram || {};
    const sqlite = metrics.sqlite || {};
    const system = metrics.system || {};
    const application = metrics.application || {};
    const disk = payload.disk || metrics.disk || {};
    const dashboard = payload.dashboard || {};
    const referenceTime = Date.parse(payload.checked_at || "");
    const frameAge = source.last_frame_at ? (Number.isFinite(referenceTime) ? referenceTime : Date.now()) - Date.parse(source.last_frame_at) : Infinity;
    const freshFrame = ["running", "streaming", "connected"].includes(source.status) && Number.isFinite(frameAge) && frameAge >= 0 && frameAge < 15000;
    const monitoring = payload.status === "running" && freshFrame && Number(ai.fps || 0) > 0;
    if ($("monitor-status")) {
      $("monitor-status").textContent = monitoring ? "正在監控" : payload.status === "running" ? (freshFrame ? "等待 AI 分析" : "等待有效影格") : payload.status === "starting" ? "正在啟動" : "尚未監控";
      $("monitor-detail").textContent = monitoring
        ? `最近影格：${Math.round(frameAge / 1000)} 秒前｜AI ${ai.fps} FPS｜快取 ${ring.segment_count || 0} 段`
        : payload.status === "running" ? (freshFrame ? "已收到來源畫面，但 AI 尚未確認實際推論；請查看運算裝置與模型狀態。" : "接收程序已啟動，但尚未確認持續影格與 AI；畫面可見不等於錄影及偵測已運作。") : "請先確認來源及啟動狀態。";
    }
    if ($("source-status")) $("source-status").textContent = freshFrame ? "持續收到畫面" : source.status === "running" ? "等待畫面" : statusText(source.status);
    if ($("source-detail")) $("source-detail").textContent = `來源：${source.source_id || "尚未確認"}｜連線 ${source.connections || 0}｜最近影格 ${freshFrame ? Math.round(frameAge / 1000) + " 秒前" : "未取得或已逾時"}`;
    if ($("ffmpeg-status")) $("ffmpeg-status").textContent = statusText(ffmpeg.status);
    if ($("ai-status")) $("ai-status").textContent = ai.backend || "unknown";
    if ($("ring-status")) $("ring-status").textContent = ring.segment_count ? `${Number(ring.seconds || 0).toFixed(1)} 秒` : "尚無壓縮片段";
    if ($("ai-detail")) $("ai-detail").textContent = `${ai.device || "unknown"}｜FPS ${ai.fps == null ? "-" : ai.fps}`;
    if ($("telegram-status")) $("telegram-status").textContent = telegram.enabled ? statusText(telegram.status || "enabled") : "未啟用";
    if ($("telegram-detail")) $("telegram-detail").textContent = telegram.enabled ? "已啟用通知；請以實際收件與傳送測試確認可用。" : "到 Telegram 設定頁啟用，並選擇接收通知的對象。";
    if ($("sqlite-status")) $("sqlite-status").textContent = statusText(sqlite.status);
    if ($("system-status") && !$("system-status").hasAttribute("data-rolling-number")) $("system-status").textContent = `${system.cpu_percent == null ? "-" : system.cpu_percent + "%"}／${system.ram_percent == null ? "-" : system.ram_percent + "%"}`;
    if ($("application-status") && !$("application-status").hasAttribute("data-rolling-number")) $("application-status").textContent = `${application.cpu_percent == null ? "計算中" : application.cpu_percent + "% CPU"}／${application.rss_bytes == null ? "-" : (application.rss_bytes / 1000000).toFixed(0) + " MB"}`;
    if ($("application-detail")) $("application-detail").textContent = `已辨識 ${application.process_count == null ? "-" : application.process_count} 個相關程序｜${application.message_zh_tw || "只計算目前可辨識的本機程序。"}`;
    if ($("system-detail") && !$("system-detail").hasAttribute("data-rolling-number")) $("system-detail").textContent = `每 5 秒更新｜最近檢查 ${payload.checked_at ? new Date(payload.checked_at).toLocaleTimeString("zh-TW") : "尚未取得"}`;
    if ($("hardware-status")) $("hardware-status").textContent = `${ai.device || "-"}／${disk.free_bytes == null ? "-" : Math.round(disk.free_bytes / 1000000000 * 10) / 10 + " GB"}`;
    if ($("dashboard-network-note")) {
      const urls = Array.isArray(dashboard.urls) ? dashboard.urls : [];
      const bind = dashboard.bind_host || "本機";
      const urlText = urls.length ? `區網網址：${urls.join("、")}` : `目前僅本機：${dashboard.local_url || "未設定"}`;
      $("dashboard-network-note").textContent = `${urlText}｜監聽位址 ${bind}｜關閉瀏覽器不會停止監控`;
    }
    setPill(monitoring ? "監控中" : "監控未確認", monitoring ? "status-ok" : "status-warn");
    document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: payload}));
  }

  function displayTailscale(data) {
    const remote = data && data.remote_dashboard ? data.remote_dashboard : {};
    const counts = data && data.device_summary ? data.device_summary : {};
    if ($("tailscale-status")) {
      $("tailscale-status").textContent = data && data.logged_in ? (remote.status === "available" ? "私人網路已連線" : "Tailscale 已登入") : (data?.status === "not_installed" ? "尚未安裝" : "尚未連線");
    }
    if ($("tailscale-detail")) {
      let online = Number.isInteger(counts.online_device_count) ? `${counts.online_device_count} 台在線` : "在線數無法取得";
      if (Number.isInteger(counts.unknown_online_state_count) && counts.unknown_online_state_count > 0 && Number.isInteger(counts.online_device_count)) online = `至少 ${counts.online_device_count} 台在線（另 ${counts.unknown_online_state_count} 台未知）`;
      let active = Number.isInteger(counts.active_device_count) ? `${counts.active_device_count} 台近期有活動` : "活動數無法取得";
      if (Number.isInteger(counts.unknown_activity_state_count) && counts.unknown_activity_state_count > 0 && Number.isInteger(counts.active_device_count)) active = `至少 ${counts.active_device_count} 台有活動（另 ${counts.unknown_activity_state_count} 台未知）`;
      const sessions = Number.isInteger(counts.dashboard_session_count) ? `${counts.dashboard_session_count} 個近期 Dashboard 工作階段` : "Dashboard 使用狀態未知";
      const watching = Number.isInteger(counts.live_viewer_device_count) ? `${counts.live_viewer_device_count} 台已確認觀看` : "觀看狀態無法取得";
      const unknown = Number.isInteger(counts.unidentified_live_viewer_connections) && counts.unidentified_live_viewer_connections > 0
        ? `；另 ${counts.unidentified_live_viewer_connections} 條連線未能辨識` : "";
      $("tailscale-detail").textContent = `${online}｜${active}｜${sessions}｜${watching}${unknown}。在線或活動不代表正在觀看。`;
    }
    document.dispatchEvent(new CustomEvent("meowkeeper:tailscale", { detail: { status: data || {}, devices: counts } }));
  }

  let tailscaleBusy = false, tailscaleAt = 0;
  async function loadTailscale(force = false) {
    if (tailscaleBusy || (!force && Date.now() - tailscaleAt < 10000)) return;
    tailscaleBusy = true; tailscaleAt = Date.now();
    try {
      const response = await fetch("/api/v1/tailscale/status", { cache: "no-store", headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Tailscale 狀態讀取失敗");
      displayTailscale(await response.json());
    } catch (_) {
      if ($("tailscale-status")) $("tailscale-status").textContent = "暫時無法更新";
      if ($("tailscale-detail")) $("tailscale-detail").textContent = "裝置狀態無法取得；不代表貓咪監控已停止。";
    } finally { tailscaleBusy = false; }
  }

  async function load() {
    try {
      const response = await fetch("/api/v1/status", { headers: { Accept: "application/json" } });
      if (response.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (!response.ok) throw new Error("狀態讀取失敗");
      const payload = await response.json();
      displayStatus(payload);
    } catch (error) {
      setPill("狀態暫時無法讀取", "status-warn");
      ["monitor-status", "source-status", "telegram-status", "ffmpeg-status", "ai-status", "ring-status", "sqlite-status", "application-status", "system-status", "hardware-status", "tailscale-status"].forEach((id) => { if ($(id)) $(id).textContent = "暫時無法更新"; });
      if ($("monitor-detail")) $("monitor-detail").textContent = "頁面無法取得最新狀態，請檢查服務是否仍在執行；這不代表監控已停止。";
      ["source-detail", "telegram-detail"].forEach((id) => { if ($(id)) $(id).textContent = "目前無法確認最新狀態，請稍後重新讀取。"; });
      document.dispatchEvent(new CustomEvent("meowkeeper:status", {detail: {metrics:{}}}));
      if ($("page-note")) $("page-note").textContent = "即時狀態暫時無法讀取；已登入的 Dashboard 不需要再次輸入小米帳號。";
      return;
    }
    await loadTailscale();
    try {
      const events = await fetch("/api/v1/events?limit=5&offset=0", { headers: { Accept: "application/json" } });
      if (!events.ok) throw new Error("事件讀取失敗");
      const data = await events.json();
      if ($("events-list") && document.getElementById("overview-preview-badge")) {
        document.dispatchEvent(new CustomEvent("meowkeeper:events", {detail: data.items || []}));
      } else if ($("events-list")) {
        const list = $("events-list");
        list.replaceChildren();
        (data.items || []).forEach((item) => {
          const row = document.createElement("div"); row.className = "event-row";
          const name = document.createElement("span"); name.textContent = item.event_type === "cat_detected" ? "發現貓咪" : "監控事件";
          const time = document.createElement("small");
          const date = new Date(item.trigger_at);
          time.textContent = Number.isFinite(date.getTime()) ? date.toLocaleString("zh-TW") : "時間尚未提供";
          row.append(name, time); list.appendChild(row);
        });
        if (!list.children.length) { const empty = document.createElement("p"); empty.className = "muted"; empty.textContent = "目前還沒有事件。來源接通並開始偵測後，符合條件的片段會出現在這裡。"; list.appendChild(empty); }
      }
    } catch (error) {
      if ($("events-list")) $("events-list").textContent = "事件暫時無法更新；監控狀態仍以上方資訊為準。";
    }
  }

  const hostRestart = $("host-restart");
  if (hostRestart) hostRestart.addEventListener("click", async () => {
    if (!window.confirm("確定要重新啟動 MeowKeeper+ Host 並重新載入最新程式與設定嗎？")) return;
    hostRestart.disabled = true;
    const message = $("service-action-message");
    if (message) message.textContent = "Host 重新啟動排程中…";
    try {
      const response = await fetch("/api/v1/host/restart", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ confirm: true }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Host 重新啟動未通過驗證");
      if (message) message.textContent = data.message_zh_tw || "Host 即將重新啟動。";
      window.setTimeout(() => window.location.reload(), 1800);
    } catch (error) {
      if (message) message.textContent = `Host 重新啟動未完成：${error.message || "請確認登入狀態與 CSRF 設定。"}`;
      hostRestart.disabled = false;
    }
  });

  $("monitor-toggle")?.addEventListener("click", async event => {
    const button=event.currentTarget, enabled=button.dataset.enabled !== "true";
    if(!enabled&&!window.confirm("停止監控將中斷來源接收、AI、錄影快取與新增事件通知；Dashboard 仍可操作。確定停止？"))return;
    button.disabled=true; $("service-action-message").textContent="監控開關套用中…";
    try {
      const response=await fetch(enabled?"/api/v1/monitor/start":"/api/v1/monitor/stop",{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrfToken()},body:JSON.stringify({confirm:true}),signal:AbortSignal.timeout(20000)});
      const result=await response.json(); if(!response.ok) throw new Error(result.detail || "開關未套用");
      $("service-action-message").textContent=result.result?.message_zh_tw||result.message_zh_tw||(enabled?"啟動已接受，等待來源影格。":"監控管線已停止，Dashboard 仍可使用。"); await load();
    } catch(error) {$("service-action-message").textContent=`監控開關未確認：${error.message}`;}
    finally {button.disabled=false;}
  });
  $("host-close")?.addEventListener("click",async event=>{
    if(!window.confirm("關閉程式將停止偵測、錄影、Host、Dashboard 與 Tray。需重新開啟才能恢復，確定繼續？"))return;
    event.currentTarget.disabled=true;
    try {
      const response=await fetch("/api/v1/host/shutdown",{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrfToken()},body:JSON.stringify({confirm:true}),signal:AbortSignal.timeout(10000)});
      const result=await response.json(); if(!response.ok)throw new Error(result.detail || "未接受關閉");
      $("service-action-message").textContent="完整關閉已接受；網頁將失去連線，請重新開啟程式以恢復。";
    }catch(error){$("service-action-message").textContent=`關閉未確認：${error.message}`;event.currentTarget.disabled=false;}
  });

  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      if (action === "refresh") return load();
      if (action === "stop" && !window.confirm("確定要停止背景監控嗎？Tray 與瀏覽器關閉不會執行此操作。")) return;
      if (action === "restart" && !window.confirm("確定要重新啟動背景監控嗎？")) return;
      button.disabled = true;
      const message = $("action-message");
      if (message) message.textContent = "操作進行中…";
      try {
        if (action === "snapshot" || action === "live") {
          const response = await fetch(action === "snapshot" ? "/api/v1/snapshot" : "/api/v1/live/recent", { method: action === "snapshot" ? "GET" : "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: action === "live" ? JSON.stringify({ pre_seconds: 5, post_seconds: 0 }) : undefined });
          if (!response.ok) throw new Error("目前沒有可用媒體");
          const url = URL.createObjectURL(await response.blob());
          window.open(url, "_blank", "noopener");
          if (message) message.textContent = action === "snapshot" ? "已開啟目前畫面。" : "已開啟最近 5 秒片段。";
        } else if (action === "simulate") {
          const response = await fetch("/api/v1/events/simulate", { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ event_type: "cat_detected" }) });
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "模擬事件失敗");
          if (message) message.textContent = data.message_zh_tw || `模擬事件：${data.status}`;
          await load();
        } else {
          const endpoint = action === "start" ? "/api/v1/monitor/start" : action === "restart" ? "/api/v1/monitor/restart" : "/api/v1/monitor/stop";
          const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ confirm: action !== "start" }) });
          const data = await response.json();
          if (!response.ok) throw new Error(data.detail || "操作未通過驗證");
          if (message) message.textContent = data.message_zh_tw || "操作已接受。";
          await load();
        }
      } catch (error) {
        if (message) message.textContent = `操作未完成：${error.message || "請確認登入狀態與 CSRF 設定。"}`;
      } finally { button.disabled = false; }
    });
  });
  load();
  if ($("monitor-status") || $("monitor-toggle")) window.setInterval(() => { if (!document.hidden) load(); }, 5000);
  if ($("tailscale-status")) window.setInterval(() => { if (!document.hidden) loadTailscale(); }, 10000);

  // Global backdrop click to close any native <dialog>
  document.addEventListener("click", (event) => {
    if (event.target && event.target.tagName === "DIALOG" && event.target.open) {
      const rect = event.target.getBoundingClientRect();
      const isInDialog = (
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width
      );
      if (!isInDialog) {
        event.target.close();
      }
    }
  });

})();
