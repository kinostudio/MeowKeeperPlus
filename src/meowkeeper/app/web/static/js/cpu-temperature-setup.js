/* Explicit user action only: page load never launches an installer. */
(() => {
  'use strict';
  const panel=document.getElementById('cpu-temperature-setup'),button=document.getElementById('cpu-setup-launch'),message=document.getElementById('cpu-setup-message');
  if(!panel||!button||!message)return;
  if(location.hash==='#cpu-temperature-setup')panel.open=true;
  const endpoint='/api/v1/telemetry/cpu-temperature/setup';
  const states={wizard_finished:'啟用工具已完成；請更新溫度狀態，取得真實讀值才算成功。',cancelled:'已取消啟用，監控繼續。',restart_required:'官方安裝器要求重新開機；請自行安排，程式不會自動重啟電腦。',failed:'啟用未完成，請在監控主機查看授權或官方安裝視窗；可稍後重試。',timeout:'啟用尚未確認完成；請先查看監控主機上的安裝視窗，勿重複啟動。',running:'啟用視窗已開啟，請在監控主機完成授權；尚未確認測溫成功。'};
  async function request(method,body){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
    try{
      const headers={};
      if(method==='POST'){
        headers['Content-Type']='application/json';
        headers['X-MeowKeeper-CSRF']=decodeURIComponent(document.cookie.split(';').map(v=>v.trim()).find(v=>v.startsWith('meowkeeper_csrf='))?.slice('meowkeeper_csrf='.length)||'');
      }
      const response=await fetch(endpoint,{method,headers,credentials:'same-origin',cache:'no-store',signal:controller.signal,...(body?{body:JSON.stringify(body)}:{})});
      const result=await response.json();if(!response.ok)throw new Error(result.detail||'目前無法讀取啟用工具狀態。');return result;
    }finally{clearTimeout(timer);}
  }
  async function refresh(){
    try{
      const data=await request('GET');button.disabled=!data.local_request||!data.launcher_available||['running','timeout'].includes(data.state);
      message.textContent=!data.local_request?'這是遠端連線；請到執行監控的電腦，以本機儀表板或程式內的一鍵工具啟用。':states[data.state]||data.reason_zh_tw||'按下按鈕後才會開啟授權視窗；你可以選擇取消。';
    }catch(error){button.disabled=true;message.textContent=error.name==='AbortError'?'啟用工具查詢逾時，未開啟安裝。':error.message;}
  }
  button.addEventListener('click',async()=>{
    if(!window.confirm('將在監控主機開啟官方 CPU 測溫啟用工具，可能下載 PawnIO 驅動並要求 Windows 管理員授權。Host 帳號不變，攝影機監控不中斷。是否開啟？'))return;
    button.disabled=true;
    try{const result=await request('POST',{confirm:true});message.textContent=result.message_zh_tw;}catch(error){message.textContent=error.name==='AbortError'?'啟用請求尚未確認，請先查看監控主機的視窗，再更新狀態，勿重複開啟。':error.message;}
  });
  panel.addEventListener('toggle',()=>{if(panel.open)refresh();});
  document.getElementById('cpu-temperature-refresh')?.addEventListener('click',refresh);
  refresh();
})();
