(async function () {
  "use strict";
  const root = document.getElementById("events-page-list");
  if (!root) return;
  const notification = { sent: "已傳送", success: "已傳送", failed: "傳送失敗", pending: "等待傳送", queued: "等待傳送", disabled: "未啟用", skipped: "未傳送" };
  let page=0,busy=false;
  const playbackDialog=document.getElementById("event-playback-dialog"), playbackVideo=document.getElementById("event-playback-video");
  const playbackStatus=document.getElementById("event-playback-status"), playbackRetry=document.getElementById("event-playback-retry");
  let playbackItem=null, playbackMode="native", playbackTimer=0;
  function startPlayback(compatible=false) {
    if(!playbackItem||!playbackVideo)return;
    clearTimeout(playbackTimer);playbackMode=compatible?"compatible":"native";
    playbackRetry.hidden=true;
    playbackStatus.textContent=compatible?"正在準備此瀏覽器相容的影片；第一次可能需要一些時間，之後會沿用本機快取。":"正在開啟原始事件影片；若此瀏覽器不支援影片編碼，會改準備相容版本。";
    playbackVideo.src=`/api/v1/events/${encodeURIComponent(playbackItem.event_id)}/video?${compatible?"playback=true":"native=true"}`;
    playbackVideo.load();
    if(!compatible)playbackTimer=setTimeout(()=>{
      if(playbackDialog?.open&&playbackMode==="native"&&playbackVideo.readyState<2)startPlayback(true);
    },12000);
  }
  function closePlayback() {
    clearTimeout(playbackTimer);playbackVideo?.pause();playbackVideo?.removeAttribute("src");playbackVideo?.load();
    playbackItem=null;playbackMode="native";if(playbackDialog?.open)playbackDialog.close();
  }
  document.getElementById("event-playback-close")?.addEventListener("click",closePlayback);
  playbackDialog?.addEventListener("close",()=>{clearTimeout(playbackTimer);playbackVideo?.pause();playbackVideo?.removeAttribute("src");playbackVideo?.load();playbackItem=null;});
  playbackVideo?.addEventListener("canplay",()=>{clearTimeout(playbackTimer);playbackStatus.textContent=playbackMode==="compatible"?"相容影片已準備完成，可以播放。":"原始事件影片已載入，可以播放。";});
  playbackVideo?.addEventListener("error",()=>{
    if(playbackMode==="native"&&playbackItem){startPlayback(true);return;}
    playbackStatus.textContent="影片載入失敗；原始檔仍保留，請稍後重試。";playbackRetry.hidden=false;
  });
  playbackRetry?.addEventListener("click",()=>startPlayback(true));
  function openPlayback(item) {
    playbackItem=item;playbackMode="native";playbackVideo.pause();
    if(playbackDialog?.showModal)playbackDialog.showModal();else playbackDialog?.setAttribute("open","");
    startPlayback(false);
  }
  const previous=document.getElementById('events-previous'),next=document.getElementById('events-next'),filter=document.getElementById('events-kind-filter');
  async function loadPage() {
  if(busy)return;busy=true;if(previous)previous.disabled=true;if(next)next.disabled=true;
  try {
    const kind=filter?.value||'all';
    const response = await fetch(`/api/v1/events?limit=50&offset=${page*50}${kind==='all'?'':'&event_type='+encodeURIComponent(kind)}`,{cache:'no-store'});
    if (response.status === 401 || response.status === 403) {
      root.textContent = "登入已失效，請重新登入後查看事件。";
      const login = document.createElement("a"); login.href = "/login"; login.textContent = "前往登入"; login.className = "button secondary"; root.appendChild(login); return;
    }
    if (!response.ok) throw new Error();
    const data = await response.json();
    root.replaceChildren();
    const items=data.items||[];
    if(next)next.disabled=items.length<50;
    items.forEach((item) => {
      const row = document.createElement("article"); row.className = "event-row";
      const info = document.createElement("div");
      const title = document.createElement("strong"); title.textContent = item.event_type === "cat_detected" ? "發現貓咪" : "監控事件";
      const detail = document.createElement("small"); detail.style.display = "block";
      const date = new Date(item.trigger_at);
      const time = Number.isFinite(date.getTime()) ? date.toLocaleString("zh-TW") : "時間尚未提供";
      const pet = !item.pet_id || item.pet_id === "unknown" ? "尚未確認" : item.pet_id;
      const score = item.confidence == null ? "未提供" : `${Math.round(item.confidence * 100)}%（非準確率）`;
      const duration = item.actual_duration_seconds == null ? "未提供" : `${Number(item.actual_duration_seconds).toFixed(1)} 秒`;
      detail.textContent = `${time}｜寵物：${pet}｜模型分數：${score}｜影片：${duration}｜通知：${notification[item.telegram_status] || "狀態待確認"}`;
      info.append(title, detail); row.appendChild(info);
      if (item.clip_exists) {
        const play = document.createElement("button"); play.type="button"; play.className = "button secondary"; play.textContent = "播放事件影片";
        play.addEventListener("click",()=>openPlayback(item));row.appendChild(play);
      } else {
        const missing = document.createElement("small"); missing.textContent = "目前沒有可播放的影片"; row.appendChild(missing);
      }
      if (item.event_type === "cat_detected" && item.trigger_origin === "detector") {
        const review = document.createElement("button"); review.type = "button";
        review.className = "button secondary"; review.textContent = "確認貓咪／回報行為";
        review.addEventListener("click",() => document.dispatchEvent(new CustomEvent("meowkeeper:review",{detail:item})));
        row.append(review);
      }
      root.appendChild(row);
      const identity=document.createElement('button');identity.type='button';identity.className='button secondary';identity.textContent='查看 AI 身分比對';identity.onclick=()=>document.dispatchEvent(new CustomEvent('meowkeeper:identity',{detail:item}));row.append(identity);
    });
    if (!root.children.length) root.textContent = "這一頁沒有符合選項的事件。可切換顯示條件或頁數。";
    root.scrollTop=0;
    const label=document.getElementById('events-page-number');if(label)label.textContent=`第 ${page+1} 頁`;
  } catch (_) {
    root.textContent = "目前無法讀取事件。請確認服務仍在執行，再重新整理頁面；這不代表先前的紀錄已遺失。";
    if(next)next.disabled=true;
  }
  finally{busy=false;if(previous)previous.disabled=page===0;}
  }
  if(previous)previous.onclick=()=>{if(!busy&&page>0){page--;loadPage();}};
  if(next)next.onclick=()=>{if(!busy){page++;loadPage();}};
  if(filter)filter.onchange=()=>{page=0;loadPage();};
  await loadPage();
})();
