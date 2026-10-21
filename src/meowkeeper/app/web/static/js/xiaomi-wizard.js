(() => {
  'use strict';
  const el=id=>document.getElementById(id); if(!el('xiaomi-devices'))return;
  let items=[],selected=null,job=null,loginPending=false,providerTimer=null,expiryTimer=null,busy=false,providerPolling=false;
  const csrf=()=>decodeURIComponent(document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('meowkeeper_csrf='))?.slice(16)||'');
  async function api(path,payload){
    const response=await fetch('/api/v1/'+path,{method:payload===undefined?'GET':'POST',cache:'no-store',headers:{'Content-Type':'application/json','X-MeowKeeper-CSRF':csrf()},body:payload===undefined?undefined:JSON.stringify(payload),signal:AbortSignal.timeout(path.endsWith('/firewall-diagnostic')?75000:60000)});
    let data={};try{data=await response.json();}catch(_){}if(!response.ok)throw new Error(data.detail||'操作未完成，請確認登入與連線。');if(path==='tailscale/devices')data.devices=data.summary?.devices||[];return data;
  }
  const note=text=>el('xiaomi-message').textContent=text;
  function badge(id,text,kind){el(id).textContent=text;el(id).className='status-pill status-'+kind;}
  function current(){return items.find(d=>d.internal_id===selected);}
  function mode(){return document.querySelector('[name="camera-mode"]:checked').value;}
  function updateSelection(){
    const d=current();el('xiaomi-selected-name').textContent=d?`目前選擇：${d.display_name}`:'請先選擇攝影機。';
    el('xiaomi-connect').disabled=!d||busy;el('xiaomi-test').disabled=!d||busy||mode()==='relay';el('xiaomi-firewall').disabled=!d||busy;
    el('camera-remote-pair').hidden=mode()!=='relay';el('camera-mode-help').textContent=mode()==='local'?'攝影機與這台電腦在同一區網時，使用直接連線即可。':'畫面會從已配對的電腦傳來；保存配對不表示目前正在使用中繼。';
    badge('xiaomi-pair-status',d?.remote_relay_paired?'✓ 已保存配對':'尚未配對',d?.remote_relay_paired?'ok':'unknown');
  }
  function select(id){selected=id;const preferred=current()?.preferred_connection_mode||'local';document.querySelector(`[name="camera-mode"][value="${preferred}"]`).checked=true;renderDevices();updateSelection();}
  function renderDevices(){
    const root=el('xiaomi-devices');root.replaceChildren();if(!items.length){root.textContent='尚未找到攝影機。請完成登入後更新清單。';return;}
    for(const d of items){
      const card=document.createElement('article');card.className='camera-device'+(d.internal_id===selected?' selected':'');
      const label=document.createElement('label'),radio=document.createElement('input');radio.type='radio';radio.name='xiaomi-device';radio.checked=d.internal_id===selected;radio.onchange=()=>select(d.internal_id);
      const title=document.createElement('strong');title.textContent=d.display_name;label.append(radio,title);
      const state=document.createElement('span');state.className='status-pill status-'+(d.monitoring_active?'ok':d.selected?'info':'unknown');state.textContent=d.monitoring_active?'● 監控中':d.selected?'◷ 等待新畫面':'可選擇';
      const q=document.createElement('p');q.className='field-help';const v=d.quality;q.textContent=v?.width?`${v.width} × ${v.height} · ${v.fps??'—'} FPS${v.historical?' · 上次驗證':''}`:'連線時會自動驗證畫面';
      const tech=document.createElement('details'),summary=document.createElement('summary');summary.textContent='技術資訊';const info=document.createElement('small');info.textContent=`型號 ${d.model} · 伺服器區域 ${d.region||'待確認'} · ${d.connection_mode==='remote_relay'?'目前使用遠端中繼':'本機接入'}${d.remote_relay_paired?' · 已保存中繼配對':''}`;tech.append(summary,info);card.append(label,state,q,tech);root.append(card);
    }
  }
  async function load(){
    const [status,devices]=await Promise.all([api('cameras/xiaomi/setup/status'),api('cameras/xiaomi/devices')]);items=devices.items||[];
    if(!items.some(d=>d.internal_id===selected))selected=items.find(d=>d.selected)?.internal_id||items[0]?.internal_id||null;
    const ready=['account_ready','connected','stream_ready','activating_source','device_selection_required'].includes(status.setup_stage)||items.length>0;
    badge('xiaomi-account-status',ready?'✓ 帳號已登入':'等待登入',ready?'ok':'warn');badge('xiaomi-stage',ready?'選擇攝影機後即可接入':'先登入小米帳號',ready?'info':'warn');
    el('xiaomi-login-diagnostic').textContent=JSON.stringify(status.login_diagnostic||{說明:'尚未取得安全診斷'},null,2);const retry=status.login_retry||{};
    el('xiaomi-cooldown').textContent=retry.cooldown_active?`請稍候約 ${retry.retry_after_seconds} 秒再嘗試登入。`:'不需要反覆登入；請先查看小米頁面的驗證提示。';
    if(selected){const p=current()?.preferred_connection_mode||'local';document.querySelector(`[name="camera-mode"][value="${p}"]`).checked=true;}renderDevices();updateSelection();
  }
  function runtime(data){
    const s=data.metrics?.source||{},stamp=Date.parse(s.last_frame_at||''),now=Date.parse(data.checked_at||''),fresh=s.status==='running'&&s.connections>0&&Number.isFinite(stamp)&&Number.isFinite(now)&&now-stamp<15000&&now-stamp>=-1000,d=items.find(x=>x.selected);
    badge('xiaomi-live-badge',fresh?'● 畫面正常':s.status==='error'?'! 連線中斷':s.status==='running'?'◷ 等待新畫面':'監控尚未啟動',fresh?'ok':s.status==='error'?'error':'warn');
    el('xiaomi-live-status').textContent=fresh?`正在接收「${d?.display_name||s.source_id||'攝影機'}」的即時畫面。`:s.status==='running'?'連線已建立，正在等待可供分析的新畫面。請至即時畫面確認影片播放狀態。':s.status==='error'?'暫時無法接收畫面，請檢查攝影機與所選連線方式。':'選擇攝影機並按「連線並開始監控」即可開始。';
  }
  document.addEventListener('meowkeeper:status',e=>runtime(e.detail));
  async function action(name,payload={}){try{const data=await api('cameras/xiaomi/setup/'+name,payload);note(data.message_zh_tw||'操作已完成。');await load();return data;}catch(e){note(e.message);return null;}}
  el('xiaomi-login').onclick=async()=>{
    const popup=window.open('about:blank','_blank');el('xiaomi-login').disabled=true;
    try{await api('cameras/xiaomi/setup/prepare',{});const data=await api('cameras/xiaomi/setup/open-login',{});
      if(data.login_url&&popup){const url=new URL(data.login_url);if(!['127.0.0.1','localhost'].includes(url.hostname)||url.protocol!=='http:')throw new Error('小米登入頁位址未通過檢查。');popup.location.href=url.href;loginPending=true;}else popup?.close();note(data.message_zh_tw||'請完成小米登入，再返回這個頁面。');
    }catch(e){popup?.close();note(e.message);}finally{el('xiaomi-login').disabled=false;}
  };
  el('xiaomi-prepare').onclick=()=>action('prepare');el('xiaomi-retry').onclick=()=>action('retry-login');el('xiaomi-diagnose').onclick=()=>action('capture-login-diagnostic');
  el('xiaomi-refresh').onclick=async()=>{el('xiaomi-refresh').disabled=true;note('正在尋找你的攝影機…');try{await action('refresh');}finally{el('xiaomi-refresh').disabled=false;}};
  window.addEventListener('focus',()=>{if(loginPending){loginPending=false;el('xiaomi-refresh').click();}});
  document.querySelectorAll('[name="camera-mode"]').forEach(x=>x.onchange=updateSelection);
  document.querySelectorAll('[data-camera-role]').forEach(b=>b.onclick=async()=>{
    const provider=b.dataset.cameraRole==='provider';document.querySelectorAll('[data-camera-role]').forEach(x=>{x.setAttribute('aria-pressed',String(x===b));x.classList.toggle('secondary',x!==b);});el('camera-monitor-options').hidden=provider;el('camera-provider-options').hidden=!provider;el('camera-connect-title').textContent=provider?'讓這台電腦提供中繼':'連線並開始監控';if(provider)await loadProvider();
  });
  el('xiaomi-pair').onclick=async()=>{if(!current())return;el('xiaomi-pair').disabled=true;try{const r=await api(`cameras/xiaomi/devices/${encodeURIComponent(selected)}/remote-pair`,{pairing_code:el('xiaomi-pair-code').value.trim()});note(r.message_zh_tw);if(r.success){current().remote_relay_paired=true;updateSelection();}}catch(e){note(e.message);}finally{el('xiaomi-pair-code').value='';el('xiaomi-pair').disabled=false;}};
  function renderFirewallResult(data){
    const panel=el('xiaomi-firewall-result-panel');
    const parent=el('xiaomi-firewall').closest('details');
    panel.hidden=false;panel.open=true;if(parent)parent.open=true;
    el('xiaomi-firewall-result-summary').textContent=data.message_zh_tw||'唯讀診斷已完成。';
    el('xiaomi-firewall-result').textContent=JSON.stringify(data,null,2);
  }
  async function deviceAction(name){
    if(!current())return;busy=true;updateSelection();
    if(name==='firewall-diagnostic')note('正在唯讀檢查連線、防火牆與攝影機待機狀態，請稍候…');
    try{
      const r=await api(`cameras/xiaomi/devices/${encodeURIComponent(selected)}/${name}`,{});
      note(r.message_zh_tw||'檢查已完成。');
      if(name==='firewall-diagnostic')renderFirewallResult(r);
      await load();
    }catch(e){
      note(e.message);
      if(name==='firewall-diagnostic')renderFirewallResult({status:'request_failed',message_zh_tw:e.message});
    }finally{busy=false;updateSelection();}
  }
  el('xiaomi-test').onclick=()=>deviceAction('probe');el('xiaomi-firewall').onclick=()=>deviceAction('firewall-diagnostic');
  el('xiaomi-connect').onclick=async()=>{
    if(!current()||busy)return;busy=true;updateSelection();try{job=await api(`cameras/xiaomi/devices/${encodeURIComponent(selected)}/connect`,{connection_mode:mode()});
      while(job.status==='running'){note(job.message_zh_tw);document.querySelectorAll('[data-connect-stage]').forEach(li=>li.classList.toggle('active',li.dataset.connectStage===job.stage||(job.stage==='waiting_frame'&&li.dataset.connectStage==='activating')));await new Promise(r=>setTimeout(r,750));job=await api(`cameras/xiaomi/setup/operations/${job.job_id}`);}
      await load();note(job.message_zh_tw);badge('xiaomi-stage',job.success?'✓ 已接入監控':'! 接入未完成',job.success?'ok':'error');
    }catch(e){note(e.message);}finally{busy=false;updateSelection();}
  };
  async function loadProvider(){try{const [p,ts]=await Promise.all([api('cameras/xiaomi/relay-provider'),api('tailscale/devices')]);el('relay-provider-status').textContent=p.message_zh_tw;const selector=el('relay-peer'),saved=selector.value;selector.replaceChildren(new Option('選擇允許連線的電腦',''));for(const d of ts.devices||[]){if(!d.is_self&&d.online===true)selector.add(new Option(d.alias||d.name||d.hostname||d.device_ref,d.device_ref));}selector.value=saved||p.peer_ref||'';}catch(e){el('relay-provider-status').textContent=e.message;}}
  el('relay-firewall').onclick=async()=>{try{const r=await api('cameras/xiaomi/relay-provider/firewall',{peer_ref:el('relay-peer').value});el('relay-provider-status').textContent=r.message_zh_tw;el('relay-admin-command').hidden=!r.administrator_command;el('relay-admin-command').textContent=r.administrator_command||'';}catch(e){el('relay-provider-status').textContent=e.message;}};
  el('relay-enable').onclick=async()=>{
    if(!current())return;el('relay-enable').disabled=true;
    try{const r=await api('cameras/xiaomi/relay-provider/enable',{device_id:selected,peer_ref:el('relay-peer').value});el('relay-provider-status').textContent=r.message_zh_tw||'正在準備中繼…';let attempts=0;clearInterval(providerTimer);
      providerTimer=setInterval(async()=>{if(providerPolling)return;if(++attempts>90){clearInterval(providerTimer);el('relay-enable').disabled=false;el('relay-provider-status').textContent='準備尚未完成，請檢查中繼狀態與診斷。';return;}providerPolling=true;try{
        const p=await api('cameras/xiaomi/relay-provider');el('relay-provider-status').textContent=p.message_zh_tw;if(p.busy)return;
        clearInterval(providerTimer);el('relay-enable').disabled=false;if(p.running){const code=await api('cameras/xiaomi/relay-provider/pairing',{});el('relay-code-output').hidden=false;el('relay-generated-code').value=code.pairing_code;let left=code.expires_in_seconds;clearInterval(expiryTimer);expiryTimer=setInterval(()=>{el('relay-expiry').textContent=`有效時間：${Math.max(0,left--)} 秒`;if(left<0){clearInterval(expiryTimer);el('relay-generated-code').value='';el('relay-code-output').hidden=true;}},1000);}
      }catch(e){clearInterval(providerTimer);el('relay-enable').disabled=false;el('relay-provider-status').textContent=e.message;}finally{providerPolling=false;}},1000);
    }catch(e){el('relay-enable').disabled=false;el('relay-provider-status').textContent=e.message;}
  };
  el('relay-stop').onclick=async()=>{try{const r=await api('cameras/xiaomi/relay-provider/stop',{});clearInterval(providerTimer);clearInterval(expiryTimer);el('relay-code-output').hidden=true;el('relay-generated-code').value='';el('relay-provider-status').textContent=r.message_zh_tw;}catch(e){note(e.message);}};
  el('relay-copy').onclick=async()=>{try{await navigator.clipboard.writeText(el('relay-generated-code').value);el('relay-expiry').textContent='已複製，請貼到另一台電腦。';}catch(_){el('relay-generated-code').select();el('relay-expiry').textContent='請複製已選取的配對碼。';}};
  load().then(()=>api('status').then(runtime)).catch(e=>note(e.message));
  window.addEventListener('pagehide',()=>{clearInterval(providerTimer);clearInterval(expiryTimer);});
})();
