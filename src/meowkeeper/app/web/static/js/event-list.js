(async function () {
  "use strict";
  const root = document.getElementById("events-page-list");
  if (!root) return;
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
  const pendingSection=document.getElementById("events-pending"), pendingList=document.getElementById("events-pending-list");
  const pendingCount=document.getElementById("events-pending-count"), petChips=document.getElementById("events-pet-chips");
  // Delivery status in the four-state language: ok / warn / alert / off.
  const delivery = {
    sent: ["ok", "已送達"], success: ["ok", "已送達"], failed: ["alert", "沒送出"],
    pending: ["warn", "等待傳送"], queued: ["warn", "等待傳送"], disabled: ["off", "未啟用"], skipped: ["off", "未傳送"],
  };
  let items=[], view="all", petFilter=null;
  const el=(tag, className, text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
  const sameDay=(a,b)=>a.getFullYear()===b.getFullYear()&&a.getMonth()===b.getMonth()&&a.getDate()===b.getDate();
  function names(item){
    if(Array.isArray(item.predicted_names)&&item.predicted_names.length)return item.predicted_names;
    if(item.pet_id&&item.pet_id!=="unknown")return [item.pet_id];
    return [];
  }
  function whenText(item){
    const date=new Date(item.trigger_at);
    if(!Number.isFinite(date.getTime()))return "時間未提供";
    const time=date.toLocaleTimeString("zh-TW",{hour:"numeric",minute:"2-digit"});
    return sameDay(date,new Date())?time:`${date.toLocaleDateString("zh-TW",{month:"numeric",day:"numeric"})} ${time}`;
  }
  function whatText(item){
    const who=names(item);
    const subject=item.event_type!=="cat_detected"?"監控事件":who.length?who.join("、"):"有貓，還認不出是誰";
    return item.posture_zh?`${subject}・${item.posture_zh}`:subject;
  }
  function needsReview(item){return item.event_type==="cat_detected"&&!item.has_human_review;}
  function thumb(item){
    const box=el("div","event-thumb");
    if(item.snapshot_exists){
      const img=el("img");img.loading="lazy";img.alt=`${whenText(item)} 事件畫面`;
      img.src=`/api/v1/events/${encodeURIComponent(item.event_id)}/snapshot`;
      img.addEventListener("error",()=>{img.remove();box.textContent="沒有畫面";},{once:true});
      box.appendChild(img);
    } else box.textContent="沒有畫面";
    if(item.actual_duration_seconds!=null)box.appendChild(el("span","event-thumb-duration",`${Number(item.actual_duration_seconds).toFixed(0)} 秒`));
    return box;
  }
  const review=(item)=>document.dispatchEvent(new CustomEvent("meowkeeper:review",{detail:item}));
  function row(item){
    const article=el("article","event-row");
    article.appendChild(thumb(item));
    article.appendChild(el("strong","event-when",whenText(item)));
    const what=el("div","event-what");
    what.appendChild(el("span","event-what-text",whatText(item)));
    const tags=el("span","event-tags-container");
    if(item.has_local_ai)tags.appendChild(el("span","event-tag tag-local-ai","本機辨識"));
    if(item.has_cloud_ai)tags.appendChild(el("span","event-tag tag-cloud-ai","雲端辨識"));
    if(item.has_human_review)tags.appendChild(el("span","event-tag tag-reviewed","你已確認"));
    if(item.confidence!=null)tags.appendChild(el("span","event-tag",`信心 ${Math.round(item.confidence*100)}%`));
    what.appendChild(tags);
    article.appendChild(what);
    const [state,label]=delivery[item.telegram_status]||["off","狀態待確認"];
    const chip=el("span","state-chip",label);chip.dataset.state=state;
    article.appendChild(chip);
    const actions=el("div","actions event-actions");
    if(item.clip_exists){
      const play=el("button","button secondary","播放");play.type="button";
      play.setAttribute("aria-label",`播放 ${whenText(item)} 的影片`);
      play.addEventListener("click",()=>openPlayback(item));
      const download=el("a","button secondary","下載");
      download.href=`/api/v1/events/${encodeURIComponent(item.event_id)}/video`;download.setAttribute("download","");
      download.setAttribute("aria-label",`下載 ${whenText(item)} 的影片`);
      actions.append(play,download);
    }
    const detail=el("button","button secondary",needsReview(item)?"確認":"詳情");detail.type="button";
    detail.setAttribute("aria-label",`${whenText(item)} 事件詳情與覆核`);
    detail.addEventListener("click",()=>review(item));
    actions.appendChild(detail);
    article.appendChild(actions);
    return article;
  }
  function pendingCard(item){
    const card=el("article","event-pending-card");
    card.appendChild(thumb(item));
    const body=el("div","event-pending-body");
    const who=names(item);
    body.appendChild(el("strong","",`${whenText(item)}・${who.length?`可能是 ${who.join("、")}`:"認不出是誰"}`));
    const go=el("button","button","看影片並確認");go.type="button";go.addEventListener("click",()=>review(item));
    body.appendChild(go);
    card.appendChild(body);
    return card;
  }
  function render(){
    const today=new Date();
    const pending=items.filter(needsReview);
    if(pendingCount)pendingCount.textContent=String(pending.length);
    if(pendingSection){pendingSection.hidden=!pending.length||view==="pending";pendingList.replaceChildren(...pending.slice(0,4).map(pendingCard));}
    // Per-cat chips from the cats seen on this page.
    if(petChips){
      const seen=[...new Set(items.flatMap(names))].slice(0,6);
      petChips.replaceChildren(...seen.map((name)=>{
        const chip=el("button","filter-chip",name);chip.type="button";chip.setAttribute("aria-pressed",String(petFilter===name));
        chip.addEventListener("click",()=>{petFilter=petFilter===name?null:name;render();});
        return chip;
      }));
    }
    document.querySelectorAll("[data-events-view]").forEach((chip)=>chip.setAttribute("aria-pressed",String(chip.dataset.eventsView===view)));
    const shown=items.filter((item)=>{
      if(view==="pending"&&!needsReview(item))return false;
      if(view==="today"){const date=new Date(item.trigger_at);if(!Number.isFinite(date.getTime())||!sameDay(date,today))return false;}
      if(petFilter&&!names(item).includes(petFilter))return false;
      return true;
    });
    root.replaceChildren(...shown.map(row));
    if(!shown.length)root.textContent=items.length?"這一頁沒有符合篩選的事件。可換個篩選或翻到其他頁。":"這一頁沒有事件。";
    root.scrollTop=0;
  }
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
    items=data.items||[];
    if(next)next.disabled=items.length<50;
    render();
    const label=document.getElementById('events-page-number');if(label)label.textContent=`第 ${page+1} 頁`;
  } catch (_) {
    root.textContent = "目前無法讀取事件。請確認服務仍在執行，再重新整理頁面；這不代表先前的紀錄已遺失。";
    if(next)next.disabled=true;
  }
  finally{busy=false;if(previous)previous.disabled=page===0;}
  }
  document.querySelectorAll("[data-events-view]").forEach((chip)=>chip.addEventListener("click",()=>{view=chip.dataset.eventsView;render();}));
  // A saved review changes "等你確認"; reload quietly when the review dialog closes.
  document.getElementById("event-review-dialog")?.addEventListener("close",()=>loadPage());
  if(previous)previous.onclick=()=>{if(!busy&&page>0){page--;loadPage();}};
  if(next)next.onclick=()=>{if(!busy){page++;loadPage();}};
  if(filter)filter.onchange=()=>{page=0;loadPage();};
  await loadPage();
})();
