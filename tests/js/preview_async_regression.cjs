const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.attrs={}; this.dataset={}; this.listeners={}; this.hidden=false; this.naturalWidth=640; this.naturalHeight=360; this.classList={contains:()=>false,add(){},remove(){},toggle(){}}; }
  append(...nodes) { this.children.push(...nodes); }
  setAttribute(k,v) { this.attrs[k]=v; }
  removeAttribute(k) { delete this.attrs[k]; if(k==='src') delete this.src; }
  addEventListener(k,v) { this.listeners[k]=v; }
  removeEventListener(k) { delete this.listeners[k]; }
  closest() { return this.parentElement; }
  contains(node) { return this.children.includes(node); }
  pause() {} load() {} remove() {} async decode() {}
}
function deferred() { let resolve; const promise=new Promise(r=>{resolve=r;}); return {promise,resolve}; }
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function setup(fetcher) {
  const stage=new Element('div'), image=new Element('img'); image.parentElement=stage;
  const document=new Element('document'); document.hidden=false; document.createElement=tag=>new Element(tag);
  const window=new Element('window'), timers=new Map(), requests=[], callbacks=[];
  let id=0;
  const context={window,document,performance:{now:()=>1000},AbortController,
    localStorage:{getItem:()=>null,setItem(){}},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},
    setTimeout(fn){timers.set(++id,fn);return id;},clearTimeout(key){timers.delete(key);},
    fetch(url,options){requests.push(url);return fetcher(url,options);}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/meowkeeper/app/web/static/js/shared-preview.js'),'utf8'),context);
  const preview=window.MeowKeeperPreview.mount({image,onFrame:frame=>callbacks.push(frame)});
  const button=mode=>stage.children.find(n=>n.className==='preview-mode-hud').children.find(n=>n.dataset.previewMode===mode);
  return {preview,button,image,timers,requests,callbacks};
}
const metadata={source_status:'running',frame_age_seconds:0,source_connections:1,source_is_xiaomi:false};
async function main(){
  // Fetch implementations can resolve after abort; the generation guard is
  // still required, especially when JSON decoding is already in progress.
  const pending=deferred();
  const first=setup(url=>url.endsWith('/status')?pending.promise:Promise.resolve({ok:true,blob:async()=>({type:'image/jpeg',size:30})}));
  await first.button('sleep').listeners.click();
  pending.resolve({ok:true,json:async()=>metadata}); await settle();
  assert.equal(first.requests.filter(url=>url.endsWith('/jpeg')).length,0);
  assert.equal(first.timers.size,0); assert.equal(first.image.src,undefined);
  first.preview.close();

  const jpeg=deferred();
  const second=setup(url=>Promise.resolve(url.endsWith('/status')?{ok:true,json:async()=>metadata}:{ok:true,blob:()=>jpeg.promise}));
  await settle();
  await second.button('sleep').listeners.click();
  jpeg.resolve({type:'image/jpeg',size:30}); await settle();
  assert.equal(second.callbacks.length,0); assert.equal(second.image.src,undefined); assert.equal(second.timers.size,0);
  second.preview.close();

  const old=deferred();let count=0;
  const third=setup(url=>url.endsWith('/status')?(++count===1?old.promise:Promise.resolve({ok:true,json:async()=>metadata})):Promise.resolve({ok:true,blob:async()=>({type:'image/jpeg',size:30})}));
  await third.button('sleep').listeners.click();
  await third.button('energy').listeners.click(); await settle();
  const currentFrames=third.callbacks.length;
  old.resolve({ok:true,json:async()=>metadata}); await settle();
  assert.equal(third.callbacks.length,currentFrames,'old mount cannot create another poll loop');
  assert.equal(third.timers.size,1,'only one current JPEG poll timer');
  third.preview.close();
  console.log('passed: late metadata, late JPEG body, sleep/resume generations, single poll loop');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
