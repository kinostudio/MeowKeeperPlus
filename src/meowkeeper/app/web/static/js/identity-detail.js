(() => {
 const dialog=document.getElementById('identity-detail-dialog');if(!dialog)return;
 const root=document.getElementById('identity-detail-tracks'), message=document.getElementById('identity-detail-message');
 const reasons={insufficient_evidence:'獨立影格不足',unstable_votes:'跨影格結果不一致',small_margin:'候選太接近',low_similarity:'相似度不足',no_gallery:'尚無有效特徵',model_mismatch:'模型版本不相容',track_ambiguous:'軌跡可能混淆',calibration_required:'尚未通過家庭校準'};
 function text(parent,tag,value){const node=document.createElement(tag);node.textContent=value;parent.append(node);return node;}
 function time(value){const raw=typeof value==='string'&&/^\d{4}-\d{2}-\d{2} /.test(value)?value.replace(' ','T')+'Z':value;const date=new Date(raw);return Number.isFinite(date.getTime())?date.toLocaleString('zh-TW',{timeZone:'Asia/Taipei'}):'未提供';}
 function render(view){root.replaceChildren();view.tracks.forEach((track,index)=>{
   const card=document.createElement('article');card.className='card';root.append(card);
   text(card,'h4',`本次證據貓咪 ${index+1}：${track.decision==='unknown'?'身分尚未確認':'AI 實驗性候選'}`);
   text(card,'p',`影格時間：${time(track.captured_at)}｜計算時間：${time(track.computed_at)}（台北時間）`);
   text(card,'p',`有效影格 ${track.valid_frames||0}｜時間跨度 ${(track.evidence_seconds||0).toFixed(1)} 秒｜投票一致性 ${(track.vote_fraction||0).toFixed(3)}（不是機率）`);
   text(card,'p',`原因：${reasons[track.unknown_reason]||track.unknown_reason||'實驗性候選'}｜模型：${track.model_version}`);
   (track.human_reviews||[]).forEach((review,index)=>text(card,'p',`人工修訂 ${index+1}：${review.cat_presence==='not_cat'?'不是貓':(view.pets||[]).find(p=>p.pet_id===review.identity)?.name||'其他貓／尚未指認'}｜${review.learning_consent?'已同意裁切學習':'未同意裁切學習'}（不覆寫 AI 原始判定）`));
   text(card,'p',`特徵更新狀態：${({awaiting_learning_consent:'等待學習同意',unconfirmed_or_conflicting:'回報待確認或有衝突',eligible:'已通過特徵候選檢查',pending:'待背景檢查',crop_not_saved_or_expired:'裁切未保存或已清理'})[track.learning_status]||track.learning_status||'尚未檢查'}`);
   let learningBox;const crop=document.createElement('img');crop.src=track.crop_url;crop.alt='當次貓咪裁切';crop.style.maxWidth='100%';crop.style.maxHeight='220px';crop.onerror=()=>{crop.hidden=true;if(learningBox){learningBox.checked=false;learningBox.disabled=true;}text(card,'p','當次裁切未保存或已清理，請勿猜測回報。');};card.append(crop);
   (track.candidates||[]).forEach(candidate=>{
     text(card,'h4',`可能是 ${candidate.name}｜融合相似度 ${candidate.pet_score.toFixed(3)}`);
     (candidate.reference_matches||[]).forEach(reference=>{
       text(card,'p',`參考照視覺相似度 ${reference.cosine_score.toFixed(3)}`);
       if(reference.thumbnail_url){const image=document.createElement('img');image.src=reference.thumbnail_url;image.alt=`${candidate.name}近似參考照`;image.style.maxWidth='100%';image.style.maxHeight='180px';card.append(image);}
       if(reference.thumbnail_url){const compare=text(card,'button','並排比較照片');compare.type='button';compare.className='button secondary';compare.disabled=!track.crop_exists;compare.onclick=()=>{document.getElementById('identity-photo-current').src=track.crop_url;document.getElementById('identity-photo-reference').src=reference.thumbnail_url;document.getElementById('identity-photo-score').textContent=`${candidate.name}｜視覺相似度 ${reference.cosine_score.toFixed(3)}，不是準確率。`;document.getElementById('identity-photo-dialog').showModal();};}
     });
   });
   if(view.event_id){
     const select=document.createElement('select');select.setAttribute('aria-label','修正這條軌跡的貓咪身分');card.append(select);
     [['','選擇身分修正（可略過）'],...(view.pets||[]).map(p=>['pet:'+p.pet_id,p.name]),['other_cat','其他貓'],['not_cat','不是貓']].forEach(([value,label])=>{const option=document.createElement('option');option.value=value;option.textContent=label;select.append(option);});
     const label=document.createElement('label');learningBox=document.createElement('input');learningBox.type='checkbox';learningBox.checked=Boolean(track.crop_exists);label.append(learningBox,document.createTextNode('我同意將此清楚裁切用於身分特徵學習（不是重訓 YOLO）'));card.append(label);
     learningBox.disabled=!track.crop_exists;
     const save=text(card,'button','保存這條軌跡的修正');save.type='button';save.className='button secondary';
     save.onclick=async()=>{if(!select.value){message.textContent='請先選擇修正身分，或略過回報。';return;}save.disabled=true;
       try{const history=await read('/api/v1/events/'+encodeURIComponent(view.event_id)+'/review');const pick=select.value;const cookie=document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('meowkeeper_csrf='));
         const response=await fetch('/api/v1/events/'+encodeURIComponent(view.event_id)+'/review',{method:'POST',headers:{'Content-Type':'application/json','X-MeowKeeper-CSRF':cookie?decodeURIComponent(cookie.slice(16)):''},body:JSON.stringify({request_id:window.MeowKeeperRequestId(),expected_revision:history.revision,label:pick==='not_cat'?'not_cat':'cat',pets:pick.startsWith('pet:')?[{pet_id:pick.slice(4),behavior:null,motion:null}]:[],track_ref:track.behavior_event_id,use_for_learning:learningBox.checked&&pick.startsWith('pet:')})});
         const result=await response.json();if(!response.ok)throw new Error(response.status===409?'已有更新回報，請重新載入；歷史未覆寫。':result.detail||'軌跡修正未完成。');message.textContent='已保存修訂 '+result.revision+'；已同步提取並寫入回饋特徵庫。';
         try{render(await read('/api/v1/events/'+encodeURIComponent(view.event_id)+'/identity'));}catch{}
       }catch(error){message.textContent=error.message;}finally{save.disabled=false;}
     };
   }
 });if(!view.tracks.length)root.textContent='此事件尚無可追溯的身分證據；舊事件未分析不代表模型判定 unknown。';}
 async function read(path){const response=await fetch(path,{cache:'no-store'});if(!response.ok)throw new Error('無法讀取身分證據；請確認登入及事件是否存在。');return response.json();}
 document.addEventListener('meowkeeper:identity',async event=>{
   message.textContent='讀取中…';root.replaceChildren();dialog.showModal();const item=event.detail||{}, video=document.getElementById('identity-detail-video');video.pause();video.hidden=!item.clip_exists;
   if(item.clip_exists)video.src=`/api/v1/events/${encodeURIComponent(item.event_id)}/video?playback=true`;else video.removeAttribute('src');video.load();
   try{render(await read(item.event_id?`/api/v1/events/${encodeURIComponent(item.event_id)}/identity`:'/api/v1/identity/summary'));message.textContent='';}catch(error){message.textContent=error.message;}
 });
 dialog.addEventListener('close',()=>document.getElementById('identity-detail-video').pause());
 document.getElementById('identity-detail-close').onclick=()=>dialog.close();
 document.getElementById('identity-photo-close').onclick=()=>document.getElementById('identity-photo-dialog').close();
 const latest=document.getElementById('latest-identity');if(latest){read('/api/v1/identity/summary').then(view=>{
   latest.textContent=view.tracks.length?`最近 AI 證據：身分尚未確認｜${time(view.tracks[0].captured_at)}（台北時間；非即時畫面）`:'尚無已保存的 AI 身分證據。';
 }).catch(()=>latest.textContent='身分摘要暫時無法讀取。');}
})();
