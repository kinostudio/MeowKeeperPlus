/* Presentation-only AI marks; never mutate or encode the video. */
(() => {
  "use strict";
  const HOLD_MS = 850;
  function mount(stage, hud) {
    const layer = document.createElement("div");
    layer.className = "preview-ai-layer";
    layer.setAttribute("aria-hidden", "true");
    stage.append(layer);
    const help = document.createElement("p");
    help.className = "field-help preview-ai-help";
    help.textContent = "AI 標記是最近 2 秒內的採樣，並非逐幀同步，與連續影片可能稍有時間差；偵測框每次更新後會穩定保留約 0.85 秒避免閃爍，過期後自動隱藏。身分與動作仍是實驗性候選，可各自開關，不會寫進原始影片。";
    stage.after(help);
    const choices = {};
    const overlayTooltips = {
      boxes: "偵測框：在預覽畫面標出 YOLO 即時偵測到的貓咪位置與信心分數（0～1）。每次偵測更新後會平滑保留約 0.85 秒避免閃爍；僅疊加於網頁預覽，不會寫入原始錄影或影響 Telegram 通知。",
      identity: "身分候選（實驗性）：依據已建立的「寵物檔案」參考照片特徵（DINOv2），比對目前偵測框最接近哪一隻貓咪。分數未達門檻時會顯示「未知」；僅供畫面輔助預覽，不作為正式身分結論。",
      behavior: "動作候選（實驗性）：依據貓咪移動軌跡、停留時間與畫面區域，初步推測目前動作（如行走、休息、進食等）。屬於即時候選提示，正式紀錄仍以事件影片與人工回報為準。"
    };
    for (const [key, label] of [["boxes", "偵測框"], ["identity", "身分候選"], ["behavior", "動作候選"]]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "preview-mode-button preview-overlay-toggle";
      button.textContent = label;
      const tip = overlayTooltips[key] || "只開關此頁疊圖，不改影片、AI 分析或 Telegram；候選為實驗性結果。";
      button.title = tip;
      button.setAttribute("aria-label", `${label}：${tip}`);
      button.setAttribute("data-tooltip", tip);
      let enabled = key === "boxes";
      try { const saved = localStorage.getItem(`meowkeeper-overlay:${key}`); if (saved !== null) enabled = saved === "true"; } catch (_) { /* optional */ }
      choices[key] = { button, enabled };
      button.setAttribute("aria-pressed", String(enabled));
      button.addEventListener("click", () => {
        choices[key].enabled = !choices[key].enabled;
        button.setAttribute("aria-pressed", String(choices[key].enabled));
        try { localStorage.setItem(`meowkeeper-overlay:${key}`, String(choices[key].enabled)); } catch (_) { /* optional */ }
        draw();
      });
      hud.append(button);
    }
    let overlay = null, lastNonEmptyOverlay = null, lastSeenPerf = 0, media = null, timer = 0;
    function activeOverlay() {
      const nowMs = Date.now();
      if (overlay && Array.isArray(overlay.detections) && overlay.detections.length > 0) {
        const age = (nowMs - Date.parse(overlay.captured_at)) / 1000;
        if (Number.isFinite(age) && age >= -1 && age <= 2.2) return overlay;
      }
      if (lastNonEmptyOverlay && Array.isArray(lastNonEmptyOverlay.detections) && lastNonEmptyOverlay.detections.length > 0) {
        const heldMs = nowMs - lastSeenPerf;
        const captureAge = (nowMs - Date.parse(lastNonEmptyOverlay.captured_at)) / 1000;
        if (heldMs >= 0 && heldMs <= HOLD_MS && Number.isFinite(captureAge) && captureAge >= -1 && captureAge <= 2.8) {
          return lastNonEmptyOverlay;
        }
      }
      return overlay;
    }
    function draw() {
      layer.replaceChildren();
      if (!media || media.hidden) return;
      const current = activeOverlay();
      if (!current) return;
      const nowMs = Date.now();
      const age = (nowMs - Date.parse(current.captured_at)) / 1000;
      const heldMs = current === lastNonEmptyOverlay ? (nowMs - lastSeenPerf) : 0;
      if (!Number.isFinite(age) || age < -1 || (age > 2.2 && heldMs > HOLD_MS)) {
        clearInterval(timer); timer = 0;
        return;
      }
      const width = media.videoWidth || media.naturalWidth, height = media.videoHeight || media.naturalHeight;
      if (!(width > 0 && height > 0)) return;
      const bounds = media.getBoundingClientRect(), parent = stage.getBoundingClientRect();
      const scale = Math.min(bounds.width / width, bounds.height / height);
      const dx = bounds.left - parent.left + (bounds.width - width * scale) / 2;
      const dy = bounds.top - parent.top + (bounds.height - height * scale) / 2;
      for (const [index, item] of (current.detections || []).slice(0, 20).entries()) {
        const b = item.bbox;
        if (!Array.isArray(b) || b.length !== 4 || !b.every(v => Number.isFinite(v) && v >= 0 && v <= 1) || b[2] <= b[0] || b[3] <= b[1]) continue;
        const box = document.createElement("div");
        box.className = "preview-ai-box";
        box.style.borderWidth = choices.boxes.enabled ? "2px" : "0";
        Object.assign(box.style, { left: `${dx + b[0] * width * scale}px`, top: `${dy + b[1] * height * scale}px`, width: `${(b[2]-b[0])*width*scale}px`, height: `${(b[3]-b[1])*height*scale}px` });
        const labels = [];
        if (choices.boxes.enabled) labels.push(`貓 ${index + 1} · ${Number(item.confidence || 0).toFixed(2)}（AI 採樣）`);
        if (choices.identity.enabled) labels.push(`身分候選：${item.identity || "未知"}（實驗性）`);
        if (choices.behavior.enabled) labels.push(`動作候選：${item.behavior || "尚未判斷"}（實驗性）`);
        if (!labels.length) continue;
        const label = document.createElement("span");
        label.textContent = labels.join("\n");
        if (dy + b[1] * height * scale >= labels.length * 18 + 10) {
          label.style.bottom = "100%";
        } else {
          label.style.top = "100%";
        }
        label.style.maxWidth = `${Math.max(80, Math.min(300, parent.width - (dx + b[0] * width * scale) - 8))}px`;
        box.append(label); layer.append(box);
      }
    }
    function clear() {
      overlay = null;
      lastNonEmptyOverlay = null;
      lastSeenPerf = 0;
      clearInterval(timer); timer = 0;
      layer.replaceChildren();
    }
    return { update: (value, element) => {
      if (!value || !element || element.hidden) { clear(); return; }
      overlay = value; media = element;
      if (Array.isArray(value.detections) && value.detections.length > 0) {
        const isNewCapture = !lastNonEmptyOverlay || lastNonEmptyOverlay.captured_at !== value.captured_at;
        lastNonEmptyOverlay = value;
        if (isNewCapture) lastSeenPerf = Date.now();
      }
      if (!timer) timer = setInterval(draw, 150);
      draw();
    }, clear, close: () => { clear(); layer.remove(); help.remove(); } };
  }
  window.MeowKeeperOverlay = { mount };
})();
