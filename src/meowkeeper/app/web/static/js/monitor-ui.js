/* Shared presentation. Consumes existing status/preview updates; never polls hardware. */
(() => {
  'use strict';
  const consumers=[];
  function mount(root) {
  const by=id=>root.querySelector(`#${id}`),ns='http://www.w3.org/2000/svg';
  const finite=value=>typeof value==='number'&&Number.isFinite(value);
  const number=(v,unit='')=>finite(v)?`${Math.round(v*10)/10}${unit}`:'尚未取得';
  const text=(node,value)=>{if(node&&node.textContent!==String(value))node.textContent=value;};
  const stage=root.querySelector('.preview-stage,.overview-preview');
  if(!stage)return;
  let status={},metadata={},original=null,originalKey=null,playback={},toastTimer=null,lastCaptureKind='photo';
  root.querySelectorAll('[data-save-capture]').forEach(button=>{button.textContent=button.dataset.saveCapture==='photo'?'截圖':'錄影（15秒）';});
  const result=document.getElementById('capture-result');
  if(result){
    const toast=document.createElement('div');toast.className='capture-toast';toast.hidden=true;
    toast.setAttribute('role','status');toast.setAttribute('aria-live','polite');toast.setAttribute('aria-atomic','true');stage.append(toast);
    result.classList.add('visually-hidden');result.setAttribute('aria-live','off');result.removeAttribute('role');
    root.querySelectorAll('[data-save-capture]').forEach(button=>button.addEventListener('click',()=>{lastCaptureKind=button.dataset.saveCapture==='video'?'video':'photo';},{capture:true}));
    new MutationObserver(()=>{
      if(root.hidden)return;
      const msg=result.textContent.trim();if(!msg)return;
      const pending=/正在|稍候/.test(msg),success=/已保存|已儲存/.test(msg),photo=lastCaptureKind==='photo';
      toast.dataset.kind=pending?'info':success?'ok':'error';
      text(toast,pending?(photo?'正在保存截圖…':'正在保存15秒影片…'):success?(photo?'截圖已保存':'影片已保存'):'保存未完成');
      toast.hidden=false;clearTimeout(toastTimer);
      toastTimer=setTimeout(()=>{toast.hidden=true;},pending?8000:success?5000:7000);
    }).observe(result,{childList:true,subtree:true,characterData:true});
  }
  const facts=document.createElement('div');facts.className='stream-facts';
  const groups=[['source','攝影機原始影像',['解析度','來源速度','編碼','驗證時間']],['play','目前播放',['播放方式','實際尺寸','更新速度','播放狀態','收音狀態']],['ai','AI 分析',['實際輸入','分析速度','設定上限','運算裝置','溫度／負載','調整原因']]];
  const values={};
  for(const [key,title,labels] of groups){const card=document.createElement('section'),heading=document.createElement('h4'),dl=document.createElement('dl');heading.textContent=title;card.append(heading,dl);for(const label of labels){const row=document.createElement('div'),dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent='尚未取得';row.append(dt,dd);dl.append(row);values[key+label]=dd;}facts.append(card);}
  const old=by('preview-metadata')||by('overview-source-spec');if(old){old.classList.add('visually-hidden');old.setAttribute('aria-live','off');if(by('overview-preview')){const detail=document.createElement('details'),summary=document.createElement('summary');detail.className='stream-facts-disclosure';summary.textContent='影像與 AI 規格';detail.append(summary,facts);old.after(detail);}else old.after(facts);}
  function renderFacts(){
    const a=status.metrics?.ai||{},adaptive=a.adaptive||{},t=status.metrics?.telemetry||{};
    const stale=metadata.checked_at&&Date.now()-Date.parse(metadata.checked_at)>15000;
    const video=stage.querySelector('video'),smooth=video&&!video.hidden&&video.videoWidth>0;
    const v=(key,value)=>text(values[key],value);
    v('source解析度',original?.width?`${original.width} × ${original.height}`:'尚未驗證');v('source來源速度',number(original?.fps,' FPS'));v('source編碼',original?.video_codec||'尚未取得');
    v('source驗證時間',original?.verified_at?`${original.historical?'上次驗證':'最近驗證'}：${new Date(original.verified_at).toLocaleString('zh-TW')}${original.historical?'（沿用記錄）':''}`:original?.historical?'已沿用上次驗證；時間未提供':'尚未提供驗證時間');
    v('play播放方式',playback.mode==='sleep'?'休眠':smooth?(playback.transport||'連續影片'):'節能 JPEG');v('play實際尺寸',smooth?`${video.videoWidth} × ${video.videoHeight}`:metadata.resolution||'尚未取得');
    v('play更新速度',stale?'資料已逾時':number(smooth?playback.fps:metadata.fps,' FPS'));v('play播放狀態',playback.reason|| (stale?'等待更新':playback.mode==='sleep'?'此頁已暫停播放':'依目前收到的畫面更新'));
    v('play收音狀態',!playback.audioRequested?'靜音（流暢影片可能仍含音軌）':playback.audioState==='no-audio-mode'?'此模式沒有聲音；收音偏好已保存':playback.audioAvailable===false?'來源未提供可播放音軌':playback.audioAvailable?'已收到音軌並解除靜音；請確認系統音量':'等待音軌，尚未確認收音');
    v('ai實際輸入',number(a.inference_input_size??metadata.ai_input_size,' px'));v('ai分析速度',number(a.fps??metadata.ai_analysis_fps,' FPS'));v('ai設定上限',`${number(adaptive.effective_input_size,' px')}／${number(adaptive.effective_fps,' FPS')}`);v('ai運算裝置',a.backend||a.device||'尚未取得');v('ai溫度／負載',`${number(adaptive.temperature_c??t.gpu_temperature_c,'°C')}／${number(adaptive.load_percent,'%')}`);v('ai調整原因',adaptive.reason_zh_tw||'依已保存的分析設定執行');
  }
  consumers.push({preview(data,source){metadata=data||{};const key=metadata.stream_name||metadata.source_id||"";if(key!==originalKey){original=null;originalKey=key;}const saved=metadata.original_source;if(saved&&Number.isInteger(saved.width)&&Number.isInteger(saved.height))original=saved;else if(source!==undefined)original=source;renderFacts();}});
  stage.addEventListener('meowkeeper:playback',e=>{playback=e.detail||{};renderFacts();});
  const home=by('overview-preview'),gauges=new Map();
  if(home){
    const hero=document.createElement('section');hero.className='monitor-hero';hero.setAttribute('aria-label','即時硬體與守護狀態');
    const hardware=document.createElement('section');hardware.className='panel hero-hardware';hardware.innerHTML='<div class="panel-heading"><div><p class="eyebrow">這台電腦，此刻的狀態</p><h3>即時硬體用量</h3></div><div class="hero-hardware-tools"></div></div><div class="hardware-rings"></div><div class="hardware-temperatures"></div><p id="hero-hardware-note" class="field-help">數值來自同一份背景採樣。</p>';
    // Move the existing clock rather than create another timer or hardware poll.
    const sampleClock=by('hardware-sample-clock');
    if(sampleClock)hardware.querySelector('.hero-hardware-tools').append(sampleClock);
    const primary=document.querySelector('.dashboard-primary'),preview=home.closest('.dashboard-preview-panel');primary.before(hero);hero.append(hardware,preview,primary);primary.classList.add('hero-services');
    const specs=[['cpu','CPU 使用率','system_cpu_percent'],['ram','RAM 使用率','system_ram_percent'],['gpu','GPU 使用率','system_gpu_percent'],['cpu-temp','CPU 溫度','cpu_temperature_c'],['gpu-temp','GPU 溫度','gpu_temperature_c']];
    for(const [id,label,key] of specs){
      const temp=id.includes('temp'),card=document.createElement('figure');card.className='hardware-gauge';card.dataset.metric=key;
      const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',temp?'0 0 120 86':'0 0 120 120');svg.setAttribute('aria-hidden','true');
      if(temp){svg.innerHTML='<path d="M15 65 A45 45 0 0 1 105 65" class="gauge-track"/><path d="M15 65 A45 45 0 0 1 105 65" class="gauge-temperature-arc"/><g class="gauge-needle"><path d="M60 65L23 65"/><circle cx="60" cy="65" r="4"/></g><text x="8" y="82">0°</text><text x="93" y="82">110°</text>';}
      else svg.innerHTML='<circle class="gauge-track" cx="60" cy="60" r="46"/><circle class="gauge-fill" cx="60" cy="60" r="46" stroke-dasharray="289.03" stroke-dashoffset="289.03"/>';
      const value=document.createElement('strong');value.className='gauge-value';value.textContent='—';const caption=document.createElement('figcaption');caption.textContent=label;const detail=document.createElement('small');detail.textContent='等待有效資料';card.append(svg,value,caption,detail);let setup=null;if(id==='cpu-temp'){setup=document.createElement('a');setup.href='/analytics#cpu-temperature-setup';setup.className='cpu-temperature-setup-link';setup.textContent='啟用／檢查測溫';card.append(setup);}hardware.querySelector(temp?'.hardware-temperatures':'.hardware-rings').append(card);gauges.set(key,{card,value,detail,svg,temp,setup});
    }
  }
  document.addEventListener('meowkeeper:status',event=>{
    status=event.detail||{};renderFacts();if(!home)return;const t=status.metrics?.telemetry||{},stamp=t.sampled_at,age=finite(stamp)?Date.now()/1000-stamp:Infinity,valid=age>=-1&&age<20;
    for(const g of gauges.values())if(g.setup)g.setup.hidden=valid&&finite(t.cpu_temperature_c);
    for(const [key,g] of gauges){const value=valid&&finite(t[key])?t[key]:null;g.card.dataset.available=String(value!==null);text(g.value,number(value,g.temp?'°C':'%'));if(g.temp){g.svg.querySelector('.gauge-needle').style.transform=`rotate(${Math.max(0,Math.min(110,value||0))/110*180}deg)`;}else g.svg.querySelector('.gauge-fill').style.strokeDashoffset=String(289.03*(1-Math.min(100,Math.max(0,value||0))/100));text(g.detail,!valid?'等待新採樣':value===null?(key==='cpu_temperature_c'?t.cpu_sensor?.message_zh_tw||'感測器尚未提供':'感測器尚未提供'):key==='system_ram_percent'?`${number(t.ram_used_bytes/1e9)} / ${number(t.ram_total_bytes/1e9,' GB')}`:key.includes('gpu')?t.gpu_name||'整張 GPU 的讀值':g.temp?t.cpu_sensor?.label||t.cpu_sensor?.source||'CPU 感測器直接讀值':'整台電腦的讀值');}
    const cpuTemperature=gauges.get('cpu_temperature_c');
    if(cpuTemperature){
      const sensor=t.cpu_sensor||{},model=sensor.hardware_name||t.cpu_model||status.hardware?.cpu_model||'CPU 型號尚未取得';
      const ready=valid&&finite(t.cpu_temperature_c)&&sensor.status==='available';
      const source=sensor.provider==='libre_hardware_monitor'?(ready?'由 LibreHardwareMonitor 監控':'LibreHardwareMonitor 尚未提供有效溫度'):sensor.provider==='psutil_cpu'?(ready?'由作業系統感測器監控':'作業系統尚未提供有效溫度'):'感測來源尚未確認';
      text(cpuTemperature.detail,model);cpuTemperature.detail.classList.add('cpu-model-label');cpuTemperature.detail.tabIndex=0;
      cpuTemperature.detail.dataset.sensorSource=source;cpuTemperature.detail.setAttribute('aria-label',`${model}；${source}`);
    }
  });
  }
  const roots=[...document.querySelectorAll('[data-monitor-view]')];
  (roots.length?roots:[document]).forEach(mount);
  window.MeowKeeperMonitorUI={preview(data,source){consumers.forEach(consumer=>consumer.preview(data,source));}};
})();
