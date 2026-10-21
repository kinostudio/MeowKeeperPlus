(() => {
  "use strict";
  const byId=id=>document.getElementById(id), panel=byId("scene-registry-panel");
  if (!panel) return;
  let data=null, editing=null, image=null, drag=null, busy=false, referenceVersion=0,vertices=[],collageObjectUrl=null;
  const canvas=byId("scene-object-canvas"), context=canvas.getContext("2d"), message=value=>{byId("scene-message").textContent=value;};
  const csrf=()=>{const part=document.cookie.split(";").map(s=>s.trim()).find(s=>s.startsWith("meowkeeper_csrf="));return part ? decodeURIComponent(part.slice(16)) : "";};
  async function request(path,body) {
    const response=await fetch(path,{cache:"no-store",credentials:"same-origin",...(body ? {method:"POST",headers:{"Content-Type":"application/json","X-MeowKeeper-CSRF":csrf()},body:JSON.stringify(body)} : {})});
    const value=await response.json();if (!response.ok) throw new Error(value.detail || "目前操作未完成。");return value;
  }
  function bounds() {return ["x","y","width","height"].map(key=>Number(byId(`scene-object-${key}`).value)/100);}
  function setBounds(values) {["x","y","width","height"].forEach((key,index)=>{byId(`scene-object-${key}`).value=(values[index]*100).toFixed(1);});draw();}
  function clearSelection() {drag=null;vertices=[];setBounds([0,0,0,0]);}
  function draw() {
    context.clearRect(0,0,canvas.width,canvas.height);if (!image) return;
    context.drawImage(image,0,0,canvas.width,canvas.height);
    if(byId("scene-shape").value==="polygon" && vertices.length){context.beginPath();vertices.forEach(([x,y],index)=>index ? context.lineTo(x*canvas.width,y*canvas.height) : context.moveTo(x*canvas.width,y*canvas.height));context.closePath();context.fillStyle="#a84f2035";context.fill();context.strokeStyle="#a84f20";context.lineWidth=3;context.stroke();byId("scene-selection-status").textContent=`已選${vertices.length}點；至少3點後可保存，系統自動連回起點。`;return;}
    const [x,y,w,h]=bounds();context.strokeStyle="#a84f20";context.lineWidth=3;
    if(w>0 && h>0){context.fillStyle="#a84f2025";context.fillRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);context.strokeRect(x*canvas.width,y*canvas.height,w*canvas.width,h*canvas.height);}
    byId("scene-selection-status").textContent=w>0 && h>0 ? "已選好一個範圍，可命名保存；拖曳可重新畫框。" : "從物件的一角拖到另一角來畫框，不必填位置數字。";
  }
  function reference() {
    const view=byId("scene-view-select").value,version=++referenceVersion;image=null;context.clearRect(0,0,canvas.width,canvas.height);if (!view) {message("請先使用目前畫面或上傳參考圖。");return;}
    const next=new Image();next.onload=()=>{if(version!==referenceVersion)return;image=next;canvas.width=next.naturalWidth;canvas.height=next.naturalHeight;draw();};
    next.onerror=()=>{if(version===referenceVersion)message("參考圖暫時無法讀取，請確認仍在登入狀態。");};
    next.src=`/api/v1/scene-registry/views/${encodeURIComponent(view)}/reference`;
  }
  async function load(preferredView=null) {
    data=await request("/api/v1/scene-registry");const select=byId("scene-view-select");const chosen=preferredView || select.value;select.replaceChildren();
    const logical=byId("scene-logical-object"),previous=logical.value;logical.replaceChildren();const empty=document.createElement("option");empty.value="";empty.textContent="新增獨立物件";logical.append(empty);
    const linked=new Set();data.objects.forEach(object=>{const id=object.logical_object_id || object.object_id;if(linked.has(id))return;linked.add(id);const option=document.createElement("option");option.value=id;option.textContent=object.name;logical.append(option);});if(linked.has(previous))logical.value=previous;
    data.views.forEach(view=>{const option=document.createElement("option");option.value=view.view_id;option.textContent=view.name;select.append(option);});
    if (data.views.some(view=>view.view_id===chosen)) select.value=chosen;
    message(data.alignment.state==="aligned" ? "參考視角已登錄；執行分析時仍會檢查背景是否對位。" : "目前視角尚未校正或已失效；區域推論停用，找貓繼續。");
    const preview=byId("scene-current-preview");preview.src="/api/v1/preview/jpeg?ts="+Date.now();preview.hidden=false;
    const list=byId("scene-object-list");list.replaceChildren();
    data.objects.forEach(object=>{
      const row=document.createElement("p"), label=document.createElement("span"), edit=document.createElement("button"), disable=document.createElement("button");
      label.textContent=`${object.name} · 第 ${object.revision} 版 · ${object.enabled ? "啟用" : "停用"} `;
      const crop=document.createElement("img");crop.src=`/api/v1/scene-registry/objects/${encodeURIComponent(object.object_id)}/crop?revision=${object.revision}`;crop.alt=`${object.name}的已圈選參考圖`;crop.width=128;crop.loading="lazy";row.append(crop);
      edit.type=disable.type="button";edit.className=disable.className="button secondary";edit.textContent="編輯／重新定位";disable.textContent=object.enabled ? "停用" : "啟用";
      edit.addEventListener("click",()=>{editing=object;vertices=object.polygon.map(point=>[...point]);byId("scene-shape").value="polygon";select.value=object.view_id;logical.value=object.logical_object_id || object.object_id;byId("scene-object-name").value=object.name;byId("scene-object-kind").value=object.kind;
        const xs=object.polygon.map(p=>p[0]),ys=object.polygon.map(p=>p[1]);const values=[Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)];
        ["x","y","width","height"].forEach((key,index)=>{byId(`scene-object-${key}`).value=(values[index]*100).toFixed(1);});reference();});
      disable.addEventListener("click",()=>run(async()=>{await request("/api/v1/scene-registry/objects",{object_id:object.object_id,logical_object_id:object.logical_object_id || object.object_id,view_id:object.view_id,expected_revision:object.revision,name:object.name,kind:object.kind,polygon:object.polygon,enabled:!object.enabled});await load();}));
      row.append(label,edit,disable);list.append(row);
    });reference();
  }
  async function run(action) {if (busy) return;busy=true;const buttons=[...panel.querySelectorAll("button"),...byId("register-panel-views").querySelectorAll("button")];buttons.forEach(button=>{button.disabled=true;});try{await action();}catch(error){message(error.message);}finally{busy=false;buttons.forEach(button=>{button.disabled=false;});}}
  panel.addEventListener("toggle",()=>{if(panel.open && !data) run(load);});
  document.addEventListener("meowkeeper:scene-load",()=>{if(!data)run(load);});
  byId("scene-shape").addEventListener("change",clearSelection);
  byId("scene-views-reload").addEventListener("click",()=>run(()=>load()));
  byId("scene-view-select").addEventListener("change",()=>{editing=null;clearSelection();reference();});
  byId("scene-align-view").addEventListener("click",()=>run(async()=>{if(!data) return;await request(`/api/v1/scene-registry/views/${encodeURIComponent(byId("scene-view-select").value)}/align`,{expected_epoch:data.alignment.epoch});await load();}));
  byId("scene-save-view").addEventListener("click",()=>run(async()=>{if(!data) throw new Error("請先載入目前來源。");const result=await request("/api/v1/scene-registry/views",{source_ref:data.source_ref,name:byId("scene-view-name").value,expected_epoch:data.alignment.epoch,confirm_current_frame:byId("scene-frame-confirm").checked});byId("scene-frame-confirm").checked=false;editing=null;clearSelection();await load(result.view_id);}));
  byId("scene-upload-reference").addEventListener("click",()=>run(async()=>{
    if(!data)throw new Error("請先設定來源並載入。");const file=byId("scene-reference-file").files[0],name=byId("scene-view-name").value.trim();
    if(!name)throw new Error("請先替這張參考圖取一個名稱。");if(!file || !["image/jpeg","image/png","image/webp"].includes(file.type) || file.size>4*1024*1024)throw new Error("請選擇4MB內的單張JPG、PNG或WebP參考圖。");
    const form=new FormData();form.append("name",name);form.append("expected_epoch",String(data.alignment.epoch));form.append("image",file);
    const response=await fetch("/api/v1/scene-registry/views/upload",{method:"POST",credentials:"same-origin",headers:{"X-MeowKeeper-CSRF":csrf()},body:form}),result=await response.json();
    if(!response.ok)throw new Error(result.detail || "參考圖未保存。");editing=null;clearSelection();await load(result.view_id);byId("scene-reference-file").value="";message(result.message_zh_tw);
  }));
  byId("scene-new-object").addEventListener("click",()=>{editing=null;clearSelection();byId("scene-object-name").value="";byId("scene-logical-object").value="";message("正在新增物件；請在圖片上拖曳畫框，再命名保存。");});
  byId("scene-clear-selection").addEventListener("click",clearSelection);
  byId("scene-save-object").addEventListener("click",()=>run(async()=>{
    const [x,y,w,h]=bounds(),polygon=byId("scene-shape").value==="polygon" ? vertices : [[x,y],[x+w,y],[x+w,y+h],[x,y+h]];
    if(!image || polygon.length<3 || ![x,y,w,h].every(Number.isFinite) || x<0 || y<0 || w<=0 || h<=0 || x+w>1 || y+h>1)throw new Error("請在圖片上框出有效範圍；多邊形至少3點。");
    const saved=await request("/api/v1/scene-registry/objects",{object_id:editing?.object_id || null,...(byId("scene-logical-object").value ? {logical_object_id:byId("scene-logical-object").value} : {}),view_id:byId("scene-view-select").value,expected_revision:editing?.revision || 0,name:byId("scene-object-name").value,kind:byId("scene-object-kind").value,polygon,enabled:editing ? Boolean(editing.enabled) : true});
    await load();editing=data.objects.find(object=>object.object_id===saved.object_id) || null;byId("scene-logical-object").value=editing?.logical_object_id || editing?.object_id || "";
  }));
  byId("scene-preview-collage").addEventListener("click",async()=>{
    const figure=byId("scene-collage-figure"),preview=byId("scene-collage-preview");
    figure.hidden=false;preview.hidden=true;message("正在於本機整理已啟用的物件裁切…");
    try{
      const response=await fetch(`/api/v1/scene-registry/reference-collage?ts=${Date.now()}`,{credentials:"same-origin",cache:"no-store"});
      if(!response.ok){let detail="參考拼貼暫時無法產生。";try{detail=(await response.json()).detail || detail;}catch{};
        if(response.status===401)detail="登入已逾時；請重新登入後再預覽。";throw new Error(detail);}
      if(!(response.headers.get("content-type")||"").startsWith("image/jpeg"))throw new Error("服務回傳格式不正確，參考圖仍保存在本機。");
      const nextUrl=URL.createObjectURL(await response.blob()),count=response.headers.get("x-scene-reference-count")||"已登錄";
      preview.onload=()=>{if(collageObjectUrl)URL.revokeObjectURL(collageObjectUrl);collageObjectUrl=nextUrl;preview.hidden=false;message(`本機參考拼貼已完成（${count} 個物件）。不會因預覽而外送；只有在 AI 設定另行選擇並同意後，才會附於事件圖片。`);};
      preview.onerror=()=>{URL.revokeObjectURL(nextUrl);message("拼貼圖片無法顯示；參考資料仍保存在本機。");};
      preview.src=nextUrl;
    }catch(error){message(error.message || "參考拼貼暫時無法產生；原圖仍保存在本機。");}
  });
  const point=event=>{const rect=canvas.getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))];};
  canvas.addEventListener("pointerdown",event=>{if(!image || busy) return;if(byId("scene-shape").value==="polygon"){if(vertices.length>=64)return;vertices.push(point(event));const xs=vertices.map(p=>p[0]),ys=vertices.map(p=>p[1]);setBounds([Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)]);return;}vertices=[];drag=point(event);canvas.setPointerCapture(event.pointerId);setBounds([drag[0],drag[1],0,0]);});
  canvas.addEventListener("pointermove",event=>{if(!drag) return;const end=point(event);setBounds([Math.min(drag[0],end[0]),Math.min(drag[1],end[1]),Math.abs(drag[0]-end[0]),Math.abs(drag[1]-end[1])]);});
  canvas.addEventListener("pointerup",()=>{drag=null;});canvas.addEventListener("pointercancel",()=>{drag=null;});
  ["x","y","width","height"].forEach(key=>byId(`scene-object-${key}`).addEventListener("input",draw));
  canvas.addEventListener("keydown",event=>{
    if(event.key==="Escape"){event.preventDefault();clearSelection();return;}
    if(!image || !["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(event.key))return;event.preventDefault();event.stopPropagation();
    let [x,y,w,h]=bounds();if(!w || !h){w=.2;h=.2;}const delta=(event.key==="ArrowLeft" || event.key==="ArrowUp") ? -.01 : .01;
    if(event.shiftKey){if(["ArrowLeft","ArrowRight"].includes(event.key))w=Math.max(.01,Math.min(1-x,w+delta));else h=Math.max(.01,Math.min(1-y,h+delta));}
    else if(["ArrowLeft","ArrowRight"].includes(event.key))x=Math.max(0,Math.min(1-w,x+delta));else y=Math.max(0,Math.min(1-h,y+delta));setBounds([x,y,w,h]);
  });
  if(panel.open)run(load);
})();
