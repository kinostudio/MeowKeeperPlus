/* Home consumes the existing status poll and shared JPEG cache. */
(() => {
  "use strict";
  const byId = id => document.getElementById(id), image = byId("overview-frame");
  if (!image) return;
  let historyBusy = false, historyAt = 0, history = null;
  const number = (value, unit = "") => Number.isFinite(value) ? value.toFixed(1) + unit : "尚未確認";
  function text(id, value) {
    const element = byId(id);
    if (!element) return;
    if (element.hasAttribute("data-rolling-number") && window.MeowKeeperHardwareMotion) {
      window.MeowKeeperHardwareMotion.setValue(element, value);
    } else element.textContent = value;
  }
  document.addEventListener("meowkeeper:tailscale", event => {
    const status = event.detail?.status || {}, devices = event.detail?.devices || {};
    const online = Number.isInteger(devices.online_device_count) ? `${devices.online_device_count} 台在線` : "在線數未知";
    const active = Number.isInteger(devices.active_device_count) ? `${devices.active_device_count} 台有活動` : "活動數未知";
    const sessions = Number.isInteger(devices.dashboard_session_count) ? `${devices.dashboard_session_count} 個 Dashboard 工作階段` : "Dashboard 使用未知";
    const watching = Number.isInteger(devices.live_viewer_device_count) ? `${devices.live_viewer_device_count} 台已確認觀看` : "觀看數未知";
    const unknown = Number.isInteger(devices.unidentified_live_viewer_connections) && devices.unidentified_live_viewer_connections > 0
      ? `；${devices.unidentified_live_viewer_connections} 條未辨識` : "";
    const state = status.logged_in ? "已登入" : status.status === "not_installed" ? "未安裝" : "未確認";
    text("overview-tail-state", `${state}｜在線 ${online}｜活動 ${active}｜使用 ${sessions}｜觀看 ${watching}${unknown}`);
  });
  const previewService=window.MeowKeeperPreview.mount({image,note:byId("overview-frame-note"),scope:"overview",defaultMode:"energy",
    onMetadata:data=>{
      window.MeowKeeperMonitorUI?.preview(data);
      text("overview-source-spec",`預覽：${data?.resolution || "尚未取得"}｜更新 ${number(data?.fps," FPS")}｜共用單一來源`);
      text("overview-frame-tag",data?.resolution ? `${data.resolution}｜預覽 ${number(data.fps," FPS")}` : "等待來源規格");
    },
    onFrame:({fresh})=>badge("overview-preview-badge",fresh?"LIVE · 本地快取":"影像時效未確認",fresh?"ok":"warn"),
    onUnavailable:()=>badge("overview-preview-badge","等待共享影格","warn")});
  function badge(id,label,kind="unknown") {const el=byId(id);if(el){el.textContent=label;el.className="status-pill status-"+kind;}}
  let systemAt=0,systemBusy=false,systemSnapshot=null;
  function renderCards(payload) {
    const m=payload.metrics||{},s=m.source||{},a=m.ai||{},t=m.telegram||{},r=m.ring_buffer||{},sys=m.system||{},app=m.application||{};
    const age=(Date.parse(payload.checked_at)-Date.parse(s.last_frame_at))/1000;
    const fresh=Number.isFinite(age)&&age>=0&&age<15&&s.connections>0;
    const enabled=["running","starting"].includes(payload.status);
    badge("source-status",fresh?"已接收最新影格":"等待有效影格",fresh?"ok":"warn");
    const mode=a.activity_mode||a.adaptive?.activity_sampling?.mode||(a.fps>=4?"active":a.fps<=1.5?"quiet":"normal");
    const modeLabel=mode==="active"?"活躍模式":mode==="quiet"?"待機節能":"一般模式";
    const modeKind=mode==="active"?"ok":mode==="quiet"?"warn":"info";
    const analyzing=fresh&&a.fps>0;
    badge("monitor-status",!enabled?"偵測已停止":analyzing?`正在分析 · ${modeLabel}`:"等待分析",!enabled?"unknown":analyzing?modeKind:"warn");
    badge("telegram-status",t.enabled?"通知已啟用":"通知未啟用",t.enabled?"info":"unknown");
    text("overview-source-name",`${s.source_id||"來源尚未確認"} · 連線 ${s.connections??"未確認"}`);
    text("overview-frame-age",Number.isFinite(age)&&age>=0?number(age," 秒前"):"尚未取得");
    text("overview-frame-time",s.last_frame_at?new Date(s.last_frame_at).toLocaleTimeString("zh-TW"):"尚未取得");
    text("overview-bitrate",Number.isFinite(s.bitrate_bps)?number(s.bitrate_bps/1000000," Mbps"):Number.isFinite(s.estimated_bitrate_bps)?`約 ${number(s.estimated_bitrate_bps/1000000," Mbps")}（快取估算）`:"等待影音分段採樣");
    const rate=document.getElementById("overview-bitrate");
    if(rate)rate.title="以近期完整壓縮影音分段大小／時長估算，包含容器及可能的轉碼；不是整台電腦上下傳，也不等於攝影機網路線上精確速率。";
    text("overview-ai-model",`${a.model||"尚未確認"} · ${a.inference_input_size||"未確認"} px`);
    const gallery=m.identity_gallery||{};
    let identityText="尚未確認";
    if(Number.isInteger(gallery.pet_count)){
      if(gallery.pet_count===0){
        identityText="尚未登錄貓咪檔案";
      }else if(gallery.feedback_feature_count>0){
        identityText=`已校準（${gallery.pet_count} 隻貓 · ${gallery.feedback_feature_count} 筆家庭驗收特徵）`;
      }else{
        identityText=`已登錄 ${gallery.pet_count} 隻貓 · 待家庭樣本校準`;
      }
    }else{
      identityText="身分候選 · 待確認";
    }
    text("overview-identity",identityText);
    const identityEl=document.getElementById("overview-identity");
    if(identityEl&&gallery&&gallery.pets){
      const parts=Object.values(gallery.pets).map(p=>`${p.name}：${p.feedback_count} 筆家庭驗收特徵`);
      if(parts.length)identityEl.title=parts.join("；");
    }
    text("overview-queue",Number.isFinite(t.queue_pending)?`${t.queue_pending} 件`:"尚未提供");
    text("overview-recipient-count",Number.isFinite(t.recipient_count)?`${t.recipient_count} 位` : "尚未提供");
    text("overview-ring-tag",`環形快取 ${number(r.seconds," 秒")} · ${r.segment_count??"未確認"} 段`);
    const transCount=a.adaptive?.activity_sampling?.active_transitions;
    const transText=Number.isInteger(transCount)&&transCount>0?` · 累計活躍 ${transCount} 次`:"";
    const detailMode=mode==="active"?`⚡ 活躍高頻採樣（5 FPS · 偵測到動態）${transText}`:mode==="quiet"?`🌙 待機節能巡檢（1 FPS · 畫面無貓咪動態）${transText}`:`🐾 一般常態採樣（3 FPS · 貓咪在鏡頭前）${transText}`;
    const calNote=(gallery&&gallery.feedback_feature_count>0)
      ?`特徵庫已收錄 ${gallery.feedback_feature_count} 筆家庭實拍驗收樣本。`
      :"身分與行為屬實驗性。";
    text("monitor-detail",!enabled?"監控管線未執行；可從右上角啟動監控。":`${detailMode}。${calNote}`);
    text("overview-runtime-note",m.core?.last_error?"核心出現錯誤，請查看維護中心。":!enabled?"來源、AI 與錄影快取未執行；Dashboard 仍可使用。":fresh?"已收到最新影格；通知送達及事件錄影另依實際回條確認。":"正在等待有效新影格，不以程序啟動宣稱監控成功。");
    text("system-status",Number.isFinite(sys.cpu_percent)?number(sys.cpu_percent,"%") : "尚未採樣");
    const cpuStat=systemSnapshot?.telemetry?.statistics?.system_cpu_percent;
    if(cpuStat)text("system-detail",`1h 平均 ${number(cpuStat.average,"%")} · 峰值 ${number(cpuStat.peak,"%")}`);
    text("overview-cpu-state",Number.isFinite(sys.cpu_percent)?sys.cpu_percent<40?"負載低":"留意負載":"尚未採樣");
    text("overview-ram-state",Number.isFinite(sys.ram_percent)?sys.ram_percent<80?"尚有餘裕":"留意記憶體":"尚未採樣");
    text("application-status",`應用 RSS：${number(Number.isFinite(app.rss_bytes)?app.rss_bytes/1000000:NaN," MB")}`);
    const totalRam=systemSnapshot?.hardware?.ram_bytes;
    text("overview-ram-used",Number.isFinite(sys.ram_percent)?totalRam?`${number(totalRam*sys.ram_percent/100/1000000000)} / ${number(totalRam/1000000000," GB")}`:`${number(sys.ram_percent,"%")} RAM`:"尚未採樣");
    text("overview-local-host",location.host);
    text("overview-lan-state",payload.dashboard?.lan_access_enabled?"已啟用，需登入":"僅限本機");
    text("overview-network-state",payload.dashboard?.lan_access_enabled?"區網登入保護":"本機登入保護");
    const used=history?.latest?.total_disk_bytes ?? m.telemetry?.total_disk_bytes;
    if(Number.isFinite(used)){
      text("overview-storage-used",number(used/1000000000," GB"));
    }
    const total=payload.disk?.total_bytes||m.disk?.total_bytes;
    const storageMeter=byId("overview-storage-meter");
    if(storageMeter){
      storageMeter.hidden=!(Number.isFinite(used)&&Number.isFinite(total)&&total>0);
      if(!storageMeter.hidden)storageMeter.value=used/total*100;
    }
    loadSystem();
  }
  async function loadSystem() {
    if(systemBusy||Date.now()-systemAt<60000||document.hidden)return;
    systemBusy=true;systemAt=Date.now();
    try {
      const response=await fetch("/api/v1/system/status",{cache:"no-store",signal:AbortSignal.timeout(10000)});
      if(!response.ok)throw new Error("系統摘要不可用"); systemSnapshot=await response.json();
      const stat=systemSnapshot.telemetry?.statistics?.system_cpu_percent||{};
      text("system-detail",`1h 平均 ${number(stat.average,"%")} · 峰值 ${number(stat.peak,"%")}`);
      text("overview-cpu-state",stat.current==null?"尚未採樣":stat.current<40?"負載低":"留意負載");
      const ram=systemSnapshot.hardware?.ram_bytes;
      text("application-detail",ram?`系統 RAM 總容量 ${number(ram/1000000000," GB")}；應用與系統用量分開。`:"硬體容量尚未提供。");
    }catch(_){text("system-detail","一小時統計暫時無法讀取；即時數值仍獨立更新。");}
    finally{systemBusy=false;}
  }
  let recentEvents=[],eventTab="pending";
  function renderEvents() {
    const target=byId("events-list");target.replaceChildren();
    const items=eventTab==="pending"?recentEvents.filter(item=>!item.has_human_review):recentEvents;
    if(!items.length){target.textContent=eventTab==="pending"?"最近事件沒有待確認項目。":"尚無事件紀錄。";return;}
    for(const item of items){
      const card=document.createElement("article");card.className="overview-event-card";

      // Formatted Title
      let titleText = "發現貓咪";
      if (Array.isArray(item.predicted_names) && item.predicted_names.length === 1) {
        titleText = `發現 ${item.predicted_names[0]}`;
      } else if (Array.isArray(item.predicted_names) && item.predicted_names.length > 1) {
        titleText = `發現 ${item.predicted_names.join("、")}`;
      } else if (item.pet_id && item.pet_id !== "unknown") {
        titleText = `發現 ${item.pet_id}`;
      } else if (item.event_type && item.event_type !== "cat_detected") {
        titleText = "監控事件";
      }
      const timeStr = item.trigger_at ? new Date(item.trigger_at).toLocaleTimeString("zh-TW") : "時間未確認";
      const heading=document.createElement("strong");heading.textContent=`${titleText} · ${timeStr}`;card.append(heading);

      // Tag Badges
      const tagsBox = document.createElement("div"); tagsBox.className = "event-tags-container";
      if (item.has_local_ai) {
        const localTag = document.createElement("span"); localTag.className = "event-tag tag-local-ai";
        localTag.textContent = "🏷️ 本地辨識 (DINOv2)";
        tagsBox.appendChild(localTag);
      }
      if (item.has_cloud_ai) {
        const cloudTag = document.createElement("span"); cloudTag.className = "event-tag tag-cloud-ai";
        cloudTag.textContent = "🏷️ 雲端辨識";
        tagsBox.appendChild(cloudTag);
      }
      card.append(tagsBox);

      if(item.snapshot_exists){const photo=document.createElement("img");photo.src=`/api/v1/events/${encodeURIComponent(item.event_id)}/snapshot`;photo.alt="事件當下影格";photo.loading="lazy";card.append(photo);}

      let petDesc = "尚未確認";
      if (Array.isArray(item.predicted_names) && item.predicted_names.length) {
        petDesc = item.predicted_names.join("、");
      } else if (item.pet_id && item.pet_id !== "unknown") {
        petDesc = item.pet_id;
      }
      const scoreStr = item.confidence != null ? `${Math.round(item.confidence * 100)}%` : "未提供";
      const posture = item.posture_zh ? `｜姿態：${item.posture_zh}` : "";
      const detail=document.createElement("p");detail.textContent=`身分：${petDesc} (${scoreStr})${posture}｜影片 ${item.clip_exists?"已保存":"尚未完成"}`;card.append(detail);

      const button=document.createElement("button");button.className="button";button.textContent="檢視詳情與覆核 →";button.addEventListener("click",()=>document.dispatchEvent(new CustomEvent("meowkeeper:review",{detail:item})));card.append(button);target.append(card);
    }
  }
  document.addEventListener("meowkeeper:events",event=>{recentEvents=event.detail;renderEvents();});
  document.querySelectorAll("[data-event-tab]").forEach(button=>button.addEventListener("click",()=>{eventTab=button.dataset.eventTab;document.querySelectorAll("[data-event-tab]").forEach(b=>b.classList.toggle("active",b===button));renderEvents();}));
  byId("overview-full-status").addEventListener("click",()=>{byId("full-system-status").open=true;byId("full-system-status").scrollIntoView({block:"start"});});
  const fsBtn = byId("overview-fullscreen");
  if (fsBtn) {
    fsBtn.disabled = false;
    fsBtn.addEventListener("click", async () => {
      try { await byId("overview-preview")?.requestFullscreen(); }
      catch (_) { text("overview-frame-note", "此瀏覽器未提供全螢幕；可至即時畫面查看。"); }
    });
  }
  (image.closest("[data-monitor-view]")||document).querySelectorAll("[data-save-capture]").forEach(button=>button.addEventListener("click",async()=>{
    const buttons=document.querySelectorAll("[data-save-capture]");buttons.forEach(b=>b.disabled=true);text("capture-result","正在保存既有影音…");
    try{const cookie=document.cookie.split(";").map(s=>s.trim()).find(s=>s.startsWith("meowkeeper_csrf="));const response=await fetch("/api/v1/live/save",{method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":cookie?decodeURIComponent(cookie.split("=").slice(1).join("=")):""},body:JSON.stringify({kind:button.dataset.saveCapture})});const result=await response.json();text("capture-result",result.message_zh_tw||result.detail||"保存結果未確認");}
    catch(_){text("capture-result","保存結果未確認，請檢查 photo／video 資料夾，避免重複保存。");}finally{buttons.forEach(b=>b.disabled=false);}
  }));
  function chart(title, keys) {
    const section = document.createElement("figure"); section.className = "dashboard-chart";
    const heading = document.createElement("h4"); heading.textContent = title; section.append(heading);
    const canvas = document.createElement("canvas"); canvas.setAttribute("role", "img");
    canvas.setAttribute("aria-label", title + "；數值摘要列於下方。"); section.append(canvas);
    byId("overview-resource-charts").append(section);
    const width = Math.max(240, section.clientWidth-32), height = 180, dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = width*dpr; canvas.height = height*dpr;
    const ctx = canvas.getContext("2d"); ctx.scale(dpr,dpr); ctx.font = "11px sans-serif";
    const points = history.points || [], left = 35, right = width-8, bottom = 155, top = 15;
    for (let step=0; step<=4; step++) {
      const y = bottom-(bottom-top)*step/4;
      ctx.strokeStyle = "#E5DACD"; ctx.beginPath(); ctx.moveTo(left,y); ctx.lineTo(right,y); ctx.stroke();
      ctx.fillStyle = "#62544A"; ctx.fillText(String(step*25),0,y+3);
    }
    const end = points.at(-1)?.sampled_at || Date.now()/1000;
    const start = points[0]?.sampled_at || end-86400;
    const summary = document.createElement("figcaption");
    keys.forEach(({key,label,color},index) => {
      ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath(); let previous = null;
      ctx.setLineDash(index ? [5,3] : []);
      const values = [];
      points.forEach(point => {
        const value = point[key];
        if (!Number.isFinite(value)) { previous = null; return; }
        values.push(value);
        const x = left+(right-left)*(point.sampled_at-start)/Math.max(1,end-start);
        const y = bottom-(bottom-top)*Math.min(100,Math.max(0,value))/100;
        if (previous === null || point.sampled_at-previous > history.bucket_seconds*2) ctx.moveTo(x,y);
        else ctx.lineTo(x,y);
        ctx.arc(x,y,1.5,0,Math.PI*2); ctx.moveTo(x,y); previous = point.sampled_at;
      }); ctx.stroke();
      const row = document.createElement("div");
      row.textContent = `${index ? "虛線" : "實線"} · ${label}` + (values.length ? `：最新 ${number(history.latest?.[key],"%")}；分段均值 ${number(values.reduce((a,b)=>a+b,0)/values.length,"%")}；最高分段均值 ${number(Math.max(...values),"%")}` : "：尚無可用採樣");
      summary.append(row);
    });
    ctx.setLineDash([]); ctx.fillStyle="#62544A"; ctx.textAlign="left";
    ctx.fillText(new Date(start*1000).toLocaleTimeString("zh-TW",{hour:"2-digit",minute:"2-digit"}),left,height-5);
    ctx.textAlign="right"; ctx.fillText(new Date(end*1000).toLocaleTimeString("zh-TW",{hour:"2-digit",minute:"2-digit"}),right,height-5);
    const range=document.createElement("div"); range.textContent=points.length ? `實際資料：${new Date(start*1000).toLocaleString("zh-TW")}～${new Date(end*1000).toLocaleString("zh-TW")}` : "尚無真實採樣；這是待資料的座標區。"; summary.append(range);
    section.append(summary);
  }
  function renderHistory() {
    const target = byId("overview-resource-charts"); target.replaceChildren();
    const latest=history.latest||{};text("overview-storage-used",number(Number.isFinite(latest.total_disk_bytes)?latest.total_disk_bytes/1000000000:NaN," GB"));
    text("overview-storage-detail",latest.disk_sampled_at?`程式＋Data 邏輯占用 · ${new Date(latest.disk_sampled_at*1000).toLocaleTimeString("zh-TW")} 採樣`:"磁碟占用尚未採樣，不補造數值。");
    const spark=byId("overview-cpu-spark");spark.replaceChildren();
    const values=(history.points||[]).filter(p=>Number.isFinite(p.system_cpu_percent)).slice(-24);
    if(values.length){const ns="http://www.w3.org/2000/svg",svg=document.createElementNS(ns,"svg"),line=document.createElementNS(ns,"polyline");svg.setAttribute("viewBox","0 0 200 48");line.setAttribute("points",values.map((p,i)=>`${i*200/Math.max(1,values.length-1)},${46-p.system_cpu_percent*.44}`).join(" "));line.setAttribute("fill","none");line.setAttribute("stroke","#99451e");line.setAttribute("stroke-width","2");svg.append(line);spark.append(svg);}
    window.MeowKeeperResourceHistory = history;
    document.dispatchEvent(new CustomEvent("meowkeeper:resource-history", {detail: history}));
    if (!history?.points?.length) text("overview-history-note","尚無資源歷史；座標僅表示量尺，曲線需等真實採樣，不會補造過去數據。");
  }
  async function loadHistory() {
    if (historyBusy || Date.now()-historyAt < 60000 || document.hidden) return;
    historyBusy = true; historyAt = Date.now();
    try {
      const response = await fetch("/api/v1/analytics/resources?hours=24",{cache:"no-store",signal:AbortSignal.timeout(10000)});
      if (!response.ok) throw new Error("歷史暫時無法讀取");
      history = await response.json(); renderHistory();
      text("overview-history-note","背景約每分鐘採樣；圖表依時間彙整為約 "+((history.bucket_seconds || 60)/60).toFixed(1)+" 分鐘一點，缺資料保留空白。移動／觸控曲線，或聚焦後用方向鍵查看數值。"+(history.warning ? " "+history.warning : ""));
    } catch (_) { text("overview-history-note","資源歷史尚未可用；即時監控不受影響。"); byId("overview-resource-charts").textContent = "尚無可顯示的歷史曲線。"; }
    finally { historyBusy = false; }
  }
  let activityAt=0, activityBusy=false;
  async function loadActivity() {
    if(activityBusy || document.hidden || Date.now()-activityAt<60000) return;
    activityBusy=true; activityAt=Date.now();
    try {
      const response=await fetch("/api/v1/analytics/dashboard",{cache:"no-store",signal:AbortSignal.timeout(10000)});
      if(!response.ok) throw new Error("統計尚未可用");
      const data=await response.json(), target=byId("overview-event-chart"); target.replaceChildren();
      const days=Array.isArray(data.days)?data.days:[], all=days.flatMap(day=>day.hours||[]), max=Math.max(1,...all), total=all.reduce((sum,value)=>sum+value,0);
      target.classList.add("event-week-heatmap");target.setAttribute("role","img");target.setAttribute("aria-label",`最近七個完整日、每天24個小時區段，合計 ${total} 次模型貓咪事件。深色代表事件較多。`);
      const corner=document.createElement("span");corner.className="event-week-day";corner.textContent="日期／時段";target.append(corner);
      for(let hour=0;hour<24;hour++){const label=document.createElement("span");label.className="event-week-hour";label.textContent=hour%3===0?String(hour).padStart(2,"0"):"";label.title=`${hour}:00`;target.append(label);}
      let peak={count:0,date:"",hour:0};
      for(const day of days){
        const date=new Date(`${day.date}T00:00:00`), dayLabel=document.createElement("span");dayLabel.className="event-week-day";dayLabel.textContent=`${date.toLocaleDateString("zh-TW",{month:"numeric",day:"numeric"})} ${["日","一","二","三","四","五","六"][date.getDay()]}`;target.append(dayLabel);
        for(let hour=0;hour<24;hour++){
          const count=Number(day.hours?.[hour]||0),cell=document.createElement("span"),level=count?Math.min(4,Math.ceil(count/max*4)):0;
          cell.className="event-week-cell";cell.dataset.level=String(level);cell.setAttribute("aria-hidden","true");cell.title=`${day.date} ${String(hour).padStart(2,"0")}:00–${String((hour+1)%24).padStart(2,"0")}:00：${count} 次貓咪偵測事件`;
          if(count>peak.count)peak={count,date:day.date,hour};target.append(cell);
        }
      }
      const legend=document.createElement("div");legend.className="event-week-legend";legend.innerHTML='<span>較少</span><i class="event-week-cell" data-level="0"></i><i class="event-week-cell" data-level="1"></i><i class="event-week-cell" data-level="2"></i><i class="event-week-cell" data-level="3"></i><i class="event-week-cell" data-level="4"></i><span>較多</span>';target.append(legend);
      const peakText=peak.count?`｜熱點 ${peak.date} ${String(peak.hour).padStart(2,"0")}:00（${peak.count} 次）`:"｜目前沒有貓咪偵測事件";
      text("overview-event-total",`最近 7 日共 ${total} 次模型貓咪事件${peakText}`);
      const n=data.notifications, counts=byId("overview-delivery-counts"); counts.replaceChildren();
      badge("overview-delivery-badge",n.total?`${number(n.confirmed/n.total*100,"%")} 有送達回條`:"尚無投遞紀錄",n.total?"info":"unknown");
      text("overview-last-delivery",n.last_confirmed_at?new Date(n.last_confirmed_at).toLocaleTimeString("zh-TW"):"尚無回條");
      for(const [key,label] of [["confirmed","已確認送達"],["pending","等待傳送"],["failed","傳送失敗"],["suppressed","未啟用／靜音"]]) {
        const item=document.createElement("div"), value=document.createElement("strong"), name=document.createElement("span");
        value.textContent=String(n[key]); name.textContent=label; item.append(value,name); counts.append(item);
      }
      const meter=byId("overview-delivery-meter"); meter.replaceChildren(); const segment=document.createElement("span");
      segment.style.width=`${n.total?n.confirmed/n.total*100:0}%`; meter.append(segment);
      text("overview-activity-note",data.message_zh_tw+`｜同期間共 ${n.total} 個通知事件。`);
    } catch(_) {
      text("overview-event-total","事件統計暫時無法讀取；未補造數字。");
      text("overview-delivery-counts","通知回條尚未可用。");
    } finally { activityBusy=false; }
  }
  document.addEventListener("meowkeeper:status",event => {
    const metrics = event.detail.metrics || {}, source = metrics.source || {}, ai = metrics.ai || {}, disk = event.detail.disk || metrics.disk || {};
    text("overview-ai-fps",number(ai.fps," FPS")); text("overview-ai-device",ai.backend || "運算後端尚未確認");
    text("overview-disk-free",Number.isFinite(disk.free_bytes) ? number(disk.free_bytes/1000000000," GB") : "尚未確認");
    text("overview-source-spec",`預覽：${source.resolution || "尚未確認"}｜更新 ${number(source.fps," FPS")}；預覽／AI 可能另有縮放，不等於來源錄影規格。`);
    loadHistory(); loadActivity(); renderCards(event.detail);
  });
  let resizeTimer;
  const fullStatus = byId("full-system-status");
  const fieldNames = {host:"主機",dashboard:"Dashboard 連線",camera:"攝影機",ai:"AI",telegram:"Telegram",system:"系統用量",hardware:"硬體",telemetry:"最近一小時採樣",go2rtc:"影音橋接",sqlite:"事件資料庫",storage_free:"剩餘磁碟空間",privacy:"隱私狀態",status:"狀態",enabled:"啟用",cpu:"CPU",ram:"RAM",current:"目前",average:"平均",peak:"峰值",mean:"平均",max:"峰值",min:"最低",sample_count:"採樣數",unit:"單位",as_of:"資料時間",hostname:"電腦名稱",cpu_model:"CPU 型號",gpu_model:"GPU 型號",ram_total_bytes:"RAM 總位元組",uptime:"持續執行時間",resolution:"預覽解析度",fps:"FPS",connected:"已連線",device:"運算裝置",model:"模型",queue_pending:"待發送數",ring_buffer_seconds:"影音快取秒數"};
  Object.assign(fieldNames,{checked_at:"快照時間",healthy:"健康檢查通過",state:"服務狀態",pid:"程序 PID",started_at:"啟動時間",urls:"可用連線網址",lan_candidate_urls:"區網候選網址（未必開放）",lan_access_enabled:"允許區網存取",tailscale_url:"已驗證 Tailscale 網址",source_status:"來源狀態",source_type:"來源類型",configured:"已設定",last_frame_at:"最近影格時間",connections:"來源連線數",available:"後端可用（不等於已推論）",version:"版本",journal_mode:"SQLite 日誌模式",schema_version:"資料表版本",privacy_adapter_configured:"隱私控制已接線",verified:"已讀回確認",privacy_mode:"隱私模式",computer_name:"電腦名稱",logical_cores:"邏輯核心數",ram_bytes:"RAM 總容量",gpu_name:"GPU 型號",statistics:"近一小時使用率／溫度",disk_io:"近一小時磁碟 I/O",coverage_seconds:"採樣涵蓋秒數",system_cpu_percent:"CPU 使用率",system_ram_percent:"RAM 使用率",system_gpu_percent:"GPU 使用率（可辨識 NVIDIA）",cpu_temperature_c:"CPU 溫度",gpu_temperature_c:"GPU 溫度",read_bytes:"讀取量",write_bytes:"寫入量"});
  const stateNames={running:"運作中",starting:"啟動中",stopped:"已停止",connected:"已連線",reconnecting:"重新連線中",unknown:"尚未確認",disabled:"未啟用",ready:"已就緒",failed:"失敗",mock:"模擬測試",cpu:"CPU",wal:"WAL（寫入預先記錄）"};
  function fieldValue(value,key,path) {
    if (value == null) return "尚無資料";
    if (value === true) return "是"; if (value === false) return "否";
    if (Array.isArray(value)) return value.join("、") || "尚無資料";
    if (Number.isFinite(value)) {
      if (["storage_free","ram_bytes","read_bytes","write_bytes"].includes(key)) return number(value/1000000000," GB") + `（${value} 位元組）`;
      if (path.includes("_percent.")) return number(value,"%");
      if (path.includes("_temperature_c.")) return number(value,"°C");
      if (key === "coverage_seconds") return number(value/60," 分鐘");
    }
    return stateNames[value] || String(value);
  }
  let fullBusy = false;
  fullStatus?.addEventListener("toggle",async () => {
    if (!fullStatus.open || fullBusy) return;
    fullBusy = true; text("full-system-note","正在讀取與 Telegram /status 同源的安全快照…");
    try {
      const response = await fetch("/api/v1/system/status",{cache:"no-store"});
      if (!response.ok) throw new Error("完整狀態暫時無法讀取");
      const data = await response.json(), target = byId("full-system-fields"); target.replaceChildren();
      function fields(value, path = "") {
        const table = document.createElement("table");
        Object.entries(value).forEach(([key,item]) => {
          const row = document.createElement("tr"), label = document.createElement("th"), cell = document.createElement("td");
          label.textContent = fieldNames[key] || key; label.scope = "row";
          if (item && typeof item === "object" && !Array.isArray(item)) cell.append(fields(item,path+key+"."));
          else cell.textContent = fieldValue(item,key,path);
          row.append(label,cell); table.append(row);
        }); return table;
      }
      target.append(fields(data)); text("full-system-note","此處完整呈現 /status 的同一份快照；採樣不足與無溫度感測器時顯示尚無資料。");
    } catch (_) { text("full-system-note","完整系統狀態暫時無法讀取；請確認 Host 版本與登入狀態。"); }
    finally { fullBusy = false; }
  });
  window.addEventListener("resize",() => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderHistory,150); });
    async function loadTailscaleDirect() {
    try {
      const resp = await fetch("/api/v1/tailscale/status", {cache: "no-store"});
      if (resp.ok) {
        const data = await resp.json(), devices = data.device_summary || {};
        const online = Number.isInteger(devices.online_device_count) ? `${devices.online_device_count} 台在線` : "在線數未知";
        const active = Number.isInteger(devices.active_device_count) ? `${devices.active_device_count} 台有活動` : "活動數未知";
        const sessions = Number.isInteger(devices.dashboard_session_count) ? `${devices.dashboard_session_count} 個 Dashboard 工作階段` : "Dashboard 使用未知";
        const watching = Number.isInteger(devices.live_viewer_device_count) ? `${devices.live_viewer_device_count} 台已確認觀看` : "觀看數未知";
        const unknown = Number.isInteger(devices.unidentified_live_viewer_connections) && devices.unidentified_live_viewer_connections > 0
          ? `；${devices.unidentified_live_viewer_connections} 條未辨識` : "";
        const state = data.logged_in ? "已登入" : data.status === "not_installed" ? "未安裝" : "未確認";
        text("overview-tail-state", `${state}｜在線 ${online}｜活動 ${active}｜使用 ${sessions}｜觀看 ${watching}${unknown}`);
      }
    } catch (_) {}
  }

  history={points:[],bucket_seconds:60}; renderHistory(); loadHistory(); loadActivity(); loadTailscaleDirect(); loadSystem();
})();
