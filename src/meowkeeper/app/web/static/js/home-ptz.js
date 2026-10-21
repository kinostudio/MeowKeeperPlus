(() => {
  'use strict';
  const panel=document.getElementById('home-ptz');if(!panel)return;
  const message=document.getElementById('home-ptz-result'), buttons=panel.querySelectorAll('button');
  let device=null,busy=false;
  document.querySelector('a[href="#home-ptz"]')?.addEventListener('click',event=>{event.preventDefault();panel.open=!panel.open;});
  const csrf=()=>decodeURIComponent(document.cookie.split(';').map(s=>s.trim()).find(s=>s.startsWith('meowkeeper_csrf='))?.slice('meowkeeper_csrf='.length)||'');
  async function bind(){
    buttons.forEach(b=>b.disabled=true);device=null;
    const response=await fetch('/api/v1/cameras/xiaomi/devices',{cache:'no-store',signal:AbortSignal.timeout(5000)});
    if(!response.ok)throw new Error('無法讀取已接入攝影機，請確認登入。');
    const data=await response.json(), active=data.items?.find(item=>item.selected);
    if(!active?.internal_id)throw new Error('目前沒有已配對的活動 Xiaomi 攝影機。');
    const stateResponse=await fetch(`/api/v1/cameras/xiaomi/devices/${encodeURIComponent(active.internal_id)}/ptz`,{cache:'no-store',signal:AbortSignal.timeout(5000)});
    const state=await stateResponse.json();
    if(!stateResponse.ok||state.ptz_available!==true)throw new Error(state.message_zh_tw||'目前方向控制不可用。');
    device=active.internal_id;message.textContent=`目前控制：${active.display_name||'已接入攝影機'}。方向操作可用，尚未送出移動。`;
    buttons.forEach(b=>b.disabled=false);
  }
  async function send(id,payload){
    const response=await fetch(`/api/v1/cameras/xiaomi/devices/${encodeURIComponent(id)}/ptz`,{method:'POST',headers:{'Content-Type':'application/json','X-MeowKeeper-CSRF':csrf()},body:JSON.stringify(payload),signal:AbortSignal.timeout(4000)});
    const result=await response.json();if(!response.ok||result.success!==true)throw new Error(result.message_zh_tw||result.detail||'控制未確認。');return result;
  }
  panel.addEventListener('toggle',()=>{if(panel.open&&!busy)bind().catch(error=>message.textContent=error.message);});
  buttons.forEach(button=>button.addEventListener('click',async()=>{
    if(busy)return;busy=true;let target=null;
    try{
      await bind();target=device;buttons.forEach(b=>b.disabled=true);
      const direction=button.dataset.homeDirection;
      const result=await send(target,direction?{action:'move',direction}:{action:'stop'});
      message.textContent=result.message_zh_tw||'操作已確認送出；請以畫面確認實際移動。';
    }catch(error){message.textContent=`操作未完成：${error.message}`;}
    finally{
      if(target&&button.dataset.homeDirection){await new Promise(resolve=>setTimeout(resolve,300));try{await send(target,{action:'stop'});}catch(_){message.textContent='停止未確認；請按「停止移動」並檢查畫面。';}}
      busy=false;buttons.forEach(b=>b.disabled=!device);
    }
  }));
})();
