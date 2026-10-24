(() => {
  "use strict";
  const byId = id => document.getElementById(id), dialog = byId("event-review-dialog");
  if (!dialog) return;
  let view = null, item = null, step = 1, selected = [], actions = {}, notCat = false, busy = false, requestId = null;
  let videoReady = false, videoFailed = false, submitted = false, videoPlaybackMode = "native", videoFallbackTimer = 0;
  let segmentPlaybackEnd = null, candidateBusy = false;
  let editingRevision = null, withdrawing = false;
  let reanalysisId = null;
  function csrf() {
    const cookie=document.cookie.split(";").map(s=>s.trim()).find(s=>s.startsWith("meowkeeper_csrf="));
    return cookie ? decodeURIComponent(cookie.slice("meowkeeper_csrf=".length)) : "";
  }
  function renderCandidateActions() {
    renderValidity();
    let active=document.getElementById("event-review-active-assessment");if(!active){active=document.createElement("section");active.id="event-review-active-assessment";byId("event-review-candidate-actions").before(active);}active.replaceChildren();
    element("h4","目前有效的人工回報",active);const assessment=view?.assessment?.human_review;
    if(!assessment?.active_segments?.length && !assessment?.latest_video_review && !assessment?.telegram_current?.length)element("p","尚無有效回報；撤回片段只保留在歷史，不會繼續當成目前結果。",active);
    (assessment?.telegram_current || []).forEach(row=>element("p",`Telegram · ${row.name || (row.identity==="not_cat" ? "不是貓咪" : "其他／未確認貓咪")} · ${row.tags.map(tag=>view.tags[tag] || "尚未確認動作").join("、") || "未標記動作"}（整段影片，不是精準片段時間）`,active));
    (assessment?.active_segments || []).forEach(row=>element("p",`片段 ${row.segment.start_seconds}～${row.segment.end_seconds} 秒 · 修訂 ${row.revision} · ${row.pets.map(pet=>view.pets.find(item=>item.pet_id===pet.pet_id)?.name || "其他／未確認貓咪").join("、") || "非貓"}`,active));
    if(assessment?.latest_video_review)element("p",`整段影片最近有效回報：修訂 ${assessment.latest_video_review.revision}（${assessment.latest_video_review.label==="not_cat" ? "不是貓咪" : "貓咪回報"}）`,active);
    const root=byId("event-review-candidate-actions"); root.replaceChildren();
    (view?.semantic_history || []).filter(row=>row.result?.status === "completed").forEach(row=> {
      (row.result.tracks || []).forEach((track,index)=> {
        (track.tags || []).forEach(code=> {
          const card=element("div","",root);
          const prior=(view.candidate_reviews || []).filter(review=>review.analysis_revision===row.revision && review.track_id===track.track_id && review.tag_code===code).at(-1);
          element("p",`候選第 ${row.revision} 版 · 軌跡 #${index+1} · ${view.tags[code] || "未知標籤"} · ${prior ? prior.decision === "accepted" ? "人工已接受" : "人工已駁回" : "尚未覆核"}`,card);
          for (const [decision,label] of [["accepted","接受這個候選"],["rejected","駁回這個候選"]]) {
            const button=element("button",label,card); button.type="button"; button.className="button secondary";
            button.disabled=!videoReady || candidateBusy;
            button.addEventListener("click",async()=> {
              if (!videoReady || candidateBusy) return;
              candidateBusy=true; renderCandidateActions();
              try {
                const latest=(view.candidate_reviews || []).at(-1)?.revision || 0;
                const response=await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/semantic/review`,{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrf()},body:JSON.stringify({request_id:window.MeowKeeperRequestId(),expected_revision:latest,analysis_revision:row.revision,track_id:track.track_id,tag_code:code,decision})});
                const result=await response.json(); if (!response.ok) throw new Error(result.detail || "候選覆核未保存。");
                view.candidate_reviews ||= [];
                view.candidate_reviews.push({revision:result.revision,analysis_revision:row.revision,track_id:track.track_id,tag_code:code,decision});
                message("候選覆核已保存；原始 AI 結果和先前人工歷史都保留。");
              } catch(error) { message(error.message+" 如有版本衝突，請關閉後重新開啟此事件。"); }
              finally { candidateBusy=false; renderCandidateActions(); }
            });
          }
        });
      });
    });
    if (!root.children.length) element("p","目前沒有可逐項覆核的候選；仍可用下方三步人工回報。",root);
  }
  function renderValidity() {
    const root=byId("event-review-validity");root.replaceChildren();if(!view) return;
    const history=view.validity_history || [], latest=history.length ? history[history.length-1] : null;
    const labels={valid:"影像有效",invalid:"無效事件",unclear:"看不清，無法評估"};
    element("p",latest ? `我最近的覆核：${labels[latest.validity]}（第 ${latest.revision} 版）` : "尚未覆核，不會當成有效事件。",root);
    for(const [validity,label] of Object.entries(labels)) {
      const button=element("button",label,root);button.type="button";button.className="button secondary";button.disabled=!videoReady || candidateBusy;
      button.addEventListener("click",async()=> {
        if(candidateBusy || !videoReady) return;candidateBusy=true;renderCandidateActions();
        try {
          const response=await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/validity`,{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrf()},body:JSON.stringify({request_id:window.MeowKeeperRequestId(),expected_revision:latest?.revision || 0,validity})});
          const result=await response.json();if(!response.ok) throw new Error(result.detail || "覆核未保存。");view.validity_history ||= [];view.validity_history.push({revision:result.revision,validity});message("事件有效性已保存；API呼叫紀錄與原始候選仍保留。");
        } catch(error){message(error.message);}
        finally{candidateBusy=false;renderCandidateActions();}
      });
    }
  }
  const message = value => { byId("event-review-message").textContent = value; };
  function syncNext() { byId("event-review-next").disabled = busy || !view || !item?.clip_exists || !videoReady; }
  let exportBusy = false;
  byId("event-export-annotated").addEventListener("click", async () => {
    if (exportBusy || !item?.clip_exists) return;
    exportBusy = true;
    const button = byId("event-export-annotated"), status = byId("event-export-status");
    button.disabled = true;
    status.textContent = "正在分析與匯出；原始影片、監控和通知設定均不變。";
    try {
      const compare = byId("event-export-compare").checked;
      const response = await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/annotated-video?compare=${compare}`, { method: "POST", headers: { "X-MeowKeeper-CSRF": csrf() } });
      if (!response.ok) { const data = await response.json(); throw new Error(data.detail || "標記匯出未完成。"); }
      const metrics = JSON.parse(response.headers.get("X-MeowKeeper-Export") || "{}");
      const blob = await response.blob(), url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = "meowkeeper-ai-marked.mp4"; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 30000);
      status.textContent = `已產生下載：分析 ${metrics.analysis_ms} ms｜帶框轉碼 ${metrics.encoding_ms} ms${metrics.baseline_encoding_ms == null ? "" : `｜無框轉碼 ${metrics.baseline_encoding_ms} ms`}。未送 Telegram，沒有通知耗時。`;
    } catch (error) { status.textContent = error.message || "匯出失敗，原片保留。"; }
    finally { exportBusy = false; button.disabled = false; }
  });
  function updateVideoMessage() {
    if (!item?.clip_exists) {
      message("這筆事件沒有已保存的影片，為避免猜測，暫時不能提交人工回報。");
    } else if (videoFailed) {
      message("影片載入失敗。請確認仍在登入狀態，再按「重新載入影片」；影片正常播放後才能回報。");
    } else if (!videoReady) {
      message("正在準備事件影片；載入完成後，請先播放確認畫面，再開始回報。");
    } else if (view) {
      message((view.conflict ? "已有不同回報，請以影片重新確認；歷史不會被覆蓋。 " : "") + "影片已載入。請先播放確認畫面，再對照選擇。");
    }
    syncNext();
  }
  function element(tag,text,parent) { const node = document.createElement(tag); if (text) node.textContent = text; parent?.append(node); return node; }
  function shownTime(value) {
    const raw=typeof value === "string" && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value) ? value.replace(" ","T")+"Z" : value;
    const date=new Date(raw); return Number.isFinite(date.getTime()) ? date.toLocaleString("zh-TW") : "時間尚未提供";
  }
  function renderWaterfall() {
    const root=byId("event-review-waterfall");root.replaceChildren();
    const timestamp=value=>typeof value === "string" && /(?:Z|[+]00:00)$/.test(value) && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
    (view.delivery_history || []).forEach((trace,index)=> {
      const result=(view.semantic_history || []).find(row=>row.result?.job_id===trace.semantic_job_id)?.result;
      const timing=result?.timings || {}, spans=[];
      function span(label,start,end,waiting=false) {const from=timestamp(start),to=timestamp(end);if(from !== null && to !== null && to>=from) spans.push({label,from,to,waiting});}
      span("AI等輪到它",timing.queued_at,timing.started_at,true);
      span("整理AI參考圖片",timing.started_at,timing.prepared_at);
      span("AI網路請求至回覆",timing.http_started_at,timing.response_received_at);
      span("整理事件影片",trace.event_trigger_at,trace.media_sendable_at);
      span("影片可送後額外等AI",trace.wait_started_at,trace.wait_finished_at,true);
      (trace.uploads || []).forEach((upload,number)=>span(`通知上傳嘗試 ${number+1}（${upload.accepted ? "Telegram已接受" : "未確認成功"}）`,upload.started_at,upload.finished_at));
      const card=element("section","",root);element("h4",`第 ${index+1} 次通知・已知時間軸`,card);
      if(!spans.length) {element("p","缺少可靠時間紀錄，無法畫出這次的平行工作。原影片與回報仍可使用。",card);return;}
      const origin=Math.min(...spans.map(value=>value.from)),finish=Math.max(...spans.map(value=>value.to)),width=Math.max(1,finish-origin);
      const accepted=(trace.uploads || []).filter(value=>value.accepted && timestamp(value.finished_at)!==null);
      const detected=timestamp(trace.event_trigger_at);
      if(detected !== null && accepted.length) {const done=Math.max(...accepted.map(value=>timestamp(value.finished_at)));if(done>=detected)element("p",`從發現到此紀錄內最後一次Telegram接受：${((done-detected)/1000).toFixed(2)}秒；不是手機已讀。`,card);}
      element("p","虛線：等待／實線：處理；目前沒有完整依賴證據，不宣稱哪個是主要瓶頸。",card).className="field-help";
      spans.forEach(value=> {
        const details=element("details","",card), summary=element("summary",`${value.label} · ${((value.to-value.from)/1000).toFixed(2)}秒`,details);
        const lane=element("div","",summary);lane.className="semantic-waterfall-lane";lane.setAttribute("aria-hidden","true");
        const bar=element("span","",lane);bar.className=value.waiting ? "semantic-waterfall-bar waiting" : "semantic-waterfall-bar";
        bar.style.marginInlineStart=((value.from-origin)/width*100)+"%";bar.style.width=Math.max(.2,(value.to-value.from)/width*100)+"%";
        element("p",`開始：${new Date(value.from).toLocaleString("zh-TW")}；結束：${new Date(value.to).toLocaleString("zh-TW")}；${value.waiting ? "這是等待，不是AI持續運算；無法確定排隊原因。" : "這是已知處理區間，不代表判斷一定正確。"}`,details);
      });
      if(!timing.http_started_at || !timing.response_received_at) element("p","此筆沒有完整HTTP開始／回覆時刻，未以工作程序時間冒充純網路區間。",card).className="field-help";
    });
    if(!root.children.length) element("p","尚無通知時間紀錄；無法推論事件為何較慢。",root);
  }
  function close() { byId("event-review-video").pause(); if (dialog.close) dialog.close(); else dialog.removeAttribute("open"); }
  function loadReviewVideo(retry = false, compatible = false) {
    const video = byId("event-review-video");
    clearTimeout(videoFallbackTimer);
    videoReady = false; videoFailed = false;
    videoPlaybackMode = compatible ? "compatible" : "native";
    byId("event-review-video-retry").hidden = true;
    const retryQuery = retry ? `&retry=${Date.now()}` : "";
    video.src = `/api/v1/events/${encodeURIComponent(item.event_id)}/video?${compatible ? "playback=true" : "native=true"}${retryQuery}`;
    video.load();
    if (!compatible) videoFallbackTimer = setTimeout(() => {
      if (dialog.open && item?.clip_exists && videoPlaybackMode === "native" && video.readyState < 2) {
        loadReviewVideo(retry, true);
      }
    }, 12000);
    updateVideoMessage();
  }
  function render() {
    const root = byId("event-review-stage"); root.replaceChildren();
    byId("event-review-back").hidden = step === 1; byId("event-review-next").textContent = step === 3 ? withdrawing ? "確認撤回這段" : "確認保存回報" : "下一步";
    dialog.querySelectorAll(".review-steps li").forEach((node,index) => { node.classList.toggle("active",index+1 === step); });
    element("h4",step === 1 ? "第一步：畫面有哪些貓咪？" : step === 2 ? "第二步：每隻貓在做什麼？" : "第三步：確認後保存",root);
    if (step === 1) {
      const noLabel = element("label","",root), no = element("input","",noLabel); no.type = "checkbox"; no.checked = notCat;
      element("span","不是貓咪",noLabel);
      no.addEventListener("change",() => { notCat = no.checked; if (notCat) selected = []; render(); });
      if (!view.pets.length) element("p","尚未登錄寵物；請先到寵物檔案新增，或確認此事件不是貓咪。",root);
      view.pets.forEach(pet => {
        const label = element("label","",root), input = element("input","",label); input.type = "checkbox";
        input.checked = selected.includes(pet.pet_id); input.disabled = notCat;
        element("span",pet.name,label);
        input.addEventListener("change",() => { selected = input.checked ? [...selected,pet.pet_id] : selected.filter(id=>id!==pet.pet_id); });
      });
      if (view.pets.length === 2) {
        const both = element("button","兩隻都在",root); both.type="button"; both.className="button secondary";
        both.addEventListener("click",() => { notCat=false; selected=view.pets.map(p=>p.pet_id); render(); });
      }
    } else if (step === 2) {
      selected.forEach(id => {
        const pet = view.pets.find(p=>p.pet_id===id), card = element("section","",root); card.className="review-pet";
        element("h4",`${pet.name}的動作（可多選）`,card); actions[id] ||= {behavior:null,motion:null,tags:[]};
        const tagsRoot=element("div","",card); tagsRoot.className="review-tag-options";
        Object.entries(view.tags || {}).forEach(([value,name]) => {
          const label=element("label","",tagsRoot), input=element("input","",label); input.type="checkbox";
          input.value=value;
          input.checked=actions[id].tags.includes(value); element("span",name,label);
          input.addEventListener("change",()=> {
            if (value === "unknown" && input.checked) actions[id].tags=["unknown"];
            else actions[id].tags=input.checked ? [...actions[id].tags.filter(tag=>tag!=="unknown"),value] : actions[id].tags.filter(tag=>tag!==value);
            tagsRoot.querySelectorAll('input[type="checkbox"]').forEach(box=>{box.checked=actions[id].tags.includes(box.value);});
          });
        });
        element("p","至少勾選一項；例如行走與玩耍可以同時選擇。請依影片確認，AI 候選不會自動替你勾選。",card).className="field-help";
      });
    } else {
      if (withdrawing) element("p",`即將撤回第 ${editingRevision} 版片段；原紀錄仍保留，不作為新的確認答案。`,root);
      element("p",notCat ? "確認為非貓事件。" : `確認此事件有 ${selected.length} 隻已登錄貓咪：`,root);
      selected.forEach(id=> { const p=view.pets.find(p=>p.pet_id===id), a=actions[id]; element("p",`${p.name}：${a.tags.map(tag=>view.tags[tag]).join("／")}`,root); });
      element("p",`回報範圍：${byId("event-review-segment-start").value}～${byId("event-review-segment-end").value} 秒`,root);
      element("p","提交會追加新修訂，不覆寫 Telegram 回報或既有歷史；人工結果不等於 AI 已確定。",root).className="field-help";
    }
  }

  async function loadAndRenderIdentityEvidence(eventId) {
    const root = byId("event-review-ai-evidence");
    if (!root) return;
    root.innerHTML = '<p class="muted">正在讀取 AI 身分比對證據…</p>';
    if (!eventId) {
      root.innerHTML = '<p class="muted">無此事件識別碼。</p>';
      return;
    }
    const reasons = {
      insufficient_evidence: '獨立影格不足', unstable_votes: '跨影格結果不一致',
      small_margin: '候選太接近', low_similarity: '相似度不足',
      no_gallery: '尚無有效特徵', model_mismatch: '模型版本不相容',
      track_ambiguous: '軌跡可能混淆', calibration_required: '尚未通過家庭校準'
    };
    try {
      const response = await fetch(`/api/v1/events/${encodeURIComponent(eventId)}/identity`, { cache: "no-store" });
      if (!response.ok) throw new Error("身分證據讀取失敗。");
      const data = await response.json();
      root.replaceChildren();
      const tracks = Array.isArray(data.tracks) ? data.tracks : [];
      if (!tracks.length) {
        root.innerHTML = '<p class="muted">此事件尚無逐影格身分比對證據（可能尚未提取或非貓事件）。</p>';
        return;
      }
      tracks.forEach((track, idx) => {
        const card = document.createElement("article");
        card.className = "review-evidence-card";

        const title = document.createElement("h5");
        title.textContent = `證據貓咪 ${idx + 1}：${track.decision === 'unknown' ? '身分尚未確認' : 'AI 候選識別'}`;
        card.appendChild(title);

        const info = document.createElement("p");
        info.className = "field-help";
        info.textContent = `影格跨度 ${(track.evidence_seconds || 0).toFixed(1)} 秒｜有效影格 ${track.valid_frames || 0}｜判定原因：${reasons[track.unknown_reason] || track.unknown_reason || '特徵比對完成'}`;
        card.appendChild(info);

        const mediaRow = document.createElement("div");
        mediaRow.className = "review-evidence-body";

        if (track.crop_url) {
          const fig = document.createElement("figure");
          fig.className = "review-evidence-crop";
          const img = document.createElement("img");
          img.src = track.crop_url;
          img.alt = "當次實拍裁切";
          img.onerror = () => { fig.hidden = true; };
          const cap = document.createElement("figcaption");
          cap.className = "field-help";
          cap.textContent = "實拍裁切 (DINOv2)";
          fig.append(img, cap);
          mediaRow.appendChild(fig);
        }

        const candList = document.createElement("div");
        candList.className = "review-evidence-candidates";
        (track.candidates || []).forEach(cand => {
          const cBox = document.createElement("div");
          cBox.className = "review-candidate-item";
          const cTitle = document.createElement("strong");
          cTitle.textContent = `可能是 ${cand.name}｜相似度 ${Number(cand.pet_score || 0).toFixed(3)}`;
          cBox.appendChild(cTitle);

          const refList = document.createElement("div");
          refList.className = "review-ref-list";
          (cand.reference_matches || []).forEach(ref => {
            if (ref.thumbnail_url) {
              const rFig = document.createElement("figure");
              rFig.className = "review-ref-item";
              const rImg = document.createElement("img");
              rImg.src = ref.thumbnail_url;
              rImg.alt = `${cand.name} 參考照片`;
              rImg.onerror = () => { rFig.hidden = true; };
              const rCap = document.createElement("figcaption");
              rCap.className = "field-help";
              rCap.textContent = `參考照相似度 ${Number(ref.cosine_score || 0).toFixed(3)}`;
              rFig.append(rImg, rCap);
              refList.appendChild(rFig);
            }
          });
          if (refList.children.length) {
            cBox.appendChild(refList);
          }
          candList.appendChild(cBox);
        });
        mediaRow.appendChild(candList);
        card.appendChild(mediaRow);
        root.appendChild(card);
      });
    } catch (err) {
      root.innerHTML = `<p class="muted">讀取身分證據失敗：${err.message}</p>`;
    }
  }

  document.addEventListener("meowkeeper:review",async event=> {
    if (busy) return; busy=true; item=event.detail; view=null; step=1; selected=[]; actions={}; notCat=false;
    requestId=window.MeowKeeperRequestId(); videoReady=false; videoFailed=false; submitted=false;
    reanalysisId=window.MeowKeeperRequestId();
    editingRevision=null; withdrawing=false;
    segmentPlaybackEnd=null;
    byId("event-review-candidate-actions").replaceChildren();
    byId("event-review-segment-start").value="0"; byId("event-review-segment-end").value="";
    byId("event-review-candidates").textContent="";
    message(item.clip_exists ? "正在讀取事件與回報歷史及影片…" : "這筆事件沒有已保存的影片，為避免猜測，暫時不能提交人工回報。");
    loadAndRenderIdentityEvidence(item.event_id);
    byId("event-export-annotated").disabled = exportBusy || !item.clip_exists;
    if (!exportBusy) byId("event-export-status").textContent = "";
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute("open","");
    const video=byId("event-review-video"); video.hidden=!item.clip_exists; video.pause();
    byId("event-review-video-retry").hidden=true;
    if (item.clip_exists) loadReviewVideo(); else { video.removeAttribute("src"); video.load(); }
    try {
      const response=await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/review`,{cache:"no-store"});
      if (!response.ok) throw new Error("事件目前無法回報，請確認登入與媒體紀錄。");
      view=await response.json(); render(); updateVideoMessage();
      renderCandidateActions();
      const candidate=view.semantic_candidates;
      byId("event-review-candidates").textContent=candidate?.status === "completed" ?
        "AI 動作候選（請依影片確認）："+candidate.tracks.map((track,index)=>`貓咪 #${index+1}：${track.tags.map(tag=>view.tags[tag]).filter(Boolean).join("、") || "尚無候選"}`).join("；") :
        "目前尚無已完成的 AI 動作候選，你仍可依影片自行回報。";
      const history=byId("event-review-history"); history.replaceChildren();
      const semanticHistory=byId("event-review-semantic-history");
      semanticHistory.replaceChildren();
      const analysisStates={completed:"分析完成，仍需確認",failed:"進階分析未完成",timeout:"AI 等候逾時",cancelled:"已取消",queue_full:"工作較忙，已略過",invalid_image:"圖片無法分析",auth_error:"服務金鑰或權限需確認",rate_limited:"服務忙碌或達使用限制",unavailable:"進階服務暫時無法連線",bad_response:"服務答案無法使用",skipped_disabled:"進階分析未啟用",skipped_no_consent:"未同意影像外送",skipped_busy:"進階工作較忙，使用本機摘要",insufficient_evidence:"參考影格證據不足",skipped_budget:"已達每日分析上限，影片照常",circuit_open:"進階服務暫停請求，影片照常",duplicate_request:"同一工作不重複外送"};
      (view.semantic_history || []).forEach(row=> {
        const result=row.result || {};
        const tags=(result.tracks || []).map(track=>(track.tags || []).map(tag=>view.tags[tag]).filter(Boolean).join("、")).filter(Boolean).join("；");
        element("p",`候選第 ${row.revision} 版｜${shownTime(row.created_at)}｜${analysisStates[result.status] || "狀態尚未確認"}${tags ? "｜"+tags : ""}`,semanticHistory);
        const timing=result.timings || {};
        const fields=[["queue_ms","排隊"],["api_response_ms","等待 AI 回覆"],["validation_ms","檢查答案"]];
        const durations=fields.filter(([key])=>typeof timing[key] === "number" && Number.isFinite(timing[key])).map(([key,label])=>`${label} ${(timing[key]/1000).toFixed(2)} 秒`);
        element("p",durations.length ? durations.join("；") : "這一版尚無各階段時間紀錄，不能判斷慢在哪裡。",semanticHistory);
        (result.tag_evidence || []).forEach(evidence=> {
          const index=(result.tracks || []).findIndex(track=>track.track_id===evidence.track_id);
          const points=(evidence.observed_offsets_seconds || []).filter(value=>typeof value === "number" && Number.isFinite(value)).map(value=>`${value < 0 ? "觸發前" : "觸發後"} ${Math.abs(value).toFixed(2)} 秒`);
          const certainty={low:"證據偏弱",medium:"有部分證據",high:"證據較明顯",unknown:"尚不確定"}[evidence.certainty] || "尚不確定";
          element("p",`貓咪 #${index+1}｜${view.tags[evidence.code] || "未確認標籤"}（AI 候選，${certainty}）｜觀察點：${points.join("、")}。這不是影片中的精準動作起迄。`,semanticHistory);
        });
      });
      if (!semanticHistory.children.length) semanticHistory.textContent="尚無已保存的 AI 候選歷史；人工回報仍可使用。";
      const deliveryHistory=byId("event-review-delivery-history");
      deliveryHistory.replaceChildren();
      (view.delivery_history || []).forEach((trace,index)=> {
        const card=element("section","",deliveryHistory);
        element("h4",`第 ${index+1} 次通知處理｜影片可發送：${shownTime(trace.media_sendable_at)}`,card);
        const used=trace.caption_source === "ai_candidate";
        element("p",used ? "通知使用 AI 動作候選；仍不是人工確認。" : "通知使用本機摘要；不等於錄影或偵測失敗。",card);
        const reasons={not_requested:"未要求進階候選",candidate_empty:"候選沒有可用動作"};
        element("p",`候選結果：${analysisStates[trace.wait_status] || reasons[trace.wait_status] || "尚未確認"}。`,card);
        const validMs=value=>typeof value === "number" && Number.isFinite(value) && value >= 0;
        const phases=[];
        if (validMs(trace.video_prepare_ms)) phases.push(["準備影片（含佇列、後錄與必要轉碼）",trace.video_prepare_ms]);
        else element("p","這筆尚無完整影片準備耗時，不能補成 0 秒。",card).className="field-help";
        phases.push(["相容性檢查／必要轉碼（已包含在影片準備中）",trace.validation_transcode_ms],
                    ["影片準備好後額外等 AI",trace.extra_wait_ms]);
        const recipients=new Map();
        (trace.uploads || []).forEach(upload=> {
          if (!recipients.has(upload.recipient_ref)) recipients.set(upload.recipient_ref,recipients.size+1);
          phases.push([`收件對象 #${recipients.get(upload.recipient_ref)} 第 ${upload.attempt} 次上傳：${upload.accepted ? "Telegram 已接受" : "尚未確認成功"}`,upload.elapsed_ms]);
        });
        const maximum=Math.max(1,...phases.filter(([,ms])=>validMs(ms)).map(([,ms])=>ms));
        const list=element("ol","",card);
        phases.forEach(([label,ms])=> {
          if (!validMs(ms)) return;
          const row=element("li",`${label}：${(ms/1000).toFixed(2)} 秒 `,list);
          const meter=element("meter","",row); meter.min=0; meter.max=maximum; meter.value=ms;
          meter.setAttribute("aria-label",`${label}耗時 ${(ms/1000).toFixed(2)} 秒`);
        });
        if (validMs(trace.wait_budget_ms)) {
          element("p",`本次 AI 等待設定上限：${(trace.wait_budget_ms/1000).toFixed(1)} 秒。`,card);
          if (validMs(trace.extra_wait_ms) && trace.extra_wait_ms > trace.wait_budget_ms+1)
            element("p","實測等待超出設定，可能包含程序排程延遲；此處保留實測值，不把它改小。",card).className="notice";
        }
        if (trace.omitted_upload_count > 0) element("p",`另有 ${trace.omitted_upload_count} 次上傳未列入此明細，不能視為 0 次。`,card);
      });
      if (!deliveryHistory.children.length) deliveryHistory.textContent="尚無已保存的通知耗時紀錄；舊事件或通知尚未完成不代表耗時為 0。";
      renderWaterfall();
      [...view.history,...view.telegram_history,...view.telegram_pet_history,...(view.telegram_tag_history || [])].forEach(row=> {
        const labels = row.pets || (row.pet_id ? [{pet_id:row.pet_id,behavior:row.behavior,motion:row.motion}] : []);
        const details = labels.map(p=>`${view.pets.find(pet=>pet.pet_id===p.pet_id)?.name || p.name || p.pet_id}：${[view.behaviors[p.behavior],view.motions[p.motion],...(p.tags || []).map(tag=>view.tags[tag])].filter(Boolean).join("／")}`).join("；");
        const range=row.scope?.segment;
        const entry=element("p",`${shownTime(row.created_at)}｜${row.channel || "Telegram"}｜${row.label === "not_cat" ? "非貓" : row.label === "cat" ? "是貓咪" : "逐貓回報"}${row.revision ? "｜修訂 "+row.revision : ""}${range ? `｜${range.start_seconds}～${range.end_seconds} 秒` : ""}${details ? "｜"+details : ""}${row.scope?.withdrawn ? "｜已撤回" : ""}${row.scope?.supersedes_revision ? `｜取代第 ${row.scope.supersedes_revision} 版` : ""}`,history);
        if (row.channel === "Web" && range && !row.scope?.withdrawn && !view.history.some(value=>value.scope?.supersedes_revision===row.revision)) {
          for (const [withdraw,label] of [[false,"修改這段範圍／標籤"],[true,"撤回這段回報"]]) {
            const button=element("button",label,entry);button.type="button";button.className="button secondary";
            button.addEventListener("click",()=> {
              if (busy || !videoReady) {message("請先確認影片能播放。");return;}
              const missing=(row.pets || []).some(pet=>!view.pets.some(value=>value.pet_id===pet.pet_id));
              if (missing) {message("此片段包含已停用寵物，請先恢復寵物檔案再修訂。");return;}
              editingRevision=row.revision;withdrawing=withdraw;submitted=false;requestId=window.MeowKeeperRequestId();
              selected=(row.pets || []).map(pet=>pet.pet_id);notCat=row.label==="not_cat";actions={};
              (row.pets || []).forEach(pet=>{actions[pet.pet_id]={behavior:pet.behavior,motion:pet.motion,tags:pet.tags || []};});
              for (const side of ["start","end"]) {const value=range[`${side}_seconds`];byId(`event-review-segment-${side}`).value=value;}
              syncSegmentRanges();
              step=withdraw ? 3 : 1;render();message(withdraw ? "確認提交後才撤回此段；原歷史仍保留。" : "修改範圍或標籤後，請按三步確認保存；舊版保留。");
            });
          }
        }
      });
      if (!history.children.length) history.textContent="尚無人工回報。";
    } catch (error) { message(error.message); }
    finally { busy=false; syncNext(); }
  });
  byId("event-review-close").addEventListener("click",close); dialog.addEventListener("close",()=>byId("event-review-video").pause());
  byId("event-review-reanalyze").addEventListener("click",async()=> {
    if(!item || !view || busy || !window.confirm("將重用這筆事件已保存的必要貓框時序拼貼，交給目前已同意的服務再分析一次，可能產生費用；不重傳影片。是否繼續？")) return;
    const button=byId("event-review-reanalyze");button.disabled=true;
    try {
      const response=await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/reanalyze`,{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrf()},body:JSON.stringify({request_id:reanalysisId,confirm_saved_crops:true})});
      const result=await response.json();if(!response.ok) throw new Error(result.detail || "重新分析未排程。");message(result.message_zh_tw);
    } catch(error) {message(error.message);}
    finally{button.disabled=false;}
  });
  byId("event-review-video").addEventListener("canplay",()=> { clearTimeout(videoFallbackTimer); videoReady=true; videoFailed=false; updateVideoMessage(); renderCandidateActions(); });
  function syncSegmentRanges(changedSide) {
    const video=byId("event-review-video"), duration=Number.isFinite(video.duration)&&video.duration>0?video.duration:Math.max(Number(byId("event-review-range-start").max)||0,Number(byId("event-review-range-end").max)||0);
    if(!(duration>0))return;
    const gap=Math.min(.1,duration), startInput=byId("event-review-segment-start"), endInput=byId("event-review-segment-end");
    let start=Math.max(0,Math.min(duration,Number(startInput.value)||0)), end=Math.max(0,Math.min(duration,Number(endInput.value)||0));
    if(end-start<gap){if(changedSide==="start")start=Math.max(0,end-gap);else end=Math.min(duration,start+gap);if(end-start<gap)start=Math.max(0,end-gap);}
    startInput.max=endInput.max=String(duration);startInput.value=String(Math.round(start*10)/10);endInput.value=String(Math.round(end*10)/10);
    const left=byId("event-review-range-start"),right=byId("event-review-range-end");left.max=right.max=String(duration);left.value=startInput.value;right.value=endInput.value;
    const timeline=byId("event-review-timeline");timeline?.style.setProperty("--segment-left",`${start/duration*100}%`);timeline?.style.setProperty("--segment-right",`${end/duration*100}%`);
  }
  byId("event-review-video").addEventListener("loadedmetadata",()=> {
    const duration=byId("event-review-video").duration;
    if (Number.isFinite(duration) && duration > 0) {
      if (!byId("event-review-segment-end").value) byId("event-review-segment-end").value=String(Math.floor(duration*10)/10);
      syncSegmentRanges();
    }
  });
  for (const side of ["start","end"]) {
    const number=byId(`event-review-segment-${side}`), range=byId(`event-review-range-${side}`);
    range.addEventListener("input",()=> { number.value=range.value; syncSegmentRanges(side); segmentPlaybackEnd=null; });
    number.addEventListener("input",()=> { syncSegmentRanges(side); segmentPlaybackEnd=null; });
    byId(`event-review-mark-${side}`).addEventListener("click",()=> {
      const video=byId("event-review-video"); if (!videoReady || !Number.isFinite(video.currentTime)) return;
      number.value=String(Math.floor(video.currentTime*10)/10); syncSegmentRanges(side); segmentPlaybackEnd=null;
    });
  }
  byId("event-review-play-segment").addEventListener("click",()=> {
    const start=Number(byId("event-review-segment-start").value), end=Number(byId("event-review-segment-end").value), video=byId("event-review-video");
    if (!videoReady || !(0 <= start && start < end && end <= video.duration)) { message("請先選擇有效的影片起點與終點。"); return; }
    segmentPlaybackEnd=end; video.currentTime=start;
    video.play().catch(()=>message("播放尚未開始，請直接按影片播放鍵。"));
  });
  byId("event-review-video").addEventListener("timeupdate",()=> {
    const video=byId("event-review-video");
    if (segmentPlaybackEnd !== null && video.currentTime >= segmentPlaybackEnd) { video.pause(); segmentPlaybackEnd=null; }
  });
  byId("event-review-video").addEventListener("error",()=> {
    clearTimeout(videoFallbackTimer);
    if (videoPlaybackMode === "native" && item?.clip_exists) { loadReviewVideo(false, true); return; }
    videoFailed=true; videoReady=false; byId("event-review-video-retry").hidden=false; updateVideoMessage();
  });
  byId("event-review-video").addEventListener("waiting",()=> { if (!videoReady) updateVideoMessage(); });
  byId("event-review-video").addEventListener("playing",()=> { videoReady=true; videoFailed=false; syncNext(); message("事件影片正在播放；請對照畫面逐步回報。"); });
  byId("event-review-video-retry").addEventListener("click",()=> { if (item?.clip_exists) loadReviewVideo(true); });
  byId("event-review-back").addEventListener("click",()=> { if (busy || !view || submitted) return; step=notCat ? 1 : Math.max(1,step-1); message(""); render(); });
  byId("event-review-next").addEventListener("click",async()=> {
    if (!view || busy) return;
    if (submitted) {
      submitted=false; step=1; selected=[]; actions={}; notCat=false; editingRevision=null;withdrawing=false;requestId=window.MeowKeeperRequestId();
      const lastEnd=Number(byId("event-review-segment-end").value), duration=byId("event-review-video").duration;
      byId("event-review-segment-start").value=String(lastEnd < duration ? lastEnd : 0);
      byId("event-review-segment-end").value=String(Math.floor(duration*10)/10);
      syncSegmentRanges();
      message("請對照下一段影片重新選擇；已保存的上一段回報會保留。"); render(); return;
    }
    if (step === 1) { if (!notCat && !selected.length) { message("請選擇至少一隻貓咪，或選擇不是貓咪。"); return; } step=notCat ? 3 : 2; render(); return; }
    if (step === 2) { if (selected.some(id=>!actions[id]?.tags?.length)) { message("請逐隻勾選至少一項動作。"); return; } step=3; render(); return; }
    const segment={start_seconds:Number(byId("event-review-segment-start").value),end_seconds:Number(byId("event-review-segment-end").value)};
    const duration=byId("event-review-video").duration;
    if (!Number.isFinite(segment.start_seconds) || !Number.isFinite(segment.end_seconds) || !(segment.start_seconds >= 0 && segment.start_seconds < segment.end_seconds && segment.end_seconds <= duration)) { message("請輸入影片範圍內的開始與結束秒數。"); return; }
    busy=true; byId("event-review-next").disabled=true; message("正在保存人工回報…");
    const cookie=document.cookie.split(";").map(s=>s.trim()).find(s=>s.startsWith("meowkeeper_csrf="));
    try {
      const response=await fetch(`/api/v1/events/${encodeURIComponent(item.event_id)}/review`,{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":cookie ? decodeURIComponent(cookie.slice("meowkeeper_csrf=".length)) : ""},body:JSON.stringify({request_id:requestId,expected_revision:view.revision,label:notCat ? "not_cat" : "cat",segment,pets:notCat ? [] : selected.map(id=>({pet_id:id,...actions[id]})),...(editingRevision ? {supersedes_revision:editingRevision,withdrawn:withdrawing} : {})})});
      const result=await response.json(); if (!response.ok) throw new Error(result.detail || "保存未完成。");
      message(`已保存修訂 ${result.revision}${withdrawing ? "（片段撤回）" : ""}；人工歷史保留，沒有自動重訓模型。`);
      const history=byId("event-review-history"); if (!history.children.length) history.replaceChildren();
      const detail=notCat ? "非貓" : selected.map(id=>`${view.pets.find(p=>p.pet_id===id).name}：${actions[id].tags.map(tag=>view.tags[tag]).join("／")}`).join("；");
      element("p",`Web｜修訂 ${result.revision}${withdrawing ? `（撤回第 ${editingRevision} 版片段）` : editingRevision ? `（取代第 ${editingRevision} 版）` : ""}｜${segment.start_seconds}～${segment.end_seconds} 秒｜${detail}`,history);
      const refresh=element("button","重新載入以編輯／撤回已存片段",history);refresh.type="button";refresh.className="button secondary";
      history.querySelectorAll("button").forEach(button=>{if(button!==refresh)button.disabled=true;});
      refresh.addEventListener("click",()=>{close();document.dispatchEvent(new CustomEvent("meowkeeper:review",{detail:item}));});
      view.revision=result.revision; submitted=true;
      try {
        const savedEvent=item.event_id, latest=await fetch(`/api/v1/events/${encodeURIComponent(savedEvent)}/review`,{cache:"no-store"});
        if(!latest.ok)throw new Error();const updated=await latest.json();
        if(item?.event_id===savedEvent){view=updated;renderCandidateActions();}
      } catch {
        const summary=byId("event-review-active-assessment");if(summary)summary.textContent="回報已成功保存；最新有效摘要暫時無法重讀，重新開啟事件即可核對，請勿重複提交。";
      }
      byId("event-review-next").textContent="回報另一段"; byId("event-review-back").hidden=true;
    } catch(error) { message(error.message+" 請勿重新猜測；可重試，重複提交不會新增相同修訂。"); }
    finally { busy=false; syncNext(); }
  });
})();
