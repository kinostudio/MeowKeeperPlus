/* One existing ingest JPEG or one go2rtc MSE consumer per viewing page. */
(() => {
  "use strict";
  const modes = {
    sleep: { label: "休眠", icon: "◉̸", help: "停止此頁影像、狀態輪詢與 WebSocket。" },
    energy: { label: "節能", icon: "🐢", help: "使用既有共享 JPEG，最高每秒輪詢 5 次。" },
    smooth: { label: "流暢", icon: "🚀", help: "優先原碼 WebRTC／MSE；不相容時共用 H.264 轉碼，仍失敗才回節能。轉碼會增加主機負載。" }
  };
  let viewerPageId = null;
  try { viewerPageId = window.MeowKeeperRequestId?.() || null; } catch (_) { /* viewer stays unidentified */ }

  function create(options) {
    let { image, empty, note, scope = "live", defaultMode = "energy", onMetadata = () => {}, onFrame = () => {}, onUnavailable = () => {} } = options;
    let stage = image.closest(".preview-stage, .overview-preview") || image.parentElement;
    let mode = defaultMode;
    let lastPlayback = {};
    const publishPlayback = detail => { lastPlayback = {...detail, audioRequested: audioEnabled, audioAvailable: audioEvidence?.available ?? null, audioState: audioButton.dataset.audioState}; if (stage.dispatchEvent && typeof CustomEvent === "function") stage.dispatchEvent(new CustomEvent("meowkeeper:playback", {detail:lastPlayback})); };
    const video = document.createElement("video");
    video.className = "preview-video";
    video.setAttribute("playsinline", "");
    video.muted = true;
    video.hidden = true;
    video.setAttribute("aria-label", "MeowKeeper+ 即時連續影片");
    stage.append(video);

    const hud = document.createElement("div");
    hud.className = "preview-mode-hud";
    hud.setAttribute("role", "group");
    hud.setAttribute("aria-label", "預覽播放模式");
    const buttons = new Map();
    for (const [key, data] of Object.entries(modes)) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preview-mode-button preview-mode-select";
      button.dataset.previewMode = key;
      button.setAttribute("aria-label", `${data.label}：${data.help}`);
      const tooltipId = `preview-mode-tooltip-${scope}-${key}`;
      button.setAttribute("aria-describedby", tooltipId);
      const icon = document.createElement("span");
      icon.className = "preview-mode-icon";
      icon.setAttribute("aria-hidden", "true");
      icon.textContent = data.icon;
      if (key === "sleep") icon.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 3l18 18M10.5 5.2A12 12 0 0121 12a15 15 0 01-3 3.7M6.2 6.2A16 16 0 003 12s3 7 9 7a11 11 0 005-1.3M10 10a3 3 0 004 4"/></svg>';
      const tooltip = document.createElement("span");
      tooltip.id = tooltipId;
      tooltip.className = "preview-mode-tooltip";
      tooltip.setAttribute("role", "tooltip");
      tooltip.textContent = `${data.label}｜${data.help}`;
      button.append(icon, tooltip);
      hud.append(button);
      buttons.set(key, button);
    }

    let audioEnabled = false, audioEvidence = null, connectedTransport = null;
    const audioButton = document.createElement("button");
    audioButton.type = "button";
    audioButton.className = "preview-mode-button preview-mode-select preview-audio-toggle";
    audioButton.dataset.previewControl = "audio";
    audioButton.setAttribute("aria-pressed", "false");
    const audioTooltipId = `preview-audio-tooltip-${scope}`;
    audioButton.setAttribute("aria-describedby", audioTooltipId);

    const audioIcon = document.createElement("span");
    audioIcon.className = "preview-mode-icon";
    audioIcon.setAttribute("aria-hidden", "true");

    const audioTooltip = document.createElement("span");
    audioTooltip.id = audioTooltipId;
    audioTooltip.className = "preview-mode-tooltip";
    audioTooltip.setAttribute("role", "tooltip");

    const mutedSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>';
    const soundSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg>';

    function updateAudioButton() {
      audioButton.setAttribute("aria-pressed", String(audioEnabled));
      audioButton.dataset.audioState = !audioEnabled ? "muted" : mode !== "smooth" ? "no-audio-mode" : audioEvidence?.available === false ? "unavailable" : audioEvidence?.available ? "track" : "pending";
      if (audioEnabled) {
        const state = mode !== "smooth" ? "目前模式沒有聲音，已記住收音偏好" : audioEvidence?.available === false ? "來源未提供可播放音軌" : audioEvidence?.available ? "已收到音軌，已解除靜音" : "已要求收音，等待音軌";
        audioButton.setAttribute("aria-label", `現場收音（${state}）：點擊切換為靜音`);
        audioTooltip.textContent = `現場收音｜${state}；聲音仍需確認裝置音量`;
        audioIcon.innerHTML = soundSvg;
      } else {
        audioButton.setAttribute("aria-label", "現場收音（已靜音）：點擊解除靜音");
        audioTooltip.textContent = "現場收音｜預設靜音；流暢影片可包含來源音軌，節能 JPEG 沒有聲音";
        audioIcon.innerHTML = mutedSvg;
      }
    }
    updateAudioButton();
    audioButton.append(audioIcon, audioTooltip);
    hud.append(audioButton);

    audioButton.addEventListener("click", async () => {
      audioEnabled = !audioEnabled;
      video.muted = !audioEnabled;
      if (audioEnabled) video.volume = 1.0;
      updateAudioButton();
      publishPlayback(lastPlayback);
      if (audioEnabled) {
        if (mode === "smooth") {
          if (connectedTransport === "WebRTC" && audioEvidence?.available === false) {
            msePlayer?.close(); msePlayer = null;
            say("WebRTC 沒有可轉送音軌，改試含音訊的 MSE；不另接攝影機。");
            startSmooth(true);
          } else {
            try { await video.play?.(); }
            catch (_) { audioEnabled = false; video.muted = true; updateAudioButton(); say("瀏覽器阻擋有聲播放；請再次點擊收音並檢查網站音量權限。"); return; }
            say(audioEvidence?.available === false ? "來源未提供此格式可播放的音軌；解除靜音不代表已收到聲音。" : "已要求收音；請以音軌狀態與實際聲音確認，並檢查系統音量。");
          }
        } else {
          say("已記住收音偏好；節能 JPEG 沒有音訊，請切換「流暢」播放。");
        }
      } else {
        video.muted = true;
        say("已關閉現場收音（靜音）。");
      }
    });

    const fsEnterSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>';
    const fsExitSvg = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/></svg>';

    const fullscreenButton = document.createElement("button");
    fullscreenButton.type = "button";
    fullscreenButton.className = "preview-mode-button preview-mode-select preview-fullscreen-toggle";
    fullscreenButton.dataset.previewControl = "fullscreen";
    fullscreenButton.setAttribute("aria-pressed", "false");
    const fsTooltipId = `preview-fullscreen-tooltip-${scope}`;
    fullscreenButton.setAttribute("aria-describedby", fsTooltipId);

    const fsIcon = document.createElement("span");
    fsIcon.className = "preview-mode-icon";
    fsIcon.setAttribute("aria-hidden", "true");

    const fsTooltip = document.createElement("span");
    fsTooltip.id = fsTooltipId;
    fsTooltip.className = "preview-mode-tooltip";
    fsTooltip.setAttribute("role", "tooltip");

    function isCurrentFullscreen() {
      const el = document.fullscreenElement || document.webkitFullscreenElement;
      return Boolean(el && (el === stage || stage.contains(el))) || stage.classList.contains("is-mobile-fullscreen");
    }

    function updateFullscreenButton() {
      const active = isCurrentFullscreen();
      fullscreenButton.setAttribute("aria-pressed", String(active));
      if (active) {
        fullscreenButton.setAttribute("aria-label", "退出全螢幕：返回一般視窗檢視");
        fsTooltip.textContent = "退出全螢幕｜點擊返回一般視窗畫面";
        fsIcon.innerHTML = fsExitSvg;
      } else {
        fullscreenButton.setAttribute("aria-label", "全螢幕：展開預覽畫面為全螢幕檢視");
        fsTooltip.textContent = "全螢幕｜點擊將即時預覽畫面切換為全螢幕";
        fsIcon.innerHTML = fsEnterSvg;
      }
    }
    updateFullscreenButton();
    fullscreenButton.append(fsIcon, fsTooltip);
    hud.append(fullscreenButton);

    async function toggleFullscreen() {
      try {
        if (stage.classList.contains("is-mobile-fullscreen")) {
          stage.classList.remove("is-mobile-fullscreen");
          document.body.classList.remove("is-mobile-fullscreen-active");
        } else if (isCurrentFullscreen()) {
          if (document.exitFullscreen) await document.exitFullscreen();
          else if (document.webkitExitFullscreen) await document.webkitExitFullscreen();
        } else {
          if (stage.requestFullscreen) await stage.requestFullscreen();
          else if (stage.webkitRequestFullscreen) await stage.webkitRequestFullscreen();
          else if (video.requestFullscreen) await video.requestFullscreen();
          else {
            stage.classList.add("is-mobile-fullscreen");
            document.body.classList.add("is-mobile-fullscreen-active");
          }
        }
      } catch (_) {
        stage.classList.toggle("is-mobile-fullscreen");
        document.body.classList.toggle("is-mobile-fullscreen-active", stage.classList.contains("is-mobile-fullscreen"));
      } finally {
        updateFullscreenButton();
      }
    }
    fullscreenButton.addEventListener("click", toggleFullscreen);
    document.addEventListener("fullscreenchange", updateFullscreenButton);
    if ("onwebkitfullscreenchange" in document) document.addEventListener("webkitfullscreenchange", updateFullscreenButton);
    function onFullscreenKeydown(e) {
      if (e.key === "Escape" && stage.classList.contains("is-mobile-fullscreen")) {
        stage.classList.remove("is-mobile-fullscreen");
        document.body.classList.remove("is-mobile-fullscreen-active");
        updateFullscreenButton();
      }
    }
    document.addEventListener("keydown", onFullscreenKeydown);



    const stats = document.createElement("div");
    stats.className = "preview-mode-stats";
    stats.setAttribute("role", "status");
    stats.setAttribute("aria-live", "polite");
    stats.textContent = "節能預覽｜等待來源影格";
    hud.append(stats);
    stage.append(hud);
    let overlay = window.MeowKeeperOverlay?.mount(stage, hud);
    let hudVisibility = window.MeowKeeperHud?.mount(stage);

    const sleepState = document.createElement("div");
    sleepState.className = "preview-sleep-state";
    sleepState.hidden = true;
    sleepState.innerHTML = '<span aria-hidden="true">◉̸</span><strong>預覽已休眠</strong><small>此頁沒有下載影格或連續影片；點選「節能」或「流暢」恢復。</small>';
    stage.append(sleepState);

    let storageKey = `meowkeeper-preview-mode:${scope}`;
    try { mode = localStorage.getItem(storageKey) || defaultMode; } catch (_) { /* storage is optional */ }
    if (!Object.prototype.hasOwnProperty.call(modes, mode)) mode = defaultMode;
    let metadata = null, metaAt = 0, metaBusy = false, frameBusy = false;
    let frameAbort = null, metaAbort = null, modeTimer = 0;
    let objectUrl = null, msePlayer = null, disposed = false, visible = !document.hidden;
    let fallbackReason = null;
    let connectedStream = null;
    let generation = 0;
    let displayCount = 0, bytesTotal = 0, bytesWindow = 0, byteWindowAt = performance.now(), frameWindowAt = performance.now();

    const say = text => { if (note) note.textContent = text; };
    const abortAll = () => {
      frameAbort?.abort(); frameAbort = null;
      metaAbort?.abort(); metaAbort = null;
      frameBusy = false; metaBusy = false;
    };
    const freshness = () => {
      const age = metadata?.frame_age_seconds;
      return Number.isFinite(age) && age >= 0 && age + (performance.now() - metaAt) / 1000 < 15
        && metadata?.source_connections !== 0
        && ["running", "streaming", "connected"].includes(metadata?.source_status);
    };
    function revokeJpeg() {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
      image.removeAttribute("src");
      image.hidden = true;
    }
    function closeMse() {
      msePlayer?.close();
      msePlayer = null;
      video.pause();
      video.removeAttribute("src");
      try { video.load(); } catch (_) { /* no-op */ }
      video.hidden = true;
    }
    function syncButtons() {
      for (const [key, button] of buttons) button.setAttribute("aria-pressed", String(key === mode));
    }
    function stopTimers() {
      if (modeTimer) clearTimeout(modeTimer);
      modeTimer = 0;
      abortAll();
    }
    function stopDisplay() {
      generation += 1;
      overlay?.clear();
      stopTimers();
      closeMse();
      revokeJpeg();
    }
    function setSleepState() {
      const sleeping = mode === "sleep" || !visible;
      sleepState.hidden = !sleeping || !visible;
      hud.hidden = !visible;
      if (empty && mode !== "sleep" && visible) empty.hidden = false;
      if (mode === "sleep") {
        image.hidden = true;
        video.hidden = true;
        stats.textContent = "休眠預覽｜0 FPS｜0 傳輸";
        say("預覽已休眠；此頁不再下載影格或連續影片，來源監控與錄影不受影響。" );
      }
    }
    async function readMetadata(force = false) {
      if (metaBusy || !visible || disposed || mode === "sleep") return metadata;
      if (!force && metadata && performance.now() - metaAt < 500) return metadata;
      metaBusy = true;
      const cycle = generation, controller = new AbortController();
      metaAbort = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const response = await fetch("/api/v1/preview/status", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 401 ? "登入已失效，請重新登入。" : "來源狀態尚未提供。");
        const value = await response.json();
        if (cycle !== generation || disposed || !visible || mode === "sleep") return null;
        metadata = value;
        metaAt = performance.now();
        if (mode === "smooth" && connectedStream && connectedStream !== metadata.stream_name) {
          overlay?.clear();
          fallBack("來源已切換，已停止舊影片並回到節能預覽；可重新選擇流暢播放。");
          return metadata;
        }
        onMetadata(metadata);
        if (freshness()) overlay?.update(metadata.overlay, mode === "smooth" ? video : image);
        else overlay?.clear();
      } catch (_) {
        if (cycle === generation && !disposed && visible && mode !== "sleep") {
          metadata = null;
          overlay?.clear();
          onMetadata(null);
        }
      } finally {
        clearTimeout(timeout);
        if (metaAbort === controller) { metaBusy = false; metaAbort = null; }
      }
      return metadata;
    }
    function updateJpegStats(blobBytes) {
      bytesTotal += blobBytes;
      bytesWindow += blobBytes;
      displayCount += 1;
      const now = performance.now();
      if (now - frameWindowAt >= 1000) {
        const fps = displayCount / ((now - frameWindowAt) / 1000);
        const mbps = bytesWindow * 8 / ((now - byteWindowAt) / 1000) / 1000000;
        stats.textContent = `節能預覽｜JPEG 更新 ${fps.toFixed(1)} 次/s｜${mbps.toFixed(2)} Mbps｜已收 ${(bytesTotal / 1000000).toFixed(2)} MB`;
        displayCount = 0; bytesWindow = 0; frameWindowAt = now; byteWindowAt = now;
      }
    }
    async function readJpeg() {
      if (frameBusy || !visible || disposed || mode !== "energy") return;
      frameBusy = true;
      const cycle = generation, controller = new AbortController();
      frameAbort = controller;
      const timeout = setTimeout(() => controller.abort(), 5000);
      try {
        const status = await readMetadata();
        if (cycle !== generation || disposed || !visible || mode !== "energy") return;
        if (!freshness()) {
          revokeJpeg();
          const text = status?.source_status === "running" ? "來源程序已啟動，但尚無 15 秒內的新影格。" : "等待來源開始提供新影格。";
          stats.textContent = `節能預覽｜等待新影格｜${status?.resolution || "解析度未確認"}`;
          say(text);
          onUnavailable(text);
          return;
        }
        const headers = { Accept: "image/jpeg" };
        if (viewerPageId) headers["X-MeowKeeper-Viewer-Id"] = viewerPageId;
        const response = await fetch("/api/v1/preview/jpeg", { cache: "no-store", headers, signal: controller.signal });
        if (!response.ok) throw new Error(response.status === 401 ? "登入已失效，請重新登入。" : "目前沒有共享影格；請確認來源狀態。");
        const blob = await response.blob();
        if (cycle !== generation || disposed || !visible || mode !== "energy") return;
        if (blob.type.split(";")[0] !== "image/jpeg" || blob.size < 4) throw new Error("收到的共享影格格式不正確。");
        const next = URL.createObjectURL(blob), previous = objectUrl;
        // Decode off-screen so a slow JPEG decode never blanks the last good frame.
        const decoded = document.createElement("img");
        decoded.decoding = "async";
        decoded.src = next;
        try { await decoded.decode(); } catch (_) {
          URL.revokeObjectURL(next);
          throw new Error("JPEG 影格無法解碼。");
        }
        if (cycle !== generation || disposed || !visible || mode !== "energy") {
          URL.revokeObjectURL(next);
          return;
        }
        image.src = next;
        image.hidden = false; video.hidden = true;
        overlay?.update(metadata?.overlay, image);
        if (empty) empty.hidden = true;
        objectUrl = next;
        if (previous) URL.revokeObjectURL(previous);
        updateJpegStats(blob.size);
        const fresh = freshness();
        const frameNote = fresh ? "節能模式使用本機共享影格；最多每秒更新 5 次，不新增攝影機連線。" : "影格可讀，但時效未確認；不標示為即時成功。";
        say(fallbackReason ? `${fallbackReason} ${frameNote}` : frameNote);
        onFrame({ fresh, metadata, width: image.naturalWidth, height: image.naturalHeight, mode: "energy" });
      } catch (error) {
        if (cycle === generation && !disposed && visible && mode === "energy") {
          const text = error?.name === "AbortError" ? "共享影格回應逾時；稍後自動重試。" : (error?.message || "共享預覽暫時無法讀取。");
          say(text); stats.textContent = "節能預覽｜等待影格"; onUnavailable(text);
        }
      } finally {
        clearTimeout(timeout);
        if (frameAbort === controller) { frameBusy = false; frameAbort = null; }
        if (cycle === generation && !disposed && visible && mode === "energy") modeTimer = setTimeout(readJpeg, 200);
      }
    }
    function startSmooth(useMse = false, compatibility = false) {
      const cycle = generation;
      revokeJpeg();
      if (empty) empty.hidden = true;
      if (!window.MeowKeeperMSE) { fallBack("此瀏覽器未載入 MSE 播放器；已改回節能預覽。"); return; }
      if (!freshness() || metadata?.source_is_xiaomi !== true || !metadata?.stream_name) {
        fallBack("目前沒有可驗證的本機 Xiaomi 連續來源；已改回節能預覽。"); return;
      }
      const sourceCodec=String(metadata.original_source?.video_codec||"").toLowerCase();
      const hevcSource=/^(hevc|h265|h\.265|hvc1|hev1)$/.test(sourceCodec);
      const hevcSupported=window.MeowKeeperMSE.supportedCodecs?.().some(codec=>codec.startsWith("hvc1."))===true;
      if(hevcSource&&!compatibility&&!hevcSupported){
        fallbackReason="瀏覽器未宣告支援此 HEVC 原碼格式，直接準備共用 H.264 相容影片。";
        startSmooth(true,true);return;
      }
      video.hidden = false;
      connectedStream = metadata.stream_name;
      video.muted = !audioEnabled;
      say(compatibility ? "正在準備共用 H.264 相容影片；只讀取本機 go2rtc，會增加主機負載。" : hevcSource ? "瀏覽器支援 HEVC，直接使用原碼 MSE；不另做 H.264 轉碼。" : "正在連接同一個 go2rtc 串流；不建立第二個攝影機來源。" );
      stats.textContent = "流暢播放｜正在協商媒體格式";
      const useRtc = !useMse && !hevcSource && Boolean(window.MeowKeeperRTC && window.RTCPeerConnection);
      connectedTransport = useRtc ? "WebRTC" : "MSE";
      audioEvidence = null;
      updateAudioButton();
      const player = useRtc ? window.MeowKeeperRTC : window.MeowKeeperMSE;
      let encodingHint = "共用 H.264 相容轉碼";
      msePlayer = player.create({
        video,
        compatibility,
        onAudio: evidence => {
          if (cycle !== generation || disposed || mode !== "smooth") return;
          audioEvidence = { ...audioEvidence, ...evidence };
          if (evidence.blocked) {
            audioEnabled = false; video.muted = true;
            say("瀏覽器阻擋有聲播放；已維持靜音，請再次點擊收音並確認網站音量權限。");
          } else if (audioEnabled && evidence.available === false && useRtc) {
            msePlayer?.close(); msePlayer = null;
            startSmooth(true);
            return;
          } else if (audioEnabled && evidence.available === false) {
            say("已連接影片，但來源未提供可播放音軌；請確認攝影機收音及來源音訊格式。");
          }
          updateAudioButton();
          publishPlayback(lastPlayback);
        },
        onStatus: (state, encoding) => {
          if (cycle !== generation || mode !== "smooth") return;
          if (state === "connecting") { stats.textContent = "流暢播放｜連接同源串流"; }
          if (state === "negotiated") { stats.textContent = "流暢播放｜格式已協商，等待解碼影格"; if(encoding?.message_zh_tw){encodingHint=encoding.message_zh_tw;say(encodingHint);} }
          if (state === "streaming") { stats.textContent = "流暢播放｜已解碼影片"; }
        },
        onStats: data => {
          if (cycle !== generation || mode !== "smooth") return;
          publishPlayback({mode,transport:useRtc?"WebRTC":compatibility?"MSE · H.264 相容轉碼":hevcSource?"MSE · HEVC 原碼":"MSE · 原碼",fps:data.fps,reason:fallbackReason});
          readMetadata();
          const fps = Number.isFinite(data.fps) ? `${Number(data.fps).toFixed(1)} FPS` : "FPS 未提供";
          stats.textContent = `流暢播放｜${useRtc ? "WebRTC" : compatibility ? "H.264 相容轉碼" : hevcSource ? "HEVC 原碼" : "原碼"} ${video.videoWidth}×${video.videoHeight}｜${fps}｜${Number(data.mbps || 0).toFixed(2)} Mbps｜已收 ${(Number(data.bytes || 0) / 1000000).toFixed(2)} MB`;
          if (data.fps > 0) {
            say(compatibility ? `${encodingHint}；速度以瀏覽器實際呈現影格計算，AI 與錄影不受預覽採樣影響。` : "連續影片已解碼；畫面播放速率以瀏覽器實際呈現影格計算，AI 與錄影沿用各自管線。" );
            onFrame({ fresh: true, metadata, width: video.videoWidth, height: video.videoHeight, mode: "smooth", fps: data.fps });
          }
        },
        onError: message => {
          if (cycle !== generation || disposed || mode !== "smooth") return;
          if (useRtc) {
            msePlayer?.close(); msePlayer = null;
            say(message);
            startSmooth(true);
          } else if(!compatibility) {
            msePlayer?.close();msePlayer=null;fallbackReason=message;startSmooth(true,true);
          } else fallBack(message);
        }
      });
      msePlayer.start();
    }
    function fallBack(message) {
      if (disposed || mode !== "smooth") return;
      stopDisplay();
      mode = "energy";
      fallbackReason = message;
      publishPlayback({mode,reason:message});
      displayCount = 0; bytesWindow = 0; frameWindowAt = performance.now(); byteWindowAt = frameWindowAt;
      try { localStorage.setItem(storageKey, mode); } catch (_) { /* fallback still works */ }
      syncButtons();
      say(message);
      stats.textContent = "節能預覽｜已自動切回";
      onUnavailable(message);
      readJpeg();
    }
    async function setMode(next, { persist = true } = {}) {
      if (!Object.prototype.hasOwnProperty.call(modes, next)) return;
      stopDisplay();
      const cycle = generation;
      mode = next;
      audioEvidence = null;
      connectedTransport = null;
      updateAudioButton();
      publishPlayback({mode,reason:null});
      connectedStream = null;
      if (persist) fallbackReason = null;
      displayCount = 0; bytesWindow = 0; frameWindowAt = performance.now(); byteWindowAt = frameWindowAt;
      if (persist) { try { localStorage.setItem(storageKey, mode); } catch (_) { /* session default remains usable */ } }
      syncButtons();
      setSleepState();
      if (!visible || mode === "sleep") return;
      const status = await readMetadata(true);
      if (cycle !== generation || !visible || disposed || mode === "sleep" || next !== mode) return;
      if (mode === "smooth") startSmooth();
      else {
        stats.textContent = "節能預覽｜正在取得共享影格";
        readJpeg();
      }
    }
    function onVisibility() {
      visible = !document.hidden;
      if (!visible) {
        stopDisplay();
        image.hidden = true; video.hidden = true;
        sleepState.hidden = true; hud.hidden = true;
        return;
      }
      setSleepState();
      if (mode !== "sleep") setMode(mode, { persist: false });
    }
    function close() {
      if (disposed) return;
      disposed = true;
      hudVisibility?.close();
      stopDisplay();
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", close);
      document.removeEventListener("fullscreenchange", updateFullscreenButton);
      document.removeEventListener("keydown", onFullscreenKeydown);
      if ("onwebkitfullscreenchange" in document) document.removeEventListener("webkitfullscreenchange", updateFullscreenButton);
      fullscreenButton.remove();
      hud.remove(); sleepState.remove(); video.remove();
      overlay?.close();
    }
    function attach(options) {
      if (disposed) return;
      // Keep video/srcObject/MediaSource and its transport alive. No setMode,
      // stopDisplay, URL revocation or media negotiation on a view change.
      const nextStage = options.image.closest(".preview-stage, .overview-preview") || options.image.parentElement;
      onMetadata = options.onMetadata || (() => {});
      onFrame = options.onFrame || (() => {});
      onUnavailable = options.onUnavailable || (() => {});
      if (nextStage === stage) return;
      if (stage.classList.contains("is-mobile-fullscreen")) {
        stage.classList.remove("is-mobile-fullscreen");
        document.body.classList.remove("is-mobile-fullscreen-active");
      }
      if (document.fullscreenElement === stage) document.exitFullscreen?.().catch?.(() => {});
      else if (document.webkitFullscreenElement === stage) document.webkitExitFullscreen?.();
      overlay?.close();
      hudVisibility?.close();
      image.hidden = true;
      image.removeAttribute("src");
      image = options.image; empty = options.empty; note = options.note;
      scope = options.scope || "live";
      storageKey = `meowkeeper-preview-mode:${scope}`;
      stage = nextStage;
      for (const node of [video, hud, sleepState]) {
        if (stage.moveBefore && node.isConnected && stage.isConnected) stage.moveBefore(node, null);
        else stage.append(node);
      }
      if (objectUrl && mode === "energy") { image.src = objectUrl; image.hidden = false; }
      else image.hidden = true;
      overlay = window.MeowKeeperOverlay?.mount(stage, hud);
      hudVisibility = window.MeowKeeperHud?.mount(stage);
      if (metadata) onMetadata(metadata);
      if (empty) empty.hidden = mode === "sleep" || !image.hidden || !video.hidden;
      publishPlayback(lastPlayback);
      overlay?.update(metadata?.overlay, mode === "smooth" ? video : image);
      if (mode === "smooth" && visible && !video.hidden) video.play?.().catch?.(() => {
        audioEnabled = false; video.muted = true; updateAudioButton();
        say("瀏覽器阻擋有聲播放；影片連線仍保留，請再次點擊收音。");
      });
      if (mode === "sleep") setSleepState();
    }
    for (const button of buttons.values()) button.addEventListener("click", () => setMode(button.dataset.previewMode));
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", close, { once: true });
    syncButtons();
    setSleepState();
    if (visible) setMode(mode, { persist: false });
    return {
      refresh: async () => { await readMetadata(true); if (mode === "energy") await readJpeg(); },
      reconnect: () => setMode(mode, { persist: false }),
      close,
      attach,
      isDisplaying: () => !image.hidden || !video.hidden,
      mode: () => mode,
      isAudioEnabled: () => audioEnabled,
      setAudioEnabled: value => {
        audioEnabled = Boolean(value);
        video.muted = !audioEnabled;
        updateAudioButton();
      },
      isFullscreen: isCurrentFullscreen,
      toggleFullscreen
    };
  }
  let shared = null;
  const views = new Map();
  function activate(scope) {
    const options = views.get(scope);
    if (!options) return;
    if (shared) shared.attach(options);
    else shared = create(options);
  }
  function mount(options) {
    const view = options.image.closest("[data-monitor-view]");
    if (!view?.dataset?.monitorView) return create(options);
    const scope = view.dataset.monitorView;
    views.set(scope, options);
    if (!view.hidden) activate(scope);
    return {
      refresh: () => view.hidden ? Promise.resolve() : shared?.refresh(),
      reconnect: () => shared?.reconnect(),
      close: () => shared?.close(),
      mode: () => shared?.mode() || options.defaultMode || "energy",
      isDisplaying: () => !view.hidden && Boolean(shared?.isDisplaying()),
      isAudioEnabled: () => Boolean(shared?.isAudioEnabled()),
      setAudioEnabled: value => shared?.setAudioEnabled(value),
      isFullscreen: () => !view.hidden && Boolean(shared?.isFullscreen()),
      toggleFullscreen: () => { if (!view.hidden) return shared?.toggleFullscreen(); }
    };
  }
  document.addEventListener("meowkeeper:monitor-view", event => activate(event.detail?.scope));
  window.addEventListener("pageshow", event => {
    if (!event.persisted) return;
    shared = null;
    activate(window.MeowKeeperMonitorNavigation?.activeScope());
  });
  window.MeowKeeperPreview = { mount };
})();
