(function () {
  "use strict";
  const form = document.getElementById("pet-enrollment-form");
  const list = document.getElementById("pets-list");
  const status = document.getElementById("pets-status");
  const message = document.getElementById("pet-enrollment-message");
  if (!form || !list || !status || !message) return;

  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim())
      .find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };

  const labels = {
    queued: "準備中",
    running: "正在準備",
    ready: "可供 AI 比對",
    unavailable: "尚未準備好",
    not_started: "尚未準備",
  };

  function render(items) {
    if (!items.length) {
      list.innerHTML = '<p class="muted">尚無登錄資料。請先上傳一隻貓的參考照片。</p>';
      return;
    }
    list.innerHTML = items.map((pet) => {
      const state = labels[pet.embedding_status] || pet.embedding_status || "待確認";
      const detail = pet.embedding_message_zh_tw || "";
      const pendingEmbeddings = Number(pet.reference_image_count || 0) > Number(pet.embedding_count || 0);
      const retryButton = pendingEmbeddings && !["queued", "running"].includes(pet.embedding_status)
        ? `<button class="button secondary pet-retry" data-pet-id="${encodeURIComponent(pet.pet_id)}" type="button">重試準備 AI 參考資料</button>`
        : "";
      const editButton = `<button class="button secondary pet-edit" data-pet-id="${encodeURIComponent(pet.pet_id)}" type="button">編輯資料／照片</button>`;
      const descriptions = [pet.coat_description, pet.pattern_description]
        .filter(Boolean).join("・");
      const photos = (pet.reference_images || []).map((photo) =>
        `<figure class="pet-photo"><img src="${escapeHtml(photo.url)}" alt="${escapeHtml(pet.display_name)}的登錄參考照片" loading="lazy" width="128" height="128">`
        + `<figcaption><button type="button" class="button secondary pet-photo-crop" data-pet="${encodeURIComponent(pet.pet_id)}" data-image="${encodeURIComponent(photo.image_id)}">裁切參考照片（選用）</button> <button type="button" class="button secondary pet-photo-replace" data-pet="${encodeURIComponent(pet.pet_id)}" data-image="${encodeURIComponent(photo.image_id)}">更換</button> `
        + `<button type="button" class="button secondary pet-photo-delete" data-pet="${encodeURIComponent(pet.pet_id)}" data-image="${encodeURIComponent(photo.image_id)}">刪除</button></figcaption></figure>`).join("");
      return `<article class="event-row pet-row"><div><strong>${escapeHtml(pet.display_name)}</strong>`
        + `<div class="muted">識別碼：${escapeHtml(pet.pet_id)}${descriptions ? `｜${escapeHtml(descriptions)}` : ""}</div>`
        + `<div class="muted">參考照片 ${pet.reference_image_count} 張｜AI 參考資料 ${pet.embedding_count} 份｜回饋參考 ${Number(pet.feedback_feature_count||0)} 份｜${escapeHtml(state)}</div>`
        + `<details class="field-help"><summary>進階辨識資訊</summary><p>模型：${escapeHtml(pet.identity_model_version||'尚未建立')}｜資料版本：${escapeHtml((pet.gallery_version||'未確認').slice(0,12))}</p></details>`
        + (detail ? `<div class="field-help">${escapeHtml(detail)}</div>` : "")
        + `<div class="pet-photo-gallery" style="display:flex;flex-wrap:wrap;gap:12px">${photos}</div>`
        + `<p class="field-help">AI／Telegram 回饋照片另外保存在事件紀錄，不會自動混入這組參考照。更換或刪除照片後，系統會重新準備 AI 參考資料。</p>`
        + `<a href="/events">查看事件與 AI 回饋證據</a>`
        + `</div><div class="actions">${editButton}${retryButton}</div></article>`;
    }).join("");
    list.querySelectorAll(".pet-retry").forEach((button) => button.addEventListener("click", () => retryEmbedding(button.dataset.petId)));
    list.querySelectorAll(".pet-photo-crop").forEach(button=>button.addEventListener("click",()=>document.dispatchEvent(new CustomEvent("meowkeeper:crop-pet",{detail:{pet:button.dataset.pet,image:button.dataset.image,onSaved:load}}))));
    list.querySelectorAll(".pet-edit").forEach((button) => button.addEventListener("click", () => beginEdit(button.dataset.petId, items)));
    list.querySelectorAll(".pet-photo-delete").forEach((button) => button.addEventListener("click", () => {
      if (window.confirm("確定刪除這張參考照片？AI 比對資料會更新，歷史回饋不會刪除。")) changePhoto(button, "DELETE");
    }));
    list.querySelectorAll(".pet-photo-replace").forEach((button) => button.addEventListener("click", () => {
      const input = document.createElement("input");
      input.type = "file"; input.accept = "image/jpeg";
      input.addEventListener("change", () => { if(input.files[0])changePhoto(button, "PUT", input.files[0]); });
      input.click();
    }));
  }

  async function changePhoto(button, method, file) {
    button.disabled = true;
    try {
      const options = {method, headers: {"X-MeowKeeper-CSRF": csrfToken()}};
      if(file){options.body=new FormData();options.body.append("images",file);}
      const response=await fetch(`/api/v1/pets/${button.dataset.pet}/images/${button.dataset.image}`,options);
      const data=await response.json();
      if(!response.ok)throw new Error(data.detail || "照片操作未完成。");
      message.textContent=data.message_zh_tw;
      await load();
    } catch(error){message.textContent=error.message;button.disabled=false;}
  }

  function beginEdit(petId, items) {
    const pet = items.find((item) => item.pet_id === petId);
    if (!pet || !editForm) return;
    document.getElementById("pet-edit-id").value = pet.pet_id;
    document.getElementById("pet-edit-display-name").value = pet.display_name || "";
    document.getElementById("pet-edit-coat").value = pet.coat_description || "";
    document.getElementById("pet-edit-pattern").value = pet.pattern_description || "";
    document.getElementById("pet-edit-enabled").checked = pet.enabled !== false;
    document.getElementById("pet-edit-images").value = "";
    editForm.hidden = false;
    editForm.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>'"]/g, (character) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
    }[character]));
  }

  const trackGrid = document.getElementById("pet-track-candidates-grid");
  const trackStatus = document.getElementById("pet-track-candidates-status");
  const trackMessage = document.getElementById("pet-track-candidates-message");
  const trackReload = document.getElementById("pet-track-candidates-reload");
  const trackSave = document.getElementById("pet-track-candidates-save");
  const trackCancel = document.getElementById("pet-track-candidates-cancel");
  const trackBatchDeleteAll = document.getElementById("pet-track-batch-delete-all");
  const trackDynamicFilters = document.getElementById("pet-track-dynamic-filters");
  let trackFilter = "all";
  let trackCandidatesData = { items: [], pets: [] };
  const stagedActions = new Map(); // eventId -> { action: 'assign'|'delete'|'clear', petId, petName, trackId }

  function formatTrackTime(iso) {
    if (!iso) return "時間未知";
    const parsed = new Date(iso);
    return Number.isNaN(parsed.getTime()) ? String(iso) : parsed.toLocaleString();
  }

  function updateSaveButton() {
    const count = stagedActions.size;
    if (trackSave) {
      trackSave.disabled = count === 0;
      trackSave.textContent = count > 0 ? `更新變更 (${count} 筆待套用)` : "更新變更";
    }
    if (trackCancel) {
      trackCancel.style.display = count > 0 ? "" : "none";
    }
  }

  function renderDynamicFilters() {
    if (!trackDynamicFilters) return;
    const pets = Array.isArray(trackCandidatesData.pets) ? trackCandidatesData.pets : [];
    trackDynamicFilters.innerHTML = pets.map((pet) => {
      const active = trackFilter === `pet:${pet.pet_id}`;
      return `<button type="button" class="button secondary ${active ? "is-active" : ""}" data-track-filter="pet:${escapeHtml(pet.pet_id)}">已入庫：${escapeHtml(pet.display_name)}</button>`;
    }).join("");
    trackDynamicFilters.querySelectorAll("[data-track-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        setTrackFilter(button.dataset.trackFilter);
      });
    });
  }

  function renderTrackCandidates() {
    if (!trackGrid) return;
    const allItems = Array.isArray(trackCandidatesData.items) ? trackCandidatesData.items : [];
    const pets = Array.isArray(trackCandidatesData.pets) ? trackCandidatesData.pets : [];
    const isEnrolled = (item) => Boolean(item.has_feature || item.feature_ready || item.learning_status === "eligible");
    const isNotCat = (item) => item.assigned_pet_id === "not_cat" || item.annotated_identity === "not_cat";
    const enrolledCount = allItems.filter(isEnrolled).length;
    const unlabeledCount = allItems.filter((item) => !item.assigned_pet_id && !isNotCat(item)).length;

    if (trackStatus) {
      trackStatus.textContent = `清晰截圖 ${allItems.length} 張｜已入庫 ${enrolledCount} 張｜待標註 ${unlabeledCount} 張`;
      trackStatus.className = `status-pill ${enrolledCount > 0 ? "status-ok" : "status-info"}`;
    }

    const filtered = allItems.filter((item) => {
      if (trackFilter.startsWith("pet:")) {
        const pId = trackFilter.split(":")[1];
        const staged = stagedActions.get(item.event_id);
        if (staged) {
          if (staged.action === "delete") return true;
          return staged.petId === pId;
        }
        return item.assigned_pet_id === pId;
      }
      if (trackFilter === "unlabeled") return !item.assigned_pet_id && !isNotCat(item);
      if (trackFilter === "enrolled") return Boolean(isEnrolled(item) || (item.assigned_pet_id && !isNotCat(item)));
      return true;
    });

    if (trackBatchDeleteAll) {
      trackBatchDeleteAll.style.display = filtered.length > 0 ? "" : "none";
    }

    if (!filtered.length) {
      trackGrid.innerHTML = `<p class="muted">${allItems.length ? "此篩選條件下尚無截圖，可切換上方篩選查看其他截圖。" : "目前尚無符合清晰度條件（單貓、≥96px、非模糊）的近期攝影機軌跡截圖。"}</p>`;
      return;
    }
    if (!pets.length) {
      trackGrid.innerHTML = '<p class="muted">請先在上方登錄至少一隻貓咪，即可從近期攝影機截圖一鍵指定身分並加入特徵庫。</p>';
      return;
    }

    trackGrid.innerHTML = filtered.map((item) => {
      const eid = item.event_id;
      const staged = stagedActions.get(eid);
      const assignedPet = pets.find((pet) => pet.pet_id === (staged?.petId || item.assigned_pet_id));
      const enrolled = isEnrolled(item);
      const notCat = staged ? staged.petId === "not_cat" : isNotCat(item);

      let cardClass = "track-candidate-card";
      let badgeHtml = "";

      if (staged) {
        if (staged.action === "delete") {
          cardClass += " is-staged-delete";
          badgeHtml = '<span class="status-pill status-warn">🗑️ [待儲存] 標記刪除</span>';
        } else if (staged.action === "clear") {
          cardClass += " is-staged-clear";
          badgeHtml = '<span class="status-pill status-unknown">⏳ [待儲存] 清除標註</span>';
        } else if (staged.petId === "not_cat") {
          cardClass += " is-staged-assign";
          badgeHtml = '<span class="status-pill status-warn">🚫 [待儲存] 標記非貓</span>';
        } else if (assignedPet) {
          cardClass += " is-staged-assign";
          badgeHtml = `<span class="status-pill status-info">📌 [待儲存] 設為：${escapeHtml(assignedPet.display_name)}</span>`;
        }
      } else {
        if (enrolled) cardClass += " is-enrolled";
        badgeHtml = notCat
          ? '<span class="status-pill status-warn">🚫 已標記非貓／略過</span>'
          : enrolled && assignedPet
            ? `<span class="status-pill status-ok">✅ 已入庫特徵：${escapeHtml(assignedPet.display_name)}</span>`
            : assignedPet
              ? `<span class="status-pill status-info">📌 已標註：${escapeHtml(assignedPet.display_name)}</span>`
              : '<span class="status-pill status-unknown">⏳ 待標註</span>';
      }

      const topAi = Array.isArray(item.ai_candidates) && item.ai_candidates.length ? item.ai_candidates[0] : null;
      const candName = topAi ? topAi.name : item.candidate_pet_name;
      const candScore = topAi ? topAi.score : item.candidate_confidence;
      const candidateHint = candName
        ? `AI 候選：${escapeHtml(candName)}${candScore != null ? ` (${Math.round(Number(candScore) * 100)}%)` : ""}`
        : "AI 候選：尚未確認";
      const trackIdVal = item.track_id || "track_01";
      const currentAssignedId = staged?.petId || item.assigned_pet_id;

      const petButtons = pets.map((pet) => {
        const isCurrent = currentAssignedId === pet.pet_id;
        return `<button type="button" class="button ${isCurrent ? "" : "secondary"} track-assign-btn" data-event-id="${encodeURIComponent(eid)}" data-track-id="${encodeURIComponent(trackIdVal)}" data-pet-id="${encodeURIComponent(pet.pet_id)}" data-pet-name="${escapeHtml(pet.display_name)}">${isCurrent ? `✓ 已設為 ${escapeHtml(pet.display_name)}` : `➕ 設為 ${escapeHtml(pet.display_name)}`}</button>`;
      }).join("");

      const isDeleteStaged = staged?.action === "delete";
      const extraButtons = `<button type="button" class="button secondary track-assign-btn" data-event-id="${encodeURIComponent(eid)}" data-track-id="${encodeURIComponent(trackIdVal)}" data-pet-id="not_cat" data-pet-name="非貓" title="標記為非貓或略過此截圖">🚫 非貓</button>`
        + `<button type="button" class="button secondary track-delete-btn" data-event-id="${encodeURIComponent(eid)}" title="刪除此截圖">${isDeleteStaged ? "↩️ 復原刪除" : "🗑️ 刪除截圖"}</button>`
        + ((currentAssignedId || notCat)
          ? `<button type="button" class="button secondary track-assign-btn" data-event-id="${encodeURIComponent(eid)}" data-track-id="${encodeURIComponent(trackIdVal)}" data-pet-id="clear" data-pet-name="清除" title="清除標註並撤回此筆特徵">清除</button>`
          : "");
      const widthVal = Number(item.width || item.crop_width || 0);
      const heightVal = Number(item.height || item.crop_height || 0);
      const timeVal = item.captured_at || item.trigger_at;

      return `<article class="${cardClass}" data-event-id="${encodeURIComponent(eid)}">`
        + `<div class="track-candidate-media">`
        + `<img src="${escapeHtml(item.crop_url)}" alt="事件 ${escapeHtml(eid)} 軌跡截圖" loading="lazy">`
        + `<div class="track-candidate-badge">${badgeHtml}</div>`
        + `</div>`
        + `<div class="track-candidate-body">`
        + `<div class="track-candidate-meta"><strong>${escapeHtml(formatTrackTime(timeVal))}</strong><span>${widthVal}×${heightVal}px</span></div>`
        + `<div class="muted track-candidate-sub">軌跡 ${escapeHtml(trackIdVal)} · <a href="/identity">查看逐軌跡明細</a></div>`
        + `<div class="field-help">${candidateHint}</div>`
        + `<div class="track-candidate-actions">${petButtons}${extraButtons}</div>`
        + `</div>`
        + `</article>`;
    }).join("");

    trackGrid.querySelectorAll(".track-assign-btn").forEach((button) => {
      button.addEventListener("click", () => stageAssign(button));
    });
    trackGrid.querySelectorAll(".track-delete-btn").forEach((button) => {
      button.addEventListener("click", () => stageDelete(button));
    });
    updateSaveButton();
  }

  function stageAssign(button) {
    const eventId = decodeURIComponent(button.dataset.eventId || "");
    const trackId = decodeURIComponent(button.dataset.trackId || "");
    const petId = decodeURIComponent(button.dataset.petId || "");
    const petName = button.dataset.petName || "";
    if (!eventId || !petId) return;

    if (petId === "clear") {
      stagedActions.set(eventId, { action: "clear", petId: "clear", trackId });
    } else {
      stagedActions.set(eventId, { action: "assign", petId, petName, trackId });
    }
    renderTrackCandidates();
    if (trackMessage) trackMessage.textContent = `已暫存變更：設為「${petName}」。請點擊上方「更新變更」一次性儲存。`;
  }

  function stageDelete(button) {
    const eventId = decodeURIComponent(button.dataset.eventId || "");
    if (!eventId) return;
    if (stagedActions.get(eventId)?.action === "delete") {
      stagedActions.delete(eventId);
      if (trackMessage) trackMessage.textContent = "已復原此截圖刪除標記。";
    } else {
      stagedActions.set(eventId, { action: "delete" });
      if (trackMessage) trackMessage.textContent = "已標記刪除此截圖。請點擊上方「更新變更」一次性儲存。";
    }
    renderTrackCandidates();
  }

  async function loadTrackCandidates() {
    if (!trackGrid) return;
    try {
      const url = `/api/v1/pets/track-candidates?limit=120&filter_mode=${encodeURIComponent(trackFilter)}`;
      const response = await fetch(url, {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "近期軌跡截圖讀取失敗。");
      trackCandidatesData = {
        items: Array.isArray(data.items) ? data.items : [],
        pets: Array.isArray(data.pets) ? data.pets : [],
      };
      renderDynamicFilters();
      renderTrackCandidates();
    } catch (error) {
      if (trackStatus) trackStatus.textContent = "讀取失敗";
      trackGrid.innerHTML = `<p class="muted">${escapeHtml(error.message || "近期軌跡截圖讀取失敗。")}</p>`;
    }
  }

  function setTrackFilter(mode) {
    trackFilter = mode || "all";
    document.querySelectorAll("[data-track-filter]").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.trackFilter === trackFilter);
    });
    loadTrackCandidates();
  }

  async function saveBatch() {
    if (!stagedActions.size) return;
    if (trackSave) trackSave.disabled = true;
    if (trackCancel) trackCancel.disabled = true;
    if (trackMessage) trackMessage.textContent = `正在批次套用 ${stagedActions.size} 筆軌跡變更並更新 DINOv2 特徵庫…`;

    const operations = Array.from(stagedActions.entries()).map(([eventId, op]) => ({
      event_id: eventId,
      ...op,
    }));

    try {
      const response = await fetch("/api/v1/pets/track-candidates/batch", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-MeowKeeper-CSRF": csrfToken(),
          Accept: "application/json",
        },
        body: JSON.stringify({ operations }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "批次更新失敗。");
      stagedActions.clear();
      if (trackMessage) trackMessage.textContent = data.message_zh_tw || "已成功批次更新特徵庫！";
      await Promise.all([loadTrackCandidates(), load()]);
    } catch (error) {
      if (trackMessage) trackMessage.textContent = error.message;
    } finally {
      if (trackSave) trackSave.disabled = stagedActions.size === 0;
      if (trackCancel) trackCancel.disabled = false;
    }
  }

  document.querySelectorAll("[data-track-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      setTrackFilter(button.dataset.trackFilter);
    });
  });

  if (trackReload) {
    trackReload.addEventListener("click", () => {
      if (trackMessage) trackMessage.textContent = "正在重新載入近期攝影機軌跡截圖…";
      loadTrackCandidates().then(() => {
        if (trackMessage) trackMessage.textContent = "已更新近期軌跡截圖清單。";
      });
    });
  }

  if (trackSave) trackSave.addEventListener("click", saveBatch);
  if (trackCancel) {
    trackCancel.addEventListener("click", () => {
      stagedActions.clear();
      renderTrackCandidates();
      if (trackMessage) trackMessage.textContent = "已放棄所有尚未儲存的暫存變更。";
    });
  }
  if (trackBatchDeleteAll) {
    trackBatchDeleteAll.addEventListener("click", () => {
      const allItems = Array.isArray(trackCandidatesData.items) ? trackCandidatesData.items : [];
      if (!allItems.length) return;
      if (!confirm(`確定要將目前篩選檢視下的 ${allItems.length} 張截圖全部標記為刪除嗎？\n（需再點擊「更新變更」才會真正寫入資料庫與撤回特徵庫）`)) return;
      allItems.forEach((item) => {
        stagedActions.set(item.event_id, { action: "delete" });
      });
      renderTrackCandidates();
      if (trackMessage) trackMessage.textContent = `已將目前檢視下的 ${allItems.length} 張截圖標記為刪除。請點擊「更新變更」執行。`;
    });
  }

  async function load() {
    const response = await fetch("/api/v1/pets", { cache: "no-store", headers: { Accept: "application/json" } });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "寵物登錄資料讀取失敗。");
    render(Array.isArray(data.items) ? data.items : []);
    status.textContent = `${Array.isArray(data.items) ? data.items.length : 0} 隻已登錄`;
    if ((data.items || []).some((pet) => pet.embedding_status === "queued" || pet.embedding_status === "running")) {
      window.setTimeout(() => load().catch(() => {}), 2500);
    }
  }

  async function retryEmbedding(petId) {
    message.textContent = "正在重新準備 AI 參考資料…";
    const response = await fetch(`/api/v1/pets/${petId}/embeddings`, {
      method: "POST", headers: { "X-MeowKeeper-CSRF": csrfToken(), Accept: "application/json" },
    });
    const data = await response.json();
    message.textContent = response.ok ? (data.pet.embedding_message_zh_tw || "已排入準備工作。") : (data.detail || "重試失敗。");
    await load().catch(() => {});
  }

  const editForm = document.getElementById("pet-edit-form");
  const editMessage = document.getElementById("pet-edit-message");
  if (editForm && editMessage) {
    document.getElementById("pet-edit-cancel").addEventListener("click", () => {
      editForm.hidden = true;
      editMessage.textContent = "";
    });
    editForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const petId = document.getElementById("pet-edit-id").value;
      if (!petId) return;
      editMessage.textContent = "正在保存寵物資料…";
      const payload = {
        display_name: document.getElementById("pet-edit-display-name").value,
        coat_description: document.getElementById("pet-edit-coat").value,
        pattern_description: document.getElementById("pet-edit-pattern").value,
        enabled: document.getElementById("pet-edit-enabled").checked,
      };
      const response = await fetch(`/api/v1/pets/${encodeURIComponent(petId)}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-MeowKeeper-CSRF": csrfToken(),
          Accept: "application/json",
        },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (!response.ok) { editMessage.textContent = data.detail || "寵物資料保存失敗。"; return; }
      const files = document.getElementById("pet-edit-images").files;
      if (files && files.length) {
        editMessage.textContent = "資料已保存，正在追加照片…";
        const formData = new FormData();
        Array.from(files).forEach((file) => formData.append("images", file));
        const imageResponse = await fetch(`/api/v1/pets/${encodeURIComponent(petId)}/images`, {
          method: "POST",
          headers: { "X-MeowKeeper-CSRF": csrfToken(), Accept: "application/json" },
          body: formData,
        });
        const imageData = await imageResponse.json();
        if (!imageResponse.ok) { editMessage.textContent = imageData.detail || "新增照片失敗。"; await load().catch(() => {}); return; }
        editMessage.textContent = imageData.message_zh_tw || "新增照片已保存。";
      } else {
        editMessage.textContent = data.message_zh_tw || "寵物資料已更新。";
      }
      editForm.hidden = true;
      await load().catch(() => {});
    });
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = document.getElementById("pet-images").files;
    if (!files || !files.length) { message.textContent = "請至少選擇一張 JPG 參考照片。"; return; }
    message.textContent = "正在保存照片並準備 AI 參考資料…";
    const response = await fetch("/api/v1/pets", {
      method: "POST",
      headers: { "X-MeowKeeper-CSRF": csrfToken(), Accept: "application/json" },
      body: new FormData(form),
    });
    const data = await response.json();
    if (!response.ok) { message.textContent = data.detail || "寵物登錄失敗。"; return; }
    const rejected = data.rejected && data.rejected.length ? `；略過 ${data.rejected.length} 張不合格照片` : "";
    message.textContent = `已登錄 ${data.pet.display_name}，接受 ${data.accepted_reference_images} 張照片${rejected}。${data.message_zh_tw || ""}`;
    form.reset();
    await Promise.all([load(), loadTrackCandidates()]).catch(() => {});
  });

  document.getElementById("pets-reload").addEventListener("click", () => {
    load().catch((error) => { message.textContent = error.message; });
    loadTrackCandidates().catch(() => {});
  });
  load().catch((error) => { status.textContent = "讀取失敗"; message.textContent = error.message; });
  loadTrackCandidates().catch(() => {});
})();
