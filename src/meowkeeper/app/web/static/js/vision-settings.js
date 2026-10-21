(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const form = byId("vision-settings-form");
  if (!form) return;

  let current = null;
  let usageHours = 24;
  let usageRows = [];
  let usageEventDetails = {};
  let usageTotal = 0;
  let usageHasMore = false;
  let usageLoading = false;
  let usageError = "";
  let discoveredModels = [];
  const profileFields = ["daily_request_limit", "requests_per_minute_limit", "tokens_per_minute_limit",
    "provider_daily_request_limit", "provider_requests_per_minute_limit", "provider_tokens_per_minute_limit",
    "estimated_tokens_per_request", "warning_threshold_percent", "quality_preset", "storyboard_frame_count",
    "storyboard_size", "storyboard_jpeg_quality", "storyboard_timestamps", "storyboard_include_zones",
    "storyboard_media_resolution", "event_merge_cooldown_seconds", "storyboard_include_scene_references"];
  const statusLabels = {
    not_checked: "尚未測試", metadata_available: "服務已回應", model_not_confirmed: "未確認模型",
    unsupported_check: "此服務無模型清單介面", auth_error: "授權或金鑰錯誤", rate_limited: "服務暫時限流",
    timeout: "服務逾時", unavailable: "服務目前無法連線", bad_response: "服務回應格式無法辨識",
  };
  const outcomeLabels = {
    completed: "分析完成", timeout: "逾時", auth_error: "金鑰或授權錯誤",
    rate_limited: "服務限流", unavailable: "服務無法使用", bad_response: "回應格式錯誤",
    skipped_budget: "達本機每日預算", skipped_rpm: "達每分鐘請求預算",
    skipped_tpm: "達每分鐘 Token 預算", token_estimate_required: "需要 Token 預估",
    skipped_busy: "已有分析工作", circuit_open: "服務暫時冷卻", duplicate_request: "重複工作略過",
    skipped_cooldown: "事件外送冷卻中",
    interrupted: "Host 中斷", insufficient_evidence: "影格證據不足", invalid_image: "圖片格式不支援",
  };
  const message = (text) => {
    byId("vision-message").textContent = text;
    const saveStatus = byId("vision-save-status");
    if (saveStatus) saveStatus.textContent = text;
  };
  const csrf = () => decodeURIComponent(document.cookie.split(";").map((value) => value.trim())
    .find((value) => value.startsWith("meowkeeper_csrf="))?.slice("meowkeeper_csrf=".length) || "");

  async function request(path, method = "GET", body = null) {
    const response = await fetch(path, {
      method, credentials: "same-origin", cache: "no-store",
      headers: body ? { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrf() } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI 分析操作未完成；本機監控仍可使用。");
    return data;
  }

  function provider() {
    return form.querySelector('input[name="vision-provider"]:checked')?.value || "openai_compatible";
  }

  function updateRangeLabels() {
    byId("vision-daily-value").value = `${byId("vision-daily-limit").value} 次`;
    byId("vision-rpm-value").value = `${byId("vision-rpm-limit").value} 次`;
    byId("vision-warning-value").value = `${byId("vision-warning-threshold").value}%`;
    byId("vision-grace-value").value = `${byId("vision-grace").value} 秒`;
    byId("vision-frame-count-value").value = `${byId("vision-frame-count").value} 張`;
    byId("vision-jpeg-quality-value").value = byId("vision-jpeg-quality").value;
    const cooldown = Number(byId("vision-cooldown").value);
    const minutes = Math.floor(cooldown / 60);
    const seconds = cooldown % 60;
    byId("vision-cooldown-value").value = cooldown === 0 ? "關閉"
      : minutes && seconds ? `${minutes} 分 ${seconds} 秒` : minutes ? `${minutes} 分鐘` : `${seconds} 秒`;
  }

  function updateGeminiEndpoint() {
    const isGemini = provider() === "gemini";
    const endpoint = byId("vision-endpoint");
    endpoint.readOnly = isGemini;
    byId("vision-endpoint-label").classList.toggle("is-readonly", isGemini);
    endpoint.placeholder = isGemini
      ? "依模型名稱自動產生 Gemini GenerateContent 網址"
      : "貼上服務商文件中的完整圖片 API 網址";
    const model = byId("vision-model").value.trim();
    if (isGemini && /^[A-Za-z0-9_.-]{1,100}$/.test(model)) {
      endpoint.value = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`;
    } else if (isGemini) {
      endpoint.value = "";
    }
    const rpm = byId("vision-rpm-limit");
    rpm.max = isGemini ? "15" : "60";
    if (Number(rpm.value) > Number(rpm.max)) rpm.value = rpm.max;
    byId("vision-provider-daily-limit").max = "10000000";
    byId("vision-count-tokens").disabled = !isGemini;
    byId("vision-count-tokens").title = isGemini ? "使用合成圖片呼叫 Gemini countTokens" : "目前只接線 Gemini countTokens";
    updateMediaResolutionOptions();
  }

  function updateMediaResolutionOptions() {
    const select = byId("vision-media-resolution");
    const isGemini = provider() === "gemini";
    const choices = isGemini
      ? [["auto", "自動（預設）"], ["low", "低（較省用量）"], ["medium", "中"], ["high", "高（較多細節）"]]
      : [["auto", "自動（預設）"], ["low", "低（較省用量）"], ["high", "高（較多細節）"]];
    const selected = choices.some(([value]) => value === select.value) ? select.value : "auto";
    select.replaceChildren(...choices.map(([value, label]) => new Option(label, value)));
    select.value = selected;
    byId("vision-resolution-help").textContent = isGemini
      ? "Gemini 會依自動／低／中／高調整送出的拼貼尺寸；自動模式使用下方畫布上限。服務商與模型仍須實測，這不是辨識準確度保證。"
      : "OpenAI 格式相容服務會把低／高映射到圖片 detail 欄位；自動模式不傳該欄位。實際支援依服務與模型而異，請先用合成示範圖測試。";
  }

  function destination() {
    const endpoint = byId("vision-endpoint").value;
    let destinationHost = "尚未指定";
    try {
      const parsed = new URL(endpoint);
      destinationHost = ["127.0.0.1", "[::1]", "::1"].includes(parsed.hostname)
        ? "這台電腦的本機服務" : parsed.hostname;
    } catch { /* 未填正確網址時不顯示假目的地。 */ }
    byId("vision-destination").textContent = `圖片會送到：${destinationHost}；模型：${byId("vision-model").value.trim() || "尚未指定"}。請先確認服務商的圖片保存規定。`;
  }

  const BUILTIN_MODELS = {
    gemini: [
      { id: "gemini-2.5-flash", label: "gemini-2.5-flash（推薦：平衡速度與準確度）" },
      { id: "gemini-2.5-flash-lite", label: "gemini-2.5-flash-lite（輕量高速・省額度）" },
      { id: "gemini-2.5-pro", label: "gemini-2.5-pro（高精細度推理）" },
      { id: "gemini-2.0-flash", label: "gemini-2.0-flash（穩定通用）" },
      { id: "gemini-flash-latest", label: "gemini-flash-latest（最新 Flash 別名）" },
      { id: "gemini-flash-lite-latest", label: "gemini-flash-lite-latest（最新 Flash-Lite 別名）" },
      { id: "gemini-1.5-flash", label: "gemini-1.5-flash" },
      { id: "gemini-1.5-pro", label: "gemini-1.5-pro" },
    ],
    openai_compatible: [
      { id: "gpt-4.1-mini", label: "gpt-4.1-mini（推薦：OpenAI 視覺輕量）" },
      { id: "gpt-4.1", label: "gpt-4.1（OpenAI 高精細度視覺）" },
      { id: "gpt-4o-mini", label: "gpt-4o-mini（OpenAI 通用視覺）" },
      { id: "gpt-4o", label: "gpt-4o（OpenAI 完整多模態）" },
      { id: "qwen2.5-vl-7b-instruct", label: "qwen2.5-vl-7b-instruct（本機／相容視覺模型）" },
      { id: "llava-v1.6-mistral-7b", label: "llava-v1.6-mistral-7b（本機 LLaVA 視覺模型）" },
    ],
  };

  function loadCachedModels(providerId) {
    try {
      const raw = window.localStorage.getItem(`meowkeeper.vision.models.${providerId}`);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string" && v.length <= 120) : [];
    } catch {
      return [];
    }
  }

  function saveCachedModels(providerId, models) {
    try {
      window.localStorage.setItem(`meowkeeper.vision.models.${providerId}`, JSON.stringify(models));
    } catch { /* ignore storage errors */ }
  }

  function addDiscoveredModels(models) {
    const activeProvider = provider();
    if (Array.isArray(models) && models.length) {
      discoveredModels = models.filter((value) => typeof value === "string" && value.length <= 120);
      saveCachedModels(activeProvider, discoveredModels);
    } else if (!discoveredModels.length) {
      discoveredModels = loadCachedModels(activeProvider);
    }
    const select = byId("vision-model-choice");
    const selected = byId("vision-model").value.trim();
    const builtins = BUILTIN_MODELS[activeProvider] || [];
    const seen = new Set();
    select.replaceChildren(new Option("從常用或已探測模型清單選擇…", ""));
    const builtinGroup = document.createElement("optgroup");
    builtinGroup.label = activeProvider === "gemini" ? "Google Gemini 常用圖片模型" : "OpenAI 相容常用圖片模型";
    builtins.forEach(({ id, label }) => {
      seen.add(id);
      builtinGroup.appendChild(new Option(label, id));
    });
    select.appendChild(builtinGroup);
    const extraDiscovered = discoveredModels.filter((model) => !seen.has(model));
    if (extraDiscovered.length) {
      const discoveredGroup = document.createElement("optgroup");
      discoveredGroup.label = "服務商連線回傳模型";
      extraDiscovered.forEach((model) => {
        seen.add(model);
        discoveredGroup.appendChild(new Option(model, model));
      });
      select.appendChild(discoveredGroup);
    }
    select.add(new Option("✎ 自訂模型名稱（於下方欄位輸入）", "__custom__"));
    if (selected && seen.has(selected)) {
      select.value = selected;
    } else if (selected) {
      select.value = "__custom__";
    } else {
      select.value = "";
    }
  }

  function render(data) {
    current = data;
    const values = data.settings;
    const selectedProvider = form.querySelector(`input[name="vision-provider"][value="${CSS.escape(values.provider_id)}"]`);
    if (selectedProvider) selectedProvider.checked = true;
    byId("vision-model").value = values.model_id || "";
    byId("vision-endpoint").value = values.endpoint || "";
    byId("vision-enabled").checked = Boolean(values.enabled);
    byId("vision-json").checked = Boolean(values.json_object_supported);
    byId("vision-grace").value = values.grace_seconds;
    byId("vision-daily-limit").value = values.daily_request_limit;
    byId("vision-rpm-limit").value = values.requests_per_minute_limit;
    byId("vision-warning-threshold").value = values.warning_threshold_percent;
    byId("vision-provider-daily-limit").value = values.provider_daily_request_limit;
    byId("vision-provider-rpm-limit").value = values.provider_requests_per_minute_limit;
    byId("vision-provider-tpm-limit").value = values.provider_tokens_per_minute_limit;
    byId("vision-token-estimate").value = values.estimated_tokens_per_request;
    byId("vision-frame-count").value = values.storyboard_frame_count;
    byId("vision-canvas-size").value = values.storyboard_size;
    byId("vision-media-resolution").value = values.storyboard_media_resolution || "auto";
    byId("vision-cooldown").value = values.event_merge_cooldown_seconds || 0;
    byId("vision-jpeg-quality").value = values.storyboard_jpeg_quality;
    byId("vision-timestamps").checked = Boolean(values.storyboard_timestamps);
    byId("vision-zones").checked = Boolean(values.storyboard_include_zones);
    byId("vision-consent").checked = Boolean(values.enabled && values.consent_reference);
    byId("vision-scene-references").checked = Boolean(values.storyboard_include_scene_references);
    byId("vision-scene-reference-consent").checked = Boolean(values.storyboard_include_scene_references
      && values.scene_reference_consent_reference);
    syncSceneReferenceConsent();
    updateGeminiEndpoint();
    addDiscoveredModels(discoveredModels);
    updateRangeLabels();
    destination();
    byId("vision-capabilities").textContent = "只傳靜態逐貓圖片拼貼，不上傳短影片、不執行工具；模型清單不代表支援圖片，請依服務商文件確認。";
    const credentialBackend = data.credential_backend === "windows_dpapi_fallback"
      ? "Windows 帳號加密（DPAPI）"
      : "Windows 安全憑證庫";
    const keyStatus = data.key_configured ? `已安全保存（${credentialBackend}）` : "尚未保存";
    message(`${values.enabled ? "目前已啟用" : "目前未啟用"}｜API Key ${keyStatus}。${data.message_zh_tw}`);
    renderOverview(data);
    renderPolicy(data);
  }

  function refreshStatus(data) {
    current = data;
    renderOverview(data);
    renderPolicy(data);
  }

  function renderOverview(data) {
    const values = data.settings;
    const policy = data.policy;
    let state = "等待設定";
    let tone = "status-unknown";
    if (!values.endpoint || !values.model_id) { state = "設定未完成"; tone = "status-warn"; }
    else if (!values.enabled) { state = "未啟用"; tone = "status-info"; }
    else if (!values.consent_reference) { state = "等待圖片授權"; tone = "status-warn"; }
    else if (policy?.daily?.remaining === 0) { state = "今日預算已用完"; tone = "status-warn"; }
    else if (policy?.circuit_open) { state = "服務暫時異常"; tone = "status-error"; }
    else { state = "已設定，等待事件"; tone = "status-ok"; }
    const health = data.health_check || { status: "not_checked" };
    byId("vision-overview-status").className = `status-pill ${tone}`;
    byId("vision-overview-status").textContent = state;
    byId("vision-summary-state").textContent = state;
    byId("vision-summary-health").textContent = `${statusLabels[health.status] || "檢查狀態未知"}${health.http_status ? ` · HTTP ${health.http_status}` : ""}`;
    byId("vision-summary-model").textContent = values.model_id || "尚未選擇";
    byId("vision-summary-provider").textContent = values.provider_id === "gemini" ? "Google Gemini" : "OpenAI 格式相容服務";
    const daily = policy?.daily;
    byId("vision-summary-success").textContent = daily ? String(daily.successful) : "—";
    byId("vision-summary-last-success").textContent = daily?.last_success_at
      ? `最近成功：${new Date(daily.last_success_at).toLocaleString()}` : "尚無成功紀錄";
    byId("vision-summary-budget").textContent = daily ? `${daily.used} / ${daily.limit} 次` : `0 / ${values.daily_request_limit} 次`;
    byId("vision-summary-remaining").textContent = daily
      ? (daily.used === 0 ? "尚無使用紀錄" : `本機估計還可安排 ${daily.remaining} 次`) : "本執行環境沒有用量帳本";
  }

  function renderPolicy(data) {
    const values = data.settings;
    const policy = data.policy;
    if (!policy) {
      byId("vision-policy-status").textContent = "本執行環境沒有 SQLite 用量帳本；不補造資料。";
      byId("vision-reset-note").textContent = "無法讀取本機用量帳本";
      return;
    }
    const daily = policy.daily;
    const rpm = policy.rpm;
    const tpm = policy.tpm;
    const dailyPct = daily.limit ? Math.min(100, daily.used / daily.limit * 100) : 0;
    const rpmPct = rpm.limit ? Math.min(100, rpm.used / rpm.limit * 100) : 0;
    byId("vision-quota-daily").textContent = `${daily.used} / ${daily.limit}`;
    byId("vision-quota-daily-progress").value = dailyPct;
    byId("vision-quota-daily-note").textContent = daily.used === 0
      ? "尚無使用紀錄。"
      : `本機預算剩 ${daily.remaining} 次${daily.used / Math.max(1, daily.limit) >= values.warning_threshold_percent / 100 ? " · 已達提醒門檻" : ""}`;
    byId("vision-quota-rpm").textContent = `${rpm.used} / ${rpm.limit}`;
    byId("vision-quota-rpm-progress").value = rpmPct;
    const actualTokens = tpm.input_actual + tpm.output_actual;
    const tokenText = [actualTokens ? `${actualTokens.toLocaleString()} 實際` : "",
      tpm.estimated ? `${tpm.estimated.toLocaleString()} 估算` : "",
      tpm.unknown_requests ? `${tpm.unknown_requests} 筆未知` : ""].filter(Boolean).join(" · ");
    byId("vision-quota-tpm").textContent = tpm.limit > 0 ? `${(actualTokens + tpm.estimated).toLocaleString()} / ${tpm.limit.toLocaleString()}` : (tokenText || "尚無資料");
    byId("vision-quota-tpm-progress").value = tpm.limit > 0 ? Math.min(100, (actualTokens + tpm.estimated) / tpm.limit * 100) : 0;
    byId("vision-quota-tpm-note").textContent = tokenText || "尚無服務商實際 Token 用量回報。";
    const todayActual = daily.input_tokens_actual + daily.output_tokens_actual;
    byId("vision-perf-today-tokens").textContent = todayActual || daily.tokens_estimated
      ? `${todayActual.toLocaleString()} 實際${daily.tokens_estimated ? ` · ${daily.tokens_estimated.toLocaleString()} 估算` : ""}` : "尚無紀錄";
    byId("vision-reset-note").textContent = `每日重置：${policy.reset_timezone} · ${policy.quota_day}；為本機統計，不是服務商即時同步`;
    const providerLimits = [];
    if (values.provider_daily_request_limit) providerLimits.push(`服務商手動填寫每日 ${values.provider_daily_request_limit} 次`);
    if (values.provider_requests_per_minute_limit) providerLimits.push(`每分鐘 ${values.provider_requests_per_minute_limit} 次`);
    if (values.provider_tokens_per_minute_limit) providerLimits.push(`每分鐘 ${values.provider_tokens_per_minute_limit.toLocaleString()} Token`);
    byId("vision-policy-status").textContent = `本機防護：今日成功 ${daily.successful} 次、已安排 ${daily.used} 次、額度略過 ${daily.skipped_budget} 次、事件冷卻略過 ${daily.skipped_cooldown || 0} 次、逾時 ${daily.timeouts} 次；最近 1 分鐘 ${rpm.used}/${rpm.limit} 請求。${providerLimits.length ? `服務商手動上限：${providerLimits.join("、")}。` : "服務商上限未知，沒有假設官方配額。"}${policy.key_fix_required ? "目前金鑰或授權需修正。" : policy.circuit_open ? `服務暫時冷卻，約 ${policy.retry_in_seconds} 秒後才允許一次試探。` : "失敗不會阻塞本機監控與 Telegram 原始影片通知。"}`;
  }

  function setQualityPreset(name) {
    const presets = {
      economy: { daily: 300, rpm: 8, frames: 4, quality: 75, size: "1280x960", resolution: "low", grace: 3 },
      balanced: { daily: 400, rpm: 12, frames: 4, quality: 82, size: "1536x1024", resolution: "auto", grace: 5 },
      detail: { daily: 400, rpm: 12, frames: 6, quality: 88, size: "2048x1365", resolution: "high", grace: 8 },
    };
    const preset = presets[name];
    if (!preset) return;
    byId("vision-daily-limit").value = Math.min(preset.daily, Number(byId("vision-provider-daily-limit").value) || preset.daily);
    byId("vision-rpm-limit").value = Math.min(preset.rpm, Number(byId("vision-provider-rpm-limit").value) || preset.rpm);
    byId("vision-frame-count").value = preset.frames;
    byId("vision-jpeg-quality").value = preset.quality;
    byId("vision-canvas-size").value = preset.size;
    byId("vision-media-resolution").value = preset.resolution;
    byId("vision-grace").value = preset.grace;
    form.dataset.qualityPreset = name;
    updateRangeLabels();
    message(`已選擇「${name === "economy" ? "節省用量" : name === "detail" ? "重視細節" : "平衡"}」草稿；按「保存設定」才會套用。這是初始建議，不代表已實測為最佳值。`);
  }

  function formatMs(value) {
    return value == null ? "—" : value >= 1000 ? `${(value / 1000).toFixed(2)} 秒` : `${Math.round(value)} 毫秒`;
  }

  function drawBars(container, buckets, valueKey, title, secondaryKey = null) {
    container.replaceChildren();
    if (!buckets?.length || !buckets.some((bucket) => (bucket[valueKey] || 0) || (secondaryKey && bucket[secondaryKey]))) {
      const empty = document.createElement("p");
      empty.className = "vision-empty";
      empty.textContent = "尚無使用紀錄。";
      container.appendChild(empty);
      return;
    }
    const maxValue = Math.max(1, ...buckets.map((bucket) => (bucket[valueKey] || 0) + (secondaryKey ? bucket[secondaryKey] || 0 : 0)));
    const visible = buckets.length > 48 ? buckets.filter((_item, index) => index % Math.ceil(buckets.length / 48) === 0) : buckets;
    visible.forEach((bucket) => {
      const bar = document.createElement("div");
      bar.className = "vision-bar-group";
      const primary = document.createElement("span");
      primary.className = "vision-bar actual";
      primary.style.height = `${Math.max(0, (bucket[valueKey] || 0) / maxValue * 100)}%`;
      const secondary = document.createElement("span");
      secondary.className = "vision-bar estimated";
      secondary.style.height = `${Math.max(0, secondaryKey ? (bucket[secondaryKey] || 0) / maxValue * 100 : 0)}%`;
      const label = document.createElement("small");
      label.textContent = new Date(bucket.start).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
      bar.title = `${new Date(bucket.start).toLocaleString()}｜${title}: ${(bucket[valueKey] || 0).toLocaleString()}${secondaryKey ? `｜估算 ${(bucket[secondaryKey] || 0).toLocaleString()}` : ""}`;
      bar.append(primary, secondary, label);
      container.appendChild(bar);
    });
  }

  function recordSummary(item, detail) {
    if (!detail) return item.event_id ? "事件細節尚未記錄" : "非事件請求／略過紀錄";
    const identity = detail.event_identity?.status === "unknown"
      ? "身分未確認" : detail.event_identity?.name || "身分未確認";
    const tags = (detail.tracks || []).flatMap((track) => (track.tags || []).map((tag) => tag.label)).slice(0, 4);
    const telegram = detail.telegram
      ? `Telegram ${detail.telegram.accepted_uploads || 0}/${detail.telegram.upload_attempts || 0}`
      : `Telegram ${detail.telegram_status || "無回條"}`;
    return [identity, ...tags, telegram].join(" · ");
  }

  function matchesRecord(item, detail, filters) {
    if (filters.status === "completed" && item.outcome !== "completed") return false;
    if (filters.status === "attention" && item.outcome === "completed") return false;
    const candidates = (detail?.tracks || []).flatMap((track) => track.identity_candidates || []);
    const hasIdentity = detail?.event_identity?.status === "recorded" || candidates.length > 0;
    if (filters.identity === "identified" && !hasIdentity) return false;
    if (filters.identity === "unknown" && hasIdentity) return false;
    const telegram = detail?.telegram;
    const explicitTelegramStatus = detail?.telegram_status && detail.telegram_status !== "disabled"
      ? detail.telegram_status : null;
    const hasTelegram = Boolean((telegram && (telegram.upload_attempts > 0 || telegram.accepted_uploads > 0))
      || explicitTelegramStatus);
    const accepted = Boolean(telegram?.accepted_uploads > 0 || explicitTelegramStatus === "sent");
    if (filters.telegram === "accepted" && !accepted) return false;
    if (filters.telegram === "failed" && (!hasTelegram || accepted)) return false;
    if (filters.telegram === "none" && hasTelegram) return false;
    if (filters.search) {
      const searchable = [item.time, item.model, outcomeLabels[item.outcome] || item.outcome,
        detail?.event_type, detail?.event_identity?.name, detail?.telegram_status,
        detail?.analysis?.provider_id, detail?.analysis?.model_id,
        ...candidates.map((candidate) => candidate.name),
        ...(detail?.tracks || []).flatMap((track) => (track.tags || []).map((tag) => tag.label))]
        .filter(Boolean).join(" ").toLocaleLowerCase();
      if (!searchable.includes(filters.search)) return false;
    }
    return true;
  }

  function renderUsageRows() {
    const tbody = byId("vision-usage-rows");
    tbody.replaceChildren();
    const filters = {
      search: byId("vision-record-search").value.trim().toLocaleLowerCase(),
      status: byId("vision-record-status").value,
      identity: byId("vision-record-identity").value,
      telegram: byId("vision-record-telegram").value,
    };
    const visibleRows = usageRows.filter((item) => matchesRecord(item, usageEventDetails[item.event_id], filters));
    byId("vision-record-count").textContent = `符合 ${visibleRows.length} 筆｜已載入 ${usageRows.length} / ${usageTotal} 筆（${usageHours === 1 ? "1 小時" : usageHours === 24 ? "24 小時" : usageHours === 168 ? "7 天" : "30 天"}）${usageError ? `｜更新失敗：${usageError}` : ""}`;
    byId("vision-usage-empty").hidden = visibleRows.length > 0;
    byId("vision-usage-empty").textContent = usageRows.length
      ? "目前篩選條件沒有符合的紀錄。"
      : usageError || "尚無使用紀錄。";
    byId("vision-usage-table").hidden = visibleRows.length === 0;
    byId("vision-usage-more").hidden = !usageHasMore || usageLoading;
    visibleRows.forEach((item) => {
      const detail = usageEventDetails[item.event_id];
      const row = document.createElement("tr");
      const cells = [new Date(item.time).toLocaleString(), item.model || "未知模型",
        outcomeLabels[item.outcome] || "分析未完成", item.http_status ?? "—", formatMs(item.elapsed_ms),
        `${item.input_tokens ?? "—"} / ${item.output_tokens ?? "—"}${item.estimated_tokens ? `（估 ${item.estimated_tokens}）` : ""}`,
        item.request_dispatched == null ? "未知" : item.request_dispatched ? "已送出" : "未送出",
        recordSummary(item, detail)];
      const cellLabels = ["時間", "模型", "結果", "HTTP", "耗時", "Input／Output Tokens", "請求送出", "事件摘要"];
      cells.forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = String(value);
        cell.dataset.label = cellLabels[row.children.length];
        row.appendChild(cell);
      });
      const detailCell = document.createElement("td");
      const detailButton = document.createElement("button");
      detailButton.type = "button";
      detailButton.className = "button secondary";
      detailButton.textContent = item.event_id ? "查看" : "—";
      detailButton.disabled = !item.event_id || !detail;
      detailButton.setAttribute("aria-expanded", "false");
      detailCell.dataset.label = "細節";
      detailCell.appendChild(detailButton);
      row.appendChild(detailCell);
      tbody.appendChild(row);
      const detailRow = document.createElement("tr");
      detailRow.className = "vision-detail-row";
      detailRow.hidden = true;
      const detailCellBody = document.createElement("td");
      detailCellBody.colSpan = 9;
      detailCellBody.className = "vision-event-detail-cell";
      detailRow.appendChild(detailCellBody);
      tbody.appendChild(detailRow);
      detailButton.addEventListener("click", () => {
        const opening = detailRow.hidden;
        detailRow.hidden = !opening;
        detailButton.setAttribute("aria-expanded", String(opening));
        if (opening && !detailCellBody.childElementCount) renderEventDetail(detailCellBody, detail);
      });
    });
  }

  function renderUsage(data, { append = false } = {}) {
    const total = data.requests || 0;
    byId("vision-perf-requests").textContent = String(total);
    byId("vision-perf-success").textContent = total ? `${(data.successful / total * 100).toFixed(1)}%` : "—";
    byId("vision-perf-mean").textContent = formatMs(data.mean_elapsed_ms);
    byId("vision-perf-p95").textContent = formatMs(data.p95_elapsed_ms);
    byId("vision-perf-input").textContent = data.average_input_tokens == null ? "—" : data.average_input_tokens.toLocaleString();
    byId("vision-perf-estimated").textContent = `${(data.tokens_estimated || 0).toLocaleString()} / ${data.tokens_unknown || 0} 未知`;
    const counts = data.counts || {};
    byId("vision-perf-skipped").textContent = String((counts.skipped_budget || 0) + (counts.skipped_rpm || 0) + (counts.skipped_tpm || 0) + (counts.skipped_cooldown || 0));
    byId("vision-perf-timeouts").textContent = String(counts.timeout || 0);
    drawBars(byId("vision-request-chart"), data.buckets, "requests", "請求數", "failed");
    drawBars(byId("vision-token-chart"), data.buckets, "input_tokens", "實際 Input Token", "estimated_tokens");
    drawBars(byId("vision-latency-chart"), data.buckets, "mean_elapsed_ms", "平均回應毫秒");
    drawBars(byId("vision-skipped-chart"), data.buckets.map((bucket) => ({ ...bucket,
      skipped_total: bucket.skipped_budget + bucket.skipped_rpm + bucket.skipped_tpm + bucket.skipped_busy + (bucket.skipped_cooldown || 0) })),
    "skipped_total", "略過事件數");
    const modelList = byId("vision-model-usage");
    modelList.replaceChildren();
    const modelRows = data.model_usage || [];
    if (!modelRows.length || !modelRows.some((row) => row.requests > 0)) {
      const empty = document.createElement("p"); empty.className = "vision-empty"; empty.textContent = "尚無使用紀錄。";
      modelList.appendChild(empty);
    } else {
      const modelTotal = modelRows.reduce((sum, row) => sum + row.requests, 0);
      modelRows.forEach((row) => {
        const line = document.createElement("div"); line.className = "vision-model-row";
        const label = document.createElement("span"); label.textContent = row.model || "未知模型";
        const progress = document.createElement("progress"); progress.max = 100;
        progress.value = Math.min(100, row.requests / Math.max(1, modelTotal) * 100);
        const value = document.createElement("strong"); value.textContent = `${row.requests} 次 · ${(row.requests / Math.max(1, modelTotal) * 100).toFixed(1)}%`;
        line.append(label, progress, value); modelList.appendChild(line);
      });
    }
    if (!append) {
      usageRows = [];
      usageEventDetails = {};
    }
    usageRows.push(...(data.recent || []));
    Object.assign(usageEventDetails, data.event_details || {});
    usageTotal = Number.isInteger(data.recent_total) ? data.recent_total : usageRows.length;
    usageHasMore = Boolean(data.recent_has_more);
    renderUsageRows();
  }

  function renderEventDetail(container, detail) {
    const heading = document.createElement("strong");
    heading.textContent = `${detail.event_type || "事件"} · ${detail.trigger_at ? new Date(detail.trigger_at).toLocaleString() : "時間未知"}`;
    container.appendChild(heading);
    const line = (label, value) => {
      const paragraph = document.createElement("p");
      const name = document.createElement("b"); name.textContent = `${label}：`;
      const text = document.createElement("span"); text.textContent = String(value ?? "未記錄");
      paragraph.append(name, text); container.appendChild(paragraph);
    };
    line("事件身分紀錄", detail.event_identity?.status === "unknown" ? "尚未確認" : detail.event_identity?.name || "尚未確認");
    line("信心分數", detail.confidence == null ? "未記錄" : `${(Number(detail.confidence) * 100).toFixed(1)}%`);
    line("影片／通知", `${detail.clip_status || "未記錄"}／${detail.telegram_status || "未記錄"}`);
    (detail.tracks || []).forEach((track, index) => {
      line(`軌跡 ${index + 1}（AI 身分候選）`, (track.identity_candidates || []).length
        ? track.identity_candidates.map((candidate) => `${candidate.name} ${Math.round(candidate.score * 100)}%${candidate.experimental ? "（實驗性候選）" : "（人工回報）"}`).join("、")
        : "尚無身分候選；不代表已確認");
      line(`軌跡 ${index + 1} 動作候選`, (track.tags || []).length
        ? track.tags.map((tag) => `${tag.label}（候選）`).join("、") : "尚無標籤");
    });
    if (detail.analysis) {
      const timingLabels = { queue_ms: "排隊", preparation_ms: "圖片準備", provider_call_ms: "服務呼叫", api_response_ms: "API 回應", validation_ms: "結果檢查" };
      line("分析狀態", `${detail.analysis.status} · ${detail.analysis.provider_id}/${detail.analysis.model_id}`);
      line("各階段耗時", Object.entries(detail.analysis.timings_ms || {}).map(([key, value]) => `${timingLabels[key] || key} ${formatMs(value)}`).join(" · ") || "未記錄");
    }
    if (detail.telegram) {
      const caption = detail.telegram.caption_source === "ai_candidate" ? "含 AI 候選文案" : detail.telegram.caption_source === "local" ? "本機文案" : "未記錄";
      line("Telegram 影片準備", `文案 ${caption} · AI 等待 ${formatMs(detail.telegram.extra_wait_ms)}／上限 ${formatMs(detail.telegram.wait_budget_ms)}`);
      line("Telegram 送出", `${detail.telegram.accepted_uploads || 0}/${detail.telegram.upload_attempts || 0} 次取得成功回條；影片準備 ${formatMs(detail.telegram.video_prepare_ms)}；轉碼 ${formatMs(detail.telegram.transcode_ms)}`);
    }
    renderParallelTimeline(container, detail.timeline);
    const note = document.createElement("small"); note.textContent = "身分與動作為候選／人工紀錄，不等於已校準的辨識結論；明細不包含影像、影片路徑、收件者或訊息內容。";
    container.appendChild(note);
  }

  function renderParallelTimeline(container, timeline) {
    const section = document.createElement("section");
    section.className = "vision-parallel-timeline";
    const heading = document.createElement("h6"); heading.textContent = "事件並行時間軸";
    section.appendChild(heading);
    const note = document.createElement("p");
    note.textContent = timeline?.message_zh_tw || "沒有可用的時間軸資料。";
    section.appendChild(note);
    const span = Number(timeline?.span_ms);
    if (Number.isFinite(span) && span > 0) {
      (timeline.lanes || []).forEach((lane) => {
        const laneRow = document.createElement("div"); laneRow.className = "vision-timeline-lane";
        const label = document.createElement("strong"); label.textContent = lane.label || "流程";
        const track = document.createElement("div"); track.className = "vision-timeline-track";
        track.setAttribute("role", "img");
        track.setAttribute("aria-label", `${label.textContent}時間軸`);
        const legend = document.createElement("div"); legend.className = "vision-timeline-legend";
        (lane.segments || []).forEach((segment) => {
          const start = Number(segment.offset_ms);
          const duration = Number(segment.duration_ms);
          if (!Number.isFinite(start) || !Number.isFinite(duration) || start < 0 || duration <= 0) return;
          const bar = document.createElement("span");
          bar.className = `vision-timeline-segment is-${segment.status === "failed" ? "failed" : "done"}`;
          const left = Math.max(0, Math.min(100, start / span * 100));
          const width = Math.max(0.3, Math.min(100 - left, duration / span * 100));
          bar.style.left = `${left}%`;
          bar.style.width = `${width}%`;
          bar.title = `${segment.label || "階段"} · ${formatMs(duration)} · 從事件起點 ${formatMs(start)}`;
          bar.setAttribute("aria-label", bar.title);
          track.appendChild(bar);
          const legendItem = document.createElement("span");
          legendItem.className = `vision-timeline-key is-${segment.status === "failed" ? "failed" : "done"}`;
          legendItem.textContent = `${segment.label || "階段"} · ${formatMs(duration)}`;
          legend.appendChild(legendItem);
        });
        laneRow.append(label, track);
        section.appendChild(laneRow);
        if (legend.childElementCount) section.appendChild(legend);
      });
      const axis = document.createElement("small");
      axis.className = "vision-timeline-axis";
      axis.textContent = `起點 ${timeline.origin_at ? new Date(timeline.origin_at).toLocaleTimeString() : "—"}　0　→　${formatMs(span)}　（依實際時間戳；不同列可同時進行）`;
      section.appendChild(axis);
    }
    container.appendChild(section);
  }

  async function loadUsage({ append = false, refresh = false } = {}) {
    if (usageLoading) return;
    usageLoading = true;
    const previousRows = usageRows.slice();
    const previousDetails = { ...usageEventDetails };
    const offset = append ? usageRows.length : 0;
    byId("vision-usage-more").disabled = true;
    try {
      const data = await request(`/api/v1/vision/usage?hours=${usageHours}&limit=100&offset=${offset}`);
      usageError = "";
      renderUsage(data, { append });
      if (refresh && previousRows.length > 100) {
        usageEventDetails = { ...previousDetails, ...usageEventDetails };
        const seen = new Set(usageRows.map((item) => `${item.event_id || ""}|${item.time}|${item.model}|${item.outcome}`));
        previousRows.slice(100).forEach((item) => {
          const key = `${item.event_id || ""}|${item.time}|${item.model}|${item.outcome}`;
          if (!seen.has(key)) { usageRows.push(item); seen.add(key); }
        });
        usageHasMore = usageHasMore || usageRows.length < usageTotal;
        renderUsageRows();
      }
    }
    catch (error) {
      usageError = error.message;
    } finally { usageLoading = false; byId("vision-usage-more").disabled = false; }
    renderUsageRows();
  }

  function setTab(name) {
    const config = name === "config";
    byId("vision-config-panel").hidden = !config;
    byId("vision-performance-panel").hidden = config;
    document.querySelectorAll("[data-vision-tab]").forEach((button) => {
      const selected = button.dataset.visionTab === name;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    if (!config) loadUsage();
  }

  function payload() {
    const pickedProvider = provider();
    const model = byId("vision-model").value.trim();
    const endpoint = pickedProvider === "gemini"
      ? (/^[A-Za-z0-9_.-]{1,100}$/.test(model)
        ? `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent` : "")
      : byId("vision-endpoint").value.trim();
    const draft = {
      provider_id: pickedProvider, endpoint, model_id: model,
      enabled: byId("vision-consent").checked && byId("vision-enabled").checked,
      json_object_supported: byId("vision-json").checked,
      grace_seconds: Number(byId("vision-grace").value),
      daily_request_limit: Number(byId("vision-daily-limit").value),
      requests_per_minute_limit: Number(byId("vision-rpm-limit").value),
      tokens_per_minute_limit: current?.settings.tokens_per_minute_limit ?? 0,
      provider_daily_request_limit: Number(byId("vision-provider-daily-limit").value),
      provider_requests_per_minute_limit: Number(byId("vision-provider-rpm-limit").value),
      provider_tokens_per_minute_limit: Number(byId("vision-provider-tpm-limit").value),
      estimated_tokens_per_request: Number(byId("vision-token-estimate").value),
      warning_threshold_percent: Number(byId("vision-warning-threshold").value),
      quality_preset: form.dataset.qualityPreset || "custom",
      storyboard_frame_count: Number(byId("vision-frame-count").value),
      storyboard_size: byId("vision-canvas-size").value,
      storyboard_jpeg_quality: Number(byId("vision-jpeg-quality").value),
      storyboard_timestamps: byId("vision-timestamps").checked,
      storyboard_include_zones: byId("vision-zones").checked,
      storyboard_include_scene_references: byId("vision-scene-references").checked,
      storyboard_media_resolution: byId("vision-media-resolution").value,
      event_merge_cooldown_seconds: Number(byId("vision-cooldown").value),
      confirm_upload: byId("vision-consent").checked,
      confirm_scene_reference_upload: byId("vision-scene-reference-consent").checked,
    };
    if (current && ["provider_id", "endpoint", "model_id"].some(key => draft[key] !== current.settings[key])) {
      // Do not copy unchanged controls from the previous model over the target
      // model's saved profile. Changed fields are explicit edits and are sent.
      profileFields.forEach(key => { if (draft[key] === current.settings[key]) delete draft[key]; });
    }
    return draft;
  }

  function syncSceneReferenceConsent() {
    const include = byId("vision-scene-references").checked;
    const consent = byId("vision-scene-reference-consent");
    consent.disabled = !include;
    if (!include) consent.checked = false;
  }

  byId("vision-scene-references").addEventListener("change", syncSceneReferenceConsent);

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const modelDraft = byId("vision-model").value.trim();
    const enableDraft = byId("vision-enabled").checked && byId("vision-consent").checked;
    if (provider() === "gemini" && (modelDraft || enableDraft)
        && !/^[A-Za-z0-9_.-]{1,100}$/.test(modelDraft)) {
      message("Gemini 模型欄請從服務商文件複製模型 ID（英數字、點、底線或連字號），不可填含空格的顯示名稱。設定尚未送出。");
      byId("vision-model").focus();
      return;
    }
    const saveButton = form.querySelector('button[type="submit"]');
    if (saveButton.disabled) return;
    saveButton.disabled = true;
    saveButton.textContent = "正在保存…";
    message("正在保存設定…");
    try {
      const changedBoundary = current && (current.settings.provider_id !== provider()
        || current.settings.endpoint !== byId("vision-endpoint").value.trim()
        || current.settings.model_id !== byId("vision-model").value.trim());
      if (changedBoundary && current?.settings?.provider_id !== provider()) {
        discoveredModels = loadCachedModels(provider());
      }
      const saved = await request("/api/v1/vision/settings", "PUT", payload());
      render(saved);
      message(!saved.settings.endpoint || !saved.settings.model_id
        ? "草稿已保存。服務網址與模型 ID 填完整後，才能檢查連線或啟用圖片分析；目前不會送出圖片。"
        : changedBoundary && !saved.settings.enabled
        ? "已保存服務／模型與本次修改；未修改的額度與品質沿用該模型的設定。目前保持關閉；確認圖片目的地並重新勾選同意後再啟用。"
        : `${saved.settings.enabled ? "設定已保存並啟用" : "設定已保存；AI 分析目前關閉"}。${saved.message_zh_tw}`);
    } catch (error) { message(error.message); }
    finally {
      saveButton.disabled = false;
      saveButton.textContent = "保存設定";
    }
  });

  async function ensureDestinationSaved() {
    if (!current) return true;
    const draftProvider = provider();
    const draftModel = byId("vision-model").value.trim();
    const draftEndpoint = byId("vision-endpoint").value.trim();
    if (current.settings.provider_id === draftProvider
        && current.settings.endpoint === draftEndpoint
        && current.settings.model_id === draftModel) {
      return true;
    }
    if (!draftModel || !draftEndpoint) return false;
    const saved = await request("/api/v1/vision/settings", "PUT", payload());
    render(saved);
    return true;
  }

  byId("vision-health-check").addEventListener("click", async () => {
    try {
      const ok = await ensureDestinationSaved();
      if (!ok) {
        byId("vision-health-result").textContent = "請先選擇或填入圖片模型與服務網址，再安全保存該服務的 Key。";
        return;
      }
    } catch (error) {
      byId("vision-health-result").textContent = error.message;
      return;
    }
    if (!window.confirm("將以已保存的網址、模型和金鑰讀取服務商模型清單；不會傳送居家圖片。要繼續嗎？")) return;
    const button = byId("vision-health-check");
    button.disabled = true;
    byId("vision-health-result").textContent = "正在查詢服務商…";
    try {
      const result = await request("/api/v1/vision/health", "POST", { confirm_metadata_request: true });
      if (Array.isArray(result.models)) addDiscoveredModels(result.models);
      byId("vision-health-result").textContent = `${statusLabels[result.status] || result.status}${result.http_status ? ` · HTTP ${result.http_status}` : ""}。模型清單只代表服務商有列出，不代表支援圖片。`;
      if (result.message_zh_tw) message(result.message_zh_tw);
      refreshStatus(await request("/api/v1/vision/settings"));
    } catch (error) {
      byId("vision-health-result").textContent = error.message;
      message(error.message);
    } finally { button.disabled = false; }
  });

  byId("vision-image-test").addEventListener("click", async () => {
    try {
      const ok = await ensureDestinationSaved();
      if (!ok) {
        byId("vision-image-test-result").textContent = "請先選擇或填入服務網址與模型，並安全保存需要的 Key，再執行測試。";
        return;
      }
    } catch (error) {
      byId("vision-image-test-result").textContent = error.message;
      return;
    }
    if (!window.confirm("將傳送一張程式在本機繪製的合成貓插圖到目前服務，測試圖片 API 是否能回應。不是你的攝影機畫面；服務商仍可能依其方案計費。要繼續嗎？")) return;
    const button = byId("vision-image-test");
    button.disabled = true;
    byId("vision-image-test-result").textContent = "正在送出合成示範圖；不會讀取攝影機影格…";
    try {
      const result = await request("/api/v1/vision/image-test", "POST", { confirm_demo_upload: true });
      const labels = { completed: "示範圖已收到並解析服務回應", auth_error: "金鑰或授權需確認", rate_limited: "服務暫時限流", timeout: "服務逾時", unavailable: "服務無法連線", bad_response: "服務回應格式無法辨識", skipped_budget: "已達本機每日預算", skipped_rpm: "已達每分鐘請求預算", skipped_tpm: "已達每分鐘 Token 預算", token_estimate_required: "需要設定單次 Token 預估", circuit_open: "服務暫時冷卻", skipped_busy: "已有一筆分析工作" };
      byId("vision-image-test-result").textContent = `${labels[result.status] || "測試未完成"}${result.http_status ? ` · HTTP ${result.http_status}` : ""}${result.elapsed_ms != null ? ` · ${result.elapsed_ms} ms` : ""}。${result.message_zh_tw || ""}`;
      refreshStatus(await request("/api/v1/vision/settings"));
      loadUsage();
    } catch (error) {
      byId("vision-image-test-result").textContent = error.message;
    } finally { button.disabled = false; }
  });

  byId("vision-count-tokens").addEventListener("click", async () => {
    if (provider() !== "gemini") {
      byId("vision-count-tokens-result").textContent = "目前只支援 Gemini countTokens；沒有送出請求。";
      return;
    }
    try {
      const ok = await ensureDestinationSaved();
      if (!ok) {
        byId("vision-count-tokens-result").textContent = "請先選擇或填入 Gemini 模型與網址，再做 Token 探測。";
        return;
      }
    } catch (error) {
      byId("vision-count-tokens-result").textContent = error.message;
      return;
    }
    if (!window.confirm("將以程式繪製的一張合成示範圖呼叫 Gemini countTokens。這不會呼叫圖片模型推論，也不會讀取攝影機畫面；服務商仍可能套用 API 配額或政策。每服務每日最多 10 次、每分鐘最多 1 次。要繼續嗎？")) return;
    const button = byId("vision-count-tokens"); button.disabled = true;
    byId("vision-count-tokens-result").textContent = "正在以合成圖片查詢 Token 數…";
    try {
      const result = await request("/api/v1/vision/count-tokens", "POST", { confirm_synthetic_count_tokens: true });
      byId("vision-count-tokens-result").textContent = result.status === "completed"
        ? `合成單軌跡示範圖：${Number(result.total_tokens).toLocaleString()} Tokens · HTTP ${result.http_status || "—"} · ${result.elapsed_ms ?? "—"} ms。${result.message_zh_tw || ""}`
        : `${outcomeLabels[result.status] || result.status}${result.http_status ? ` · HTTP ${result.http_status}` : ""}。${result.message_zh_tw || ""}`;
    } catch (error) { byId("vision-count-tokens-result").textContent = error.message; }
    finally { button.disabled = false; }
  });

  byId("vision-key-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = byId("vision-key").value;
    byId("vision-key").value = "";
    try {
      await ensureDestinationSaved();
      const data = await request("/api/v1/vision/credential", "POST", { key, reauth: byId("vision-key-confirm").checked });
      refreshStatus(data);
      message("API Key 已安全保存；頁面不會回填金鑰。");
    }
    catch (error) { message(error.message); }
    byId("vision-key-confirm").checked = false;
  });

  byId("vision-key-toggle").addEventListener("click", () => {
    const input = byId("vision-key");
    const visible = input.type === "password";
    input.type = visible ? "text" : "password";
    byId("vision-key-toggle").setAttribute("aria-pressed", String(visible));
    byId("vision-key-toggle").textContent = visible ? "隱藏金鑰" : "顯示金鑰";
  });

  byId("vision-key-clear").addEventListener("click", async () => {
    if (!byId("vision-key-confirm").checked) { message("請先勾選確認修改或清除金鑰。"); return; }
    try {
      const data = await request("/api/v1/vision/credential", "POST", { clear: true, reauth: true });
      refreshStatus(data);
      message("已清除目前服務的 API Key；若服務需要金鑰，新的分析將不會送出。");
    }
    catch (error) { message(error.message); }
    byId("vision-key-confirm").checked = false;
  });

  document.querySelectorAll('input[name="vision-provider"]').forEach((radio) => radio.addEventListener("change", () => {
    if (provider() !== "gemini" && /^https:\/\/generativelanguage\.googleapis\.com\/v1beta\/models\/[A-Za-z0-9_.-]+:generateContent$/.test(byId("vision-endpoint").value)) {
      byId("vision-endpoint").value = "";
    }
    discoveredModels = loadCachedModels(provider());
    addDiscoveredModels(discoveredModels);
    updateGeminiEndpoint();
    byId("vision-consent").checked = false;
    byId("vision-scene-reference-consent").checked = false;
    destination();
  }));
  byId("vision-model").addEventListener("input", () => {
    updateGeminiEndpoint();
    byId("vision-consent").checked = false;
    byId("vision-scene-reference-consent").checked = false;
    const select = byId("vision-model-choice");
    const typed = byId("vision-model").value.trim();
    const knownOptions = new Set([...select.options].map((opt) => opt.value));
    select.value = typed && knownOptions.has(typed) ? typed : (typed ? "__custom__" : "");
    destination();
  });
  byId("vision-model-choice").addEventListener("change", () => {
    const choice = byId("vision-model-choice").value;
    if (choice === "__custom__") {
      byId("vision-model").focus();
      byId("vision-model").select();
      return;
    }
    if (choice) {
      byId("vision-model").value = choice;
      updateGeminiEndpoint();
      byId("vision-consent").checked = false;
      byId("vision-scene-reference-consent").checked = false;
      destination();
    }
  });
  byId("vision-media-resolution").addEventListener("change", () => { form.dataset.qualityPreset = "custom"; });
  byId("vision-canvas-size").addEventListener("change", () => { form.dataset.qualityPreset = "custom"; });
  byId("vision-endpoint").addEventListener("input", () => {
    byId("vision-consent").checked = false;
    byId("vision-scene-reference-consent").checked = false;
    destination();
  });
  byId("vision-consent").addEventListener("change", () => {
    if (!byId("vision-consent").checked) {
      byId("vision-enabled").checked = false;
      message("已撤回雲端圖片分析授權；保存後不會再送出新的圖片。已送到服務商的請求無法撤回。重新啟用前需再次確認授權。");
    }
  });
  document.querySelectorAll('input[type="range"]').forEach((input) => input.addEventListener("input", () => {
    updateRangeLabels();
    form.dataset.qualityPreset = "custom";
  }));
  document.querySelectorAll("[data-quality-preset]").forEach((button) => button.addEventListener("click", () => setQualityPreset(button.dataset.qualityPreset)));
  document.querySelectorAll("[data-vision-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.visionTab)));
  document.querySelectorAll("[data-vision-hours]").forEach((button) => button.addEventListener("click", () => {
    usageHours = Number(button.dataset.visionHours);
    document.querySelectorAll("[data-vision-hours]").forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
    loadUsage();
  }));
  ["vision-record-search", "vision-record-status", "vision-record-identity", "vision-record-telegram"]
    .forEach((id) => byId(id).addEventListener(id === "vision-record-search" ? "input" : "change", renderUsageRows));
  byId("vision-usage-more").addEventListener("click", () => loadUsage({ append: true }));
  document.addEventListener("keydown", (event) => {
    if (!event.target.matches('[role="tab"]') || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const tabs = [...document.querySelectorAll("[data-vision-tab]")];
    const index = tabs.indexOf(event.target);
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  });

  async function load() {
    try { render(await request("/api/v1/vision/settings")); }
    catch (error) { message(error.message); byId("vision-summary-state").textContent = "讀取失敗"; }
  }
  load();
  loadUsage();
  window.setInterval(() => {
    if (document.hidden) return;
    if (!byId("vision-performance-panel").hidden) loadUsage({ refresh: true });
    request("/api/v1/vision/settings").then(refreshStatus).catch(() => {});
  }, 30000);
})();
