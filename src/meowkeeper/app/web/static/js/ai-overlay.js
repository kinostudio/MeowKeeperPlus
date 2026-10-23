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
    const choices = {
      boxes: { enabled: true },
      identity: { enabled: true },
      behavior: { enabled: true }
    };
    for (const key of ["boxes", "identity", "behavior"]) {
      try {
        const saved = localStorage.getItem(`meowkeeper-overlay:${key}`);
        if (saved !== null) choices[key].enabled = saved === "true";
      } catch (_) {}
    }

    // Single unified AI Icon button with dropdown popover
    const aiDropdown = document.createElement("div");
    aiDropdown.className = "preview-ai-dropdown";

    const aiButton = document.createElement("button");
    aiButton.type = "button";
    aiButton.className = "preview-mode-button preview-mode-select preview-ai-toggle";
    aiButton.setAttribute("aria-label", "AI 偵測框與身分動作顯示設定");
    aiButton.setAttribute("aria-haspopup", "true");
    aiButton.innerHTML = `<span class="preview-mode-icon" aria-hidden="true"><svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8V5a1 1 0 0 1 1-1h3M4 16v3a1 1 0 0 0 1 1h3M16 4h3a1 1 0 0 1 1 1v3M16 20h3a1 1 0 0 0 1-1v-3"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="5" x2="12" y2="7"/><line x1="12" y1="17" x2="12" y2="19"/><line x1="5" y1="12" x2="7" y2="12"/><line x1="17" y1="12" x2="19" y2="12"/></svg></span>`;

    const aiMenu = document.createElement("div");
    aiMenu.className = "preview-ai-menu";

    const optionsConfig = [
      { key: "boxes", label: "偵測框顯示" },
      { key: "identity", label: "身分偵測顯示 (本地AI·DINOv2)" },
      { key: "behavior", label: "動作顯示 (本地AI·DINOv2)" }
    ];

    optionsConfig.forEach(({ key, label }) => {
      const optLabel = document.createElement("label");
      optLabel.className = "preview-ai-option";
      const chk = document.createElement("input");
      chk.type = "checkbox";
      chk.checked = choices[key].enabled;
      chk.addEventListener("change", () => {
        choices[key].enabled = chk.checked;
        try { localStorage.setItem(`meowkeeper-overlay:${key}`, String(chk.checked)); } catch (_) {}
        updateAiState();
        draw();
      });
      const txt = document.createElement("span");
      txt.textContent = label;
      optLabel.append(chk, txt);
      aiMenu.appendChild(optLabel);
    });

    function updateAiState() {
      const anyActive = choices.boxes.enabled || choices.identity.enabled || choices.behavior.enabled;
      aiButton.setAttribute("aria-pressed", String(anyActive));
    }
    updateAiState();

    aiButton.addEventListener("click", (e) => {
      e.stopPropagation();
      aiDropdown.classList.toggle("is-open");
    });
    document.addEventListener("click", (e) => {
      if (!aiDropdown.contains(e.target)) {
        aiDropdown.classList.remove("is-open");
      }
    });

    aiDropdown.append(aiButton, aiMenu);
    hud.append(aiDropdown);
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
        if (choices.boxes.enabled) labels.push(`貓 ${index + 1} · ${Number(item.confidence || 0).toFixed(2)}`);
        if (choices.identity.enabled) labels.push(`身分偵測：${item.identity || "未知"} (DINOv2)`);
        if (choices.behavior.enabled) labels.push(`動作：${item.behavior || "尚未判斷"} (DINOv2)`);
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
