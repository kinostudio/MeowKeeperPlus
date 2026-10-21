(function () {
  "use strict";
  const enabled = document.getElementById("telegram-enabled");
  if (!enabled) return;
  const status = document.getElementById("telegram-runtime-status");
  const credentials = document.getElementById("telegram-credentials-status");
  const message = document.getElementById("telegram-message");
  const botToken = document.getElementById("telegram-bot-token");
  const recipientList = document.getElementById("telegram-recipient-list");
  const addRecipient = document.getElementById("add-telegram-recipient");
  const saveRecipients = document.getElementById("save-telegram-recipients");
  const applyButton = document.getElementById("apply-telegram");
  const startupNotice = document.getElementById("telegram-startup-notice");
  const selectionSummary = document.getElementById("telegram-recipient-selection");
  let latestConfig = null;
  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };
  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "Telegram 操作失敗。");
    return data;
  }
  function addRecipientRow(recipient = {}) {
    if (!recipientList) return;
    const row = document.createElement("div");
    row.className = "telegram-recipient-row settings-grid";
    if (recipient.recipient_id) row.dataset.recipientId = recipient.recipient_id;

    const alias = document.createElement("input");
    alias.type = "text";
    alias.maxLength = 80;
    alias.placeholder = "例如：主要手機";
    alias.value = recipient.alias || "";
    alias.setAttribute("aria-label", "Telegram 通知對象別名");

    const chatId = document.createElement("input");
    chatId.type = "text";
    chatId.maxLength = 128;
    chatId.autocomplete = "off";
    chatId.placeholder = recipient.chat_id_masked || "123456789 或 @your_channel";
    if (recipient.chat_id) chatId.value = recipient.chat_id;
    chatId.setAttribute("aria-label", "Telegram Chat ID");
    chatId.setAttribute("aria-describedby", "telegram-chat-id-help");

    const active = document.createElement("input");
    active.type = "checkbox";
    active.checked = recipient.enabled !== false;
    active.setAttribute("aria-label", "啟用此 Telegram 通知對象");

    const role = document.createElement("select");
    role.setAttribute("aria-label", "Telegram 使用者權限");
    [["viewer", "Viewer：查看"], ["operator", "Operator：查看與控制"], ["admin", "Admin：完整管理"]].forEach(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      role.appendChild(option);
    });
    role.value = ["viewer", "operator", "admin"].includes(recipient.role) ? recipient.role : "viewer";

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button secondary recipient-remove-btn";
    remove.textContent = "移除";
    remove.addEventListener("click", () => {
      row.remove();
      if (!recipientList.children.length) addRecipientRow();
      updateSelectionSummary();
    });

    const aliasLabel = document.createElement("label");
    aliasLabel.textContent = "別名";
    aliasLabel.appendChild(alias);
    const chatLabel = document.createElement("label");
    chatLabel.textContent = "Chat ID";
    chatLabel.title = "修改既有對象請重新輸入；留白則保留已保存值";
    chatLabel.appendChild(chatId);
    const activeLabel = document.createElement("label");
    activeLabel.className = "checkbox-line recipient-check";
    activeLabel.appendChild(active);
    activeLabel.appendChild(document.createTextNode("接收通知"));
    const roleLabel = document.createElement("label");
    roleLabel.textContent = "權限";
    roleLabel.appendChild(role);
    row.append(aliasLabel, chatLabel, roleLabel, activeLabel, remove);
    recipientList.appendChild(row);
    alias.addEventListener("input", updateSelectionSummary);
    chatId.addEventListener("input", updateSelectionSummary);
    active.addEventListener("change", updateSelectionSummary);
    updateSelectionSummary();
  }
  function updateSelectionSummary() {
    if (!selectionSummary || !recipientList) return;
    const rows = Array.from(recipientList.children);
    const usable = rows.filter((row) => {
      const inputs = row.querySelectorAll("input");
      return inputs.length >= 3 && inputs[1].value.trim();
    });
    const selected = usable.filter((row) => row.querySelectorAll("input")[2].checked).length;
    const pending = rows.length - usable.length;
    const pendingText = pending ? `；${pending} 列待填寫` : "";
    selectionSummary.textContent = `目前 ${usable.length} 個有效對象，已勾選 ${selected} 個${pendingText}。只有勾選且有 Chat ID 的對象會在 Host 啟動時接通。`;
  }
  if (recipientList) {
    recipientList.addEventListener("input", updateSelectionSummary);
    recipientList.addEventListener("change", updateSelectionSummary);
    recipientList.addEventListener("click", (event) => {
      const remove = event.target.closest("[data-remove-fallback]");
      if (!remove) return;
      const row = remove.closest(".telegram-recipient-row");
      if (row) row.remove();
      if (!recipientList.children.length) addRecipientRow();
      updateSelectionSummary();
    });
    const fallbackRemove = recipientList.querySelector("[data-remove-fallback]");
    if (fallbackRemove) {
      fallbackRemove.addEventListener("click", () => {
        const row = fallbackRemove.closest(".telegram-recipient-row");
        if (row) row.remove();
        if (!recipientList.children.length) addRecipientRow();
        updateSelectionSummary();
      });
    }
  }
  function collectRecipients({ requireChatIds = false } = {}) {
    if (!recipientList) return [];
    return Array.from(recipientList.children).map((row, index) => {
      const inputs = row.querySelectorAll("input");
      if (inputs.length < 3) return null;
      const alias = inputs[0].value.trim() || `通知對象 ${index + 1}`;
      const chatId = inputs[1].value.trim();
      if (!chatId && !row.dataset.recipientId) return null;
      const role = row.querySelector("select")?.value || "viewer";
      const item = { alias, enabled: inputs[2].checked, role };
      if (chatId) item.chat_id = chatId;
      if (row.dataset.recipientId) item.recipient_id = row.dataset.recipientId;
      if (requireChatIds && !chatId) throw new Error(`請輸入「${alias}」的 Chat ID。`);
      return item;
    }).filter(Boolean);
  }
  function renderRecipients(config) {
    if (!recipientList) return;
    recipientList.replaceChildren();
    const recipients = Array.isArray(config.recipients) ? config.recipients : [];
    if (recipients.length) recipients.forEach(addRecipientRow);
    else addRecipientRow();
  }
  async function refresh() {
    const config = await request("/api/v1/telegram/config", { headers: { Accept: "application/json" } });
    renderRecipients(config);
    latestConfig = config;
    enabled.checked = Boolean(config.enabled);
    const [runtimeResult, settingsResult] = await Promise.allSettled([
      request("/api/v1/telegram/status", { headers: { Accept: "application/json" } }),
      request("/api/v1/settings", { headers: { Accept: "application/json" } }),
    ]);
    const runtime = runtimeResult.status === "fulfilled" ? runtimeResult.value : { status: "not_checked" };
    const settings = settingsResult.status === "fulfilled" ? settingsResult.value : { settings: {} };
    if (startupNotice && settings.settings) startupNotice.checked = Boolean(settings.settings.telegram_startup_notice);
    let runtimeLabel = runtime.status || "未啟用";
    if (config.enabled && runtime.status === "disabled") runtimeLabel = "尚未套用（請啟用並重新啟動 Host）";
    status.textContent = `啟用設定：${config.enabled ? "是" : "否"}｜執行狀態：${runtimeLabel}`;
    if (credentials) {
      const token = config.bot_token_configured ? "Bot Token 已設定" : "Bot Token 尚未設定";
      const chats = config.chat_ids_configured ? "Chat ID 已設定" : "Chat ID 尚未設定";
      const count = Number(config.recipient_count || 0);
      const countText = count ? `｜已保存對象 ${count} 個` : "";
      credentials.textContent = `安全憑證狀態：${token}｜${chats}${countText}｜保存方式：${config.secret_source || "尚未設定"}。實際值不會回傳。`;
    }
  }
  // Keep the editor usable even when an older Host or a transient API error
  // prevents the first refresh from completing.
  if (recipientList && !recipientList.children.length) addRecipientRow();
  else updateSelectionSummary();
  if (addRecipient) addRecipient.onclick = () => addRecipientRow();
  const saveCredentials = document.getElementById("save-telegram-credentials");
  if (saveCredentials) saveCredentials.addEventListener("click", async () => {
    const tokenValue = botToken ? botToken.value : "";
    if (!tokenValue.trim()) {
      message.textContent = "請填寫 Bot Token；通知對象請在下方逐一新增。";
      return;
    }
    let recipients;
    try { recipients = collectRecipients({ requireChatIds: true }); }
    catch (error) { message.textContent = error.message; return; }
    if (!recipients.length) {
      message.textContent = "請至少填寫一個通知對象的 Chat ID。";
      return;
    }
    const chatValue = recipients.map((item) => item.chat_id).join(",");
    try {
      const data = await request("/api/v1/telegram/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ bot_token: tokenValue, chat_ids: chatValue, recipients, reauth: true }),
      });
      if (botToken) botToken.value = "";
      message.textContent = data.message_zh_tw || "憑證與通知對象已安全保存；請啟用 Telegram。";
      await refresh();
    } catch (error) { message.textContent = `憑證未保存：${error.message || "請確認登入狀態。"}`; }
  });
  if (saveRecipients) saveRecipients.addEventListener("click", async () => {
    let recipients;
    try { recipients = collectRecipients(); }
    catch (error) { message.textContent = error.message; return; }
    if (!recipients.length) { message.textContent = "請至少新增一個通知對象。"; return; }
    try {
      const data = await request("/api/v1/telegram/recipients", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ recipients, reauth: true }),
      });
      message.textContent = data.message_zh_tw || "通知對象已保存；請啟用 Telegram。";
      await refresh();
    } catch (error) { message.textContent = `通知對象未保存：${error.message || "請先保存 Bot Token。"}`; }
  });
  async function saveStartupNotice() {
    if (!startupNotice) return null;
    return request("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
      body: JSON.stringify({ telegram_startup_notice: startupNotice.checked, reauth: true }),
    });
  }
  const clearCredentials = document.getElementById("clear-telegram-credentials");
  if (clearCredentials) clearCredentials.addEventListener("click", async () => {
    if (!window.confirm("確認清除本機 Windows Credential Manager 中的 Telegram 憑證與通知對象？")) return;
    try {
      const data = await request("/api/v1/telegram/credentials", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ clear: true, reauth: true }),
      });
      message.textContent = data.message_zh_tw || "本機憑證已清除；請重新啟動 Host。";
      await refresh();
    } catch (error) { message.textContent = `憑證未清除：${error.message || "請確認登入狀態。"}`; }
  });
  const saveTelegram = document.getElementById("save-telegram");
  if (saveTelegram) saveTelegram.addEventListener("click", async () => {
    try {
      await saveStartupNotice();
      const data = await request("/api/v1/telegram/config", { method: "PUT", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ enabled: enabled.checked, reauth: true }) });
      message.textContent = data.message_zh_tw || "設定已保存；請重新啟動 Host。";
      await refresh();
    } catch (error) { message.textContent = `Telegram 設定未保存：${error.message || "請確認登入狀態。"}`; }
  });
  if (applyButton) applyButton.addEventListener("click", async () => {
    if (!latestConfig) { message.textContent = "尚未讀取 Telegram 設定，請稍候再試。"; return; }
    if (!latestConfig.bot_token_configured || !latestConfig.chat_ids_configured) { message.textContent = "請先安全保存 Bot Token 與至少一個通知對象，再啟用 Telegram。"; return; }
    if (!window.confirm("確定啟用 Telegram 事件通知並重新啟動 Host？監控會短暫重連，但已保存的憑證不會清除。")) return;
    applyButton.disabled = true;
    try {
      await saveStartupNotice();
      await request("/api/v1/telegram/config", { method: "PUT", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ enabled: true, reauth: true }) });
      const restart = await request("/api/v1/host/restart", { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({ confirm: true }) });
      if (restart.status !== "restarting" && restart.status !== "accepted") throw new Error(restart.message_zh_tw || "Host 未接受重新啟動要求。");
      message.textContent = "Telegram 已啟用；Host 正在重新啟動並載入已保存憑證。請稍候查看執行狀態。";
      window.setTimeout(() => window.location.reload(), 1800);
    } catch (error) {
      message.textContent = `Telegram 尚未套用：${error.message || "Host 重新啟動失敗，請查看診斷。"}`;
      applyButton.disabled = false;
      await refresh().catch(() => {});
    }
  });
  const testTelegram = document.getElementById("test-telegram");
  if (testTelegram) testTelegram.addEventListener("click", async () => {
    if (!window.confirm("確認傳送一則 Telegram 測試文字給所有已啟用通知對象？")) return;
    try {
      const data = await request("/api/v1/telegram/test", { method: "POST", headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() }, body: JSON.stringify({}) });
      message.textContent = data.message_zh_tw || "Telegram 測試已完成。";
      await refresh();
    } catch (error) { message.textContent = `Telegram 測試未完成：${error.message || "請確認環境變數與權限。"}`; }
  });
  refresh().catch((error) => { message.textContent = error.message || "需要登入後才能讀取 Telegram 狀態。"; });
})();
