(function () {
  "use strict";
  const form = document.getElementById("ai-settings-form");
  if (!form) return;
  const message = document.getElementById("ai-message");
  const runtime = document.getElementById("ai-runtime-status");
  const analyzer = document.getElementById("ai-analyzer-mode");
  const device = document.getElementById("ai-inference-device");
  const fields = {
    confidence_threshold: document.getElementById("ai-confidence-threshold"),
    identity_accepted_threshold: document.getElementById("ai-identity-accepted-threshold"),
    identity_minimum_margin: document.getElementById("ai-identity-minimum-margin"),
    analysis_fps: document.getElementById("ai-analysis-fps"),
    analysis_width: document.getElementById("ai-analysis-width"),
    activity_fps: document.getElementById("ai-activity-fps"),
    identity_frame_width: document.getElementById("ai-identity-frame-width"),
    preview_frame_width: document.getElementById("ai-preview-frame-width"),
    cooldown_seconds: document.getElementById("ai-cooldown-seconds"),
    event_pre_seconds: document.getElementById("ai-event-pre-seconds"),
    event_post_seconds: document.getElementById("ai-event-post-seconds"),
    retention_seconds: document.getElementById("ai-retention-seconds"),
    ring_maximum_size_mb: document.getElementById("ai-ring-maximum-size-mb"),
  };
  const csrfToken = () => {
    const item = document.cookie.split(";").map((value) => value.trim()).find((value) => value.startsWith("meowkeeper_csrf="));
    return item ? decodeURIComponent(item.substring("meowkeeper_csrf=".length)) : "";
  };
  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: "same-origin", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "AI／行為設定操作失敗。");
    return data;
  }
  function render(data) {
    const values = data.settings || data;
    analyzer.value = values.analyzer_mode || "yolo";
    device.value = values.inference_device || "auto";
    analyzer.disabled = Boolean(values.analyzer_override);
    document.getElementById("ai-analyzer-note").textContent = values.analyzer_override
      ? "啟動時已固定分析模式，此欄暫不可修改；請由維護者調整啟動設定。"
      : "基本找貓用於貓咪事件；追蹤與行為仍屬實驗性。保存後重新啟動服務才會套用。";
    Object.entries(fields).forEach(([name, input]) => {
      if (input && values[name] !== undefined && values[name] !== null) input.value = values[name];
    });
    const restart = data.restart_required ? "；重新啟動 Host 後套用" : "";
    runtime.textContent = `設定已載入${restart}`;
  }
  async function load() {
    render(await request("/api/v1/settings", { headers: { Accept: "application/json" } }));
  }
  async function loadCatFeedback() {
    const status = document.getElementById("cat-feedback-status");
    const summary = document.getElementById("cat-feedback-summary");
    const buckets = document.getElementById("cat-feedback-buckets");
    try {
      const data = await request("/api/v1/analytics/cat-feedback", { headers: { Accept: "application/json" } });
      const interval = data.reviewed_alert_precision_wilson_95;
      const precision = data.reviewed_alert_precision == null ? "尚無一致人工標註" : `${(data.reviewed_alert_precision * 100).toFixed(1)}%`;
      const uncertainty = interval ? `；95% Wilson 區間 ${(interval.lower * 100).toFixed(1)}%～${(interval.upper * 100).toFixed(1)}%` : "";
      const labels = data.manual_annotation_events || {};
      summary.textContent = `模型告警 ${data.detector_alerts} 筆；已回饋 ${data.reviewed_events} 筆；未回饋 ${data.unreviewed_events} 筆；意見不一致 ${data.disputed_events} 筆。已審核告警 precision：${precision}${uncertainty}（一致標註 ${data.confirmed_cat + data.confirmed_not_cat} 筆）。個體人工標註 ${labels.identity || 0} 筆；行為人工標註 ${labels.behavior || 0} 筆。這不是整體準確率或召回率；人工標註不代表模型已學會。`;
      buckets.replaceChildren();
      (data.confidence_score_buckets || []).forEach((item) => {
        const row = document.createElement("p");
        const bandInterval = item.precision_wilson_95;
        const bandPrecision = item.reviewed_alert_precision == null ? "尚無一致標註" : `${(item.reviewed_alert_precision * 100).toFixed(1)}%`;
        const bandUncertainty = bandInterval ? `，95% 區間 ${ (bandInterval.lower * 100).toFixed(1) }%～${ (bandInterval.upper * 100).toFixed(1) }%` : "";
        row.textContent = `模型分數 ${item.range}：已審核告警 precision ${bandPrecision}${bandUncertainty}；一致樣本 ${item.consensus}，是貓咪 ${item.cat}，不是貓咪 ${item.not_cat}，意見不一致 ${item.disputed}`;
        buckets.appendChild(row);
      });
      status.textContent = data.status === "sampled" ? "僅代表已回覆告警的統計" : "尚無可用標註";
    } catch (error) {
      status.textContent = "統計暫不可用";
      summary.textContent = error.message || "需要登入後才能讀取回饋統計。";
    }
  }
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = {};
    payload.inference_device = device.value;
    Object.entries(fields).forEach(([name, input]) => { payload[name] = Number(input.value); });
    if (!analyzer.disabled) payload.analyzer_mode = analyzer.value;
    try {
      const data = await request("/api/v1/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ ...payload, reauth: true }),
      });
      render(data);
      message.textContent = data.message_zh_tw || "AI／行為設定已保存。";
    } catch (error) { message.textContent = `設定未保存：${error.message || "請確認登入狀態。"}`; }
  });
  document.getElementById("reset-ai-settings").addEventListener("click", async () => {
    if (!window.confirm("確認恢復 AI／行為預設值？需要重新啟動 Host 才會套用。")) return;
    try {
      const data = await request("/api/v1/settings/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-MeowKeeper-CSRF": csrfToken() },
        body: JSON.stringify({ reauth: true }),
      });
      render(data);
      message.textContent = data.message_zh_tw || "已恢復預設值。";
    } catch (error) { message.textContent = `預設值未恢復：${error.message || "請確認登入狀態。"}`; }
  });
  load().catch((error) => { message.textContent = error.message || "需要登入後才能讀取 AI／行為設定。"; });
  loadCatFeedback();
  let adaptiveLoading = false;
  async function loadAdaptive(snapshot = null) {
    if (document.hidden || adaptiveLoading) return;
    adaptiveLoading = true;
    const display = document.getElementById("ai-adaptive-status");
    try {
      const data = snapshot || await request("/api/v1/status", { headers: { Accept: "application/json" } });
      const ai = data.metrics?.ai || {};
      const adaptive = ai.adaptive;
      display.replaceChildren();
      if(!adaptive){display.textContent="尚未取得 AI 分析狀態；請先啟動監控。";return;}
      const mb=value=>value==null?'未知':`${(value*1.048576).toFixed(0)} MB`;
      const bottlenecks={suspended:'AI 已暫停；需檢查原因後手動重試',analysis_processing:'分析處理耗時超過目前預算；尚不能歸因 GPU 滿載',waiting_or_non_model_work_unresolved:'單張耗時在預算內，但吞吐偏低；需檢查排程、來源及非模型工作',activity_sampling:'活動／安靜模式正使用較低採樣上限，不代表 GPU 忙碌',within_budget:'目前樣本在設定預算內',not_measured:'近期沒有足夠有效樣本'};
      const rows=[['實際運算裝置',ai.backend||ai.device],
        ['GPU 型號',adaptive.gpu_sampling?.gpu_model??'未取得／不適用'],
        ['設定最高 FPS',adaptive.preferred_fps==null?'尚未取得':`${adaptive.preferred_fps} FPS`],
        ['目前降階後上限',`${adaptive.effective_input_size} px／${adaptive.effective_fps} FPS`],
        ['活動採樣／共同上限',`${adaptive.activity_sampling?.target_fps??'未量測'}／${adaptive.combined_sampling_fps_cap??adaptive.effective_fps} FPS`],
        ['實際分析速度',ai.fps==null?'尚未取得':`${ai.fps} FPS（最近 ${adaptive.fps_window_seconds||5} 秒完成量）`],
        ['溫度',adaptive.temperature_c==null?'無法取得':`${adaptive.temperature_c}°C`],
        ['目前 GPU 核心忙碌率（整卡）',adaptive.gpu_sampling?.gpu_kernel_busy_percent==null?'無法取得':`${adaptive.gpu_sampling.gpu_kernel_busy_percent.toFixed(1)}%`],
        ['AI 程序 CPU 用量',adaptive.load_scope==='ai_worker_cpu_normalized'&&adaptive.load_percent!=null?`${adaptive.load_percent.toFixed(1)}%（按邏輯核心容量正規化）`:'未量測／不適用'],
        ['GPU 專屬顯存（整卡）',adaptive.gpu_sampling?.dedicated_memory_used_mib==null||adaptive.gpu_sampling?.dedicated_memory_total_mib==null?'無法取得':`${mb(adaptive.gpu_sampling.dedicated_memory_used_mib)}／${mb(adaptive.gpu_sampling.dedicated_memory_total_mib)}（可用 ${mb(adaptive.gpu_sampling.memory_free_mib)}）`],
        ['GPU 記憶體控制器忙碌率（整卡）',adaptive.gpu_sampling?.memory_controller_busy_percent==null?'無法取得':`${adaptive.gpu_sampling.memory_controller_busy_percent.toFixed(1)}%`],
        ['GPU 時脈（核心／記憶體）',`${adaptive.gpu_sampling?.graphics_clock_mhz?.toFixed(0)??'未量測'}／${adaptive.gpu_sampling?.memory_clock_mhz?.toFixed(0)??'未量測'} MHz`],
        ['GPU 功耗（整卡）',`${adaptive.gpu_sampling?.power_draw_w?.toFixed(1)??'未量測'}／${adaptive.gpu_sampling?.power_limit_w?.toFixed(1)??'未提供'} W`],
        ['NVIDIA 降頻原因',Array.isArray(adaptive.gpu_sampling?.throttle_active_reasons)?(adaptive.gpu_sampling.throttle_active_reasons.join('、')||'未偵測到已知降頻原因'):'無法取得'],
        ['最近單次耗時',adaptive.latency_ms==null?'未量測':`${adaptive.latency_ms.toFixed(1)} ms`],
        ['近期平均／P95',`${adaptive.latency_average_ms??'未量測'}／${adaptive.latency_p95_ms??'未量測'} ms`],
        ['目前每張預算',`${adaptive.frame_budget_ms??'未量測'} ms`],
        ['近期平均是否超時',adaptive.currently_over_budget===true?'近期平均高於預算':adaptive.currently_over_budget===false?'近期平均低於預算':'近期樣本不足或已逾時'],
        ['等待新影格／最新影格排隊',`${ai.frame_reader?.frame_wait_ms?.toFixed(1)??'未量測'}／${ai.frame_reader?.latest_frame_queue_wait_ms?.toFixed(1)??'未量測'} ms`],
        ['主要偵測佇列等待平均／P95',`${adaptive.host_scheduler_timings?.high_queue_wait_ms?.average_ms??'未量測'}／${adaptive.host_scheduler_timings?.high_queue_wait_ms?.p95_ms??'未量測'} ms`],
        ['最近 60 秒 GPU 忙碌率平均／最大',`${adaptive.gpu_sampling?.load_mean_60s_percent?.toFixed(1)??'未量測'}／${adaptive.gpu_sampling?.load_max_60s_percent?.toFixed(1)??'未量測'}%（${adaptive.gpu_sampling?.sample_count_60s??0} 個樣本）`],
        ['最近 60 秒 GPU 顯存平均／最高',`${mb(adaptive.gpu_sampling?.dedicated_memory_used_mean_60s_mib)}／${mb(adaptive.gpu_sampling?.dedicated_memory_used_max_60s_mib)}`],
        ['最近 60 秒溫度平均／最高',`${adaptive.gpu_sampling?.temperature_mean_60s_c?.toFixed(1)??'未量測'}／${adaptive.gpu_sampling?.temperature_max_60s_c?.toFixed(1)??'未量測'}°C`],
        ['GPU 採樣新鮮度／查詢耗時',adaptive.gpu_sampling?.status==='available'?`${adaptive.gpu_sampling.sample_age_seconds?.toFixed(1)??'未知'} 秒前／${adaptive.gpu_sampling.query_duration_ms?.toFixed(1)??'未知'} ms`:'資料目前無法取得'],
        ['GPU 負載範圍','整張 NVIDIA GPU，包含桌面與其他程式；不是 MeowKeeper+ 專屬使用率'],
        ['目前瓶頸判斷',bottlenecks[adaptive.bottleneck_status]||'尚未完成細分量測'],
        [adaptive.last_trigger?'最近降階原因（歷史）':'調整說明',adaptive.last_trigger?.reason_zh_tw||adaptive.reason_zh_tw||'使用已保存的設定']];
      const trigger=adaptive.last_trigger;
      if(trigger){rows.push(['觸發當時',`${trigger.previous_fps??'未知'} FPS／預算 ${trigger.frame_budget_ms??'未知'} ms；平均 ${trigger.latency_average_ms??'未知'} ms，P95 ${trigger.latency_p95_ms??'未知'} ms。不是目前仍超時的證據。`]);}
      const grid=document.createElement('dl');grid.className='ai-runtime-grid';
      for(const [label,value] of rows){const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value??'尚未取得';row.append(dt,dd);grid.append(row);}display.append(grid);
      const details=document.createElement('details'),summary=document.createElement('summary'),info=document.createElement('p');summary.textContent='GPU 時脈、功耗與各階段耗時';info.textContent=`AI 程序 ${adaptive.worker?.pid??'尚未啟動'}；端到端樣本 ${adaptive.latency_sample_count??'未知'} 筆。模型執行時間包含等待同步輸出，不是純 CUDA kernel 時間；RPC 差值包含傳輸、排程與序列化。各階段可能巢狀，不可相加成總時間。來源解碼／縮放若未提供會標示未量測。整卡數據包含其他程式；本介面不宣稱 per-process GPU 使用率。`;details.append(summary,info);
      const stageNames={
        media_frame_interarrival_ms:'來源影格到達間隔',latest_frame_callback_ms:'新影格回呼處理',
        frame_wait_ms:'等待下一張影格',latest_frame_queue_wait_ms:'最新影格佇列停留',
        high_queue_wait_ms:'主要偵測排隊等待',high_task_ms:'主要偵測排程工作',
        jpeg_decode_ms:'JPEG 解碼',model_call_wall_ms:'模型呼叫完整耗時',
        model_input_prepare_ms:'模型縮放／輸入準備',model_run_completed_output_ms:'模型執行至輸出同步',
        yolo_postprocess_ms:'YOLO 後處理',detection_result_adaptation_ms:'偵測結果整理',
        analysis_total_ms:'分析器單次總耗時',cpu_fallback_ms:'GPU 失敗後 CPU 重試',
        ultralytics_preprocess_reported_ms:'模型回報前處理（參考值）',
        ultralytics_inference_reported_ms:'模型回報推論（參考值）',
        ultralytics_postprocess_reported_ms:'模型回報後處理（參考值）',
        tracking_ms:'追蹤器',worker_tracking_and_crops_ms:'追蹤與裁切',
        worker_action_ms:'AI worker 單次工作',rpc_roundtrip_ms:'Host ↔ worker 往返',
        rpc_overhead_ms:'往返差額（傳輸／排程等）',ipc_send_ms:'IPC 傳送',
        v02_source_sanitize_ms:'來源資訊安全遮罩',v02_scene_registry_ms:'場景／區域讀取',
        v02_host_adapter_total_ms:'Host 分析器總耗時',v02_frame_pipeline_total_ms:'逐影格分析管線總耗時',
        v02_preflight_ms:'分析前檢查',v02_sampling_gate_ms:'採樣判斷',
        v02_yolo_scheduler_roundtrip_ms:'YOLO 排程往返',v02_track_history_feature_ms:'軌跡／行為特徵',
        v02_behavior_update_ms:'動作候選更新',v02_sqlite_persistence_ms:'SQLite 寫入',
        v02_identity_enqueue_ms:'身分工作排入',v02_feedback_crop_ms:'回饋影像裁切',
        v02_identity_result_apply_ms:'身分結果套用',v02_notification_enqueue_ms:'通知排入',
        v02_event_finalize_ms:'事件完成處理'
      };
      const timingGroups=[
        ['來源影格與佇列',ai.frame_reader?.timings],
        ['Host 排程器',adaptive.host_scheduler_timings],
        ['AI worker、模型與 IPC',adaptive.worker?.timings],
        ['Host 分析器',adaptive.host_adapter_stage_timings],
        ['V0.2 事件／身分／行為管線',adaptive.v02_pipeline_stage_timings]
      ];
      for(const [groupName,values] of timingGroups){
        const entries=Object.entries(values||{}).filter(([,value])=>value&&typeof value==='object'&&('average_ms' in value||'p95_ms' in value));
        if(!entries.length)continue;
        const heading=document.createElement('h4');heading.textContent=groupName;
        const timingTable=document.createElement('table'),timingBody=document.createElement('tbody');
        for(const [name,value] of entries){const tr=document.createElement('tr'),label=document.createElement('th'),avg=document.createElement('td'),p50=document.createElement('td'),p95=document.createElement('td'),count=document.createElement('td');label.textContent=stageNames[name]||name;avg.textContent=value.average_ms==null?'未量測':`${value.average_ms.toFixed(1)} ms`;p50.textContent=value.p50_ms==null?'未量測':`${value.p50_ms.toFixed(1)} ms`;p95.textContent=value.p95_ms==null?'未量測':`${value.p95_ms.toFixed(1)} ms`;count.textContent=`${value.samples??0} 筆`;tr.append(label,avg,p50,p95,count);timingBody.append(tr);}
        timingTable.append(timingBody);timingTable.setAttribute('aria-label',`${groupName}階段平均、P50 與 P95 耗時`);details.append(heading,timingTable);
      }
      const technical=document.createElement('pre');technical.textContent=JSON.stringify({gpu_sampling:adaptive.gpu_sampling??null,warmup_provider_nodes:adaptive.worker?.provider_node_counts??null,host_scheduler_metrics:adaptive.host_scheduler_queue_metrics??null,frame_reader_scope:ai.frame_reader?.scope??null,source_decode_resize_ms:ai.frame_reader?.source_decode_resize_ms??null},null,2);details.append(technical);display.append(details);
    } catch (_) { display.textContent = "目前無法讀取 AI 狀態；請確認登入與 Host。"; }
    finally { adaptiveLoading = false; }
  }
  document.getElementById("ai-adaptive-retry").addEventListener("click", async () => {
    try {
      const result = await request("/api/v1/inference/retry", {
        method: "POST", headers: { "X-MeowKeeper-CSRF": csrfToken() },
      });
      message.textContent = result.message_zh_tw;
    } catch (_) { message.textContent = "AI 重試未成功；請確認登入狀態。"; }
  });
  loadAdaptive();
  document.addEventListener('meowkeeper:status',event=>loadAdaptive(event.detail));
})();
