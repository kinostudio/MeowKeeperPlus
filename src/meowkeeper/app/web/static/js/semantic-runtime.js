(() => {
  "use strict";
  const root=document.getElementById("semantic-runtime-jobs"),summary=document.getElementById("home-semantic-metrics"),refresh=document.getElementById("semantic-runtime-refresh");
  if(!root)return;
  const add=(tag,value,parent=root)=>{const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;};
  const sec=value=>typeof value==="number" ? (value/1000).toFixed(2)+"秒" : "尚無樣本";
  const reasons={memory_pressure:"記憶體餘裕不足",thermal_pressure:"運算溫度偏高",compute_pressure:"本機運算負載偏高",media_preparing:"正在組片或轉碼，先保護錄影"};
  async function load(){refresh.disabled=true;try{
    const responses=await Promise.all([fetch("/api/v1/vision/runtime",{cache:"no-store"}),fetch("/api/v1/analytics/semantics?hours=24",{cache:"no-store"})]);
    if(responses.some(response=>!response.ok))throw new Error("分析狀態暫時無法讀取，請確認登入與服務；本機監控不受此面板影響。");
    const [runtime,report]=await Promise.all(responses.map(response=>response.json()));root.replaceChildren();
    if(summary){summary.replaceChildren();add("p",`近24小時分析 ${report.analysis_count} 筆；確認外送 ${report.confirmed_request_count} 次、略過 ${report.skipped_count} 次、失敗／逾時 ${Object.entries(report.status_counts).filter(([key])=>["failed","timeout","auth_error","rate_limited","unavailable","bad_response"].includes(key)).reduce((sum,[,value])=>sum+value,0)} 次。`,summary);
      for(const state of ["valid","unreviewed"]){const group=report.quality_groups?.[state]?.successful_response;add("p",`${state==="valid" ? "人工確認有效影像" : "尚未覆核影像"}的成功回應：平均 ${sec(group?.mean_ms)}／p95 ${sec(group?.p95_ms)}；${group?.sample_count || 0}份樣本。`,summary);}
      add("p",`影片就緒後額外等待 p95：${sec(report.metrics.extra_wait_ms.p95_ms)}。費用／Token未知；沒有紀錄不算免費或成功。`,summary);
      if(report.truncated || report.invalid_record_count)add("p","資料不完整，請至分析頁查看；不以局部資料宣稱完整成功率。",summary);
    }
    add("p",`${runtime.worker_active ? "背景分析器已接通" : "背景分析器尚未啟動"}｜${runtime.pressure_reason ? reasons[runtime.pressure_reason] || "優先保護本機影音，這次略過可選分析" : "目前未回報負載退讓"}。`);
    if(!runtime.jobs.length)add("p","目前沒有等待或執行中的可選分析工作。");
    runtime.jobs.forEach(job=>{const row=add("section","");add("p",`${job.provider_id}／${job.model_id} · ${job.cancel_requested ? "取消已請求" : job.state==="queued" ? "等待分析" : "分析執行中"} · 排入時間 ${new Date(job.queued_at).toLocaleString("zh-TW")}`,row);
      const button=add("button","取消這筆可選分析",row);button.type="button";button.className="button secondary";button.disabled=Boolean(job.cancel_requested);
      button.addEventListener("click",async()=>{button.disabled=true;try{const csrf=document.cookie.split(";").map(part=>part.trim()).find(part=>part.startsWith("meowkeeper_csrf="));const response=await fetch(`/api/v1/events/${encodeURIComponent(job.event_id)}/analysis/${encodeURIComponent(job.job_id)}/cancel`,{method:"POST",headers:{"X-MeowKeeper-CSRF":csrf ? decodeURIComponent(csrf.slice(16)) : ""}});if(!response.ok)throw new Error();await load();}catch{add("p","取消未完成，請更新狀態；不會重新發送分析。",row);button.disabled=false;}});
    });
    add("p","只取消可選AI工作，不刪事件或停止錄影。已外送的請求不能撤回或保證免收費；依原網路逾時結束後會丟棄候選。這裡不持續輪詢，按更新才重讀。");
  }catch(error){root.textContent=error.message || "目前無法讀取工作狀態。";}finally{refresh.disabled=false;}}
  refresh.addEventListener("click",load);load();
})();
