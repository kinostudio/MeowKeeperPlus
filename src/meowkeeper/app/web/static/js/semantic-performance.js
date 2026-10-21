(() => {
  "use strict";
  const button=document.getElementById("semantic-performance-load"), select=document.getElementById("semantic-performance-limit");
  const root=document.getElementById("semantic-performance-results"), message=document.getElementById("semantic-performance-message");
  if (!button || !select || !root || !message) return;
  const text=(parent,tag,value)=>{const node=document.createElement(tag);node.textContent=value;parent.appendChild(node);return node;};
  const percent=value=>typeof value === "number" && Number.isFinite(value) ? (value*100).toFixed(1)+"%" : "尚無完整分母，暫不計算";
  const seconds=value=>typeof value === "number" && Number.isFinite(value) ? (value/1000).toFixed(2)+" 秒" : "尚無可用紀錄";
  const labels={queue_ms:"進階工作排隊",preparation_ms:"選取／整理參考圖",api_response_ms:"API 等待回應（有網路證據）",validation_ms:"檢查候選答案",
                video_prepare_ms:"影片準備（含佇列與必要轉碼）",extra_wait_ms:"影片可發送後額外等 AI",telegram_upload_ms:"上傳 Telegram（單次嘗試）"};
  async function load() {
    button.disabled=true;root.replaceChildren();message.textContent="正在讀取已保存紀錄，不會送出新的 API 請求…";
    try {
      const hours=document.getElementById("semantic-performance-window")?.value;
      const start=document.getElementById("semantic-range-start").value,end=document.getElementById("semantic-range-end").value;
      if(Boolean(start)!==Boolean(end)) throw new Error("自訂時間需同時填開始與結束，或兩者都清空。");
      const params=new URLSearchParams(start ? {start:new Date(start).toISOString(),end:new Date(end).toISOString()} : hours ? {hours} : {limit:select.value});
      [["provider","semantic-provider"],["model","semantic-model"],["validity","semantic-validity"],["input_mode","semantic-input-mode"]].forEach(([key,id])=>{const value=document.getElementById(id).value;if(value)params.set(key,value);});
      const response=await fetch("/api/v1/analytics/semantics?"+params,{cache:"no-store",credentials:"same-origin"});
      if (response.status === 401) throw new Error("請重新登入後查看處理統計。");
      if (!response.ok) throw new Error("目前無法讀取統計，請稍後再試；既有紀錄不會因此刪除。");
      const data=await response.json();
      [["provider_id","semantic-provider","全部服務"],["model_id","semantic-model","全部模型"]].forEach(([key,id,label])=>{
        const target=document.getElementById(id),chosen=target.value;target.replaceChildren();const all=document.createElement("option");all.value="";all.textContent=label;target.append(all);
        const values=new Set(data.filter_options?.[key] || []);if(chosen)values.add(chosen);
        values.forEach(value=>{const option=document.createElement("option");option.value=value;option.textContent=value==="unknown" ? "尚無來源證據" : value;target.append(option);});target.value=chosen;
      });
      message.textContent=data.event_count ? `${data.window_hours ? `近 ${data.window_hours} 小時區間` : "最近事件"} ${data.event_count} 筆模型貓咪事件｜${data.analysis_count} 筆已保存分析｜${data.delivery_trace_count} 次通知處理` : "尚無模型貓咪事件；開始監控後，實際保存的紀錄才會出現在這裡。";
      text(root,"p",data.message_zh_tw);
      if(data.filters_applied)text(root,"p",`範圍內事件 ${data.event_count} 筆；符合比較條件且有分析紀錄 ${data.matched_event_count} 筆。通知分母只採可連回這些分析工作的紀錄，不能當成全部通知成功率。`).className="notice";
      if (data.truncated || data.invalid_record_count) text(root,"p",`資料不完整：截斷 ${data.truncated ? "是" : "否"}，無法驗證 ${data.invalid_record_count} 筆；暫不提供成功百分比。`).className="notice";
      text(root,"h3","API 與通知分母");
      text(root,"p",`可確認已送出 API ${data.confirmed_request_count} 次；未知送出證據 ${data.unknown_request_count} 次；確認未送出 ${data.not_dispatched_count} 次；略過 ${data.skipped_count} 次（可能包含在未送出中，不能相加）。`);
      text(root,"p",`已確認請求中的有效答案 ${data.valid_success_count} 次｜成功率：${percent(data.api_success_rate)}`);
      text(root,"p",`AI 候選文案 ${data.ai_caption_count} 次；本機文案 ${data.local_caption_count} 次。有效候選可對應文案 ${data.candidate_caption_linked_count} 次，其中未採用 ${data.candidate_caption_missed_count} 次：${percent(data.candidate_caption_miss_rate)}。`);
      text(root,"p",`可確認候選晚到 ${data.late_candidate_count} 次；缺少時刻 ${data.late_unknown_count} 次；尚無對應分析紀錄 ${data.unlinked_caption_count} 次。未採用不一定都是 API 慢，也可能是候選無法使用。`);
      text(root,"p",`Telegram 上傳嘗試 ${data.upload_attempt_count} 次，已接受 ${data.accepted_upload_count} 次：${percent(data.upload_acceptance_rate)}。省略明細 ${data.omitted_upload_count} 次。`);
      text(root,"h3","各步驟的平均與分布");
      text(root,"h3","依服務與模型分開看");
      (data.provider_models || []).forEach(group=> {
        text(root,"p",`${group.provider_id}／${group.model_id}：分析 ${group.analysis_count} 筆，已確認送出 ${group.confirmed_request_count} 次，未知 ${group.unknown_request_count} 次；成功率 ${percent(group.success_rate)}。回應平均 ${seconds(group.api_response.mean_ms)}、較慢情況 p95 ${seconds(group.api_response.p95_ms)}。`);
      });
      text(root,"h3","每日趨勢（UTC日期）");
      const trends=data.daily_trend || [];
      if(!trends.length)text(root,"p","此範圍沒有可用的每日分析紀錄。");
      trends.forEach(day=>{const row=text(root,"section","");text(row,"h4",day.date_utc);const bar=document.createElement("meter");bar.min=0;bar.max=Math.max(1,...trends.map(item=>item.analysis_count));bar.value=day.analysis_count;bar.setAttribute("aria-label",`${day.date_utc} 分析 ${day.analysis_count} 筆`);row.append(bar);text(row,"p",`${day.analysis_count} 筆分析；確認送出 ${day.confirmed_request_count} 次｜成功率 ${percent(day.success_rate)}｜回應平均 ${seconds(day.api_response.mean_ms)}、p95 ${seconds(day.api_response.p95_ms)}。`);});
      text(root,"h3","影像是否有效，與API是否可靠分開看");
      const validityLabels={valid:"人工確認有效",invalid:"人工確認無效",unclear:"看不清",conflict:"不同覆核有衝突",unreviewed:"尚未覆核"};
      Object.entries(data.quality_groups || {}).forEach(([state,group])=>text(root,"p",`${validityLabels[state] || "尚未確認"}：${group.analysis_count} 筆分析；其中成功請求的回應樣本 ${group.successful_response.sample_count} 筆、平均 ${seconds(group.successful_response.mean_ms)}、p95 ${seconds(group.successful_response.p95_ms)}。`));
      text(root,"p","標為無效只改變辨識品質分組，不扣掉已發生的API請求、失敗與等待。Token與費用目前沒有可靠來源，顯示未知，不當成免費或0元。").className="field-help";
      text(root,"h3","如果等 AI 0、5 或10秒，會有什麼差別？");
      text(root,"p","以下依已保存完成時間回放，不會重新呼叫AI或傳影片，也不是手機收到通知的時間保證。缺時刻的紀錄列為未知，不填成0。").className="field-help";
      (data.grace_replay || []).forEach(row=>text(root,"p",`等待上限 ${row.seconds} 秒：可回放 ${row.sample_count} 筆、未知 ${row.unknown_count} 筆，候選可及時使用 ${row.candidate_available_count} 筆；平均額外等待 ${seconds(row.extra_wait.mean_ms)}、p95 ${seconds(row.extra_wait.p95_ms)}。`));
      Object.entries(labels).forEach(([key,label])=> {
        const metric=data.metrics?.[key] || {}, card=text(root,"section","");
        text(card,"h4",label);
        text(card,"p",`可用樣本 ${metric.sample_count || 0} 筆｜平均 ${seconds(metric.mean_ms)}｜p50 ${seconds(metric.p50_ms)}｜p95 ${seconds(metric.p95_ms)}`);
      });
      text(root,"p","p50／p95 使用排序後的 nearest-rank 百分位；樣本少時不代表長期效能。影片準備含多個子步驟，不能再與其他時間相加。").className="field-help";
    } catch (error) { message.textContent=error.message || "統計暫時無法讀取，請稍後再試。"; }
    finally {button.disabled=false;}
  }
  button.addEventListener("click",load); load();
})();
