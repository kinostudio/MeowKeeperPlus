const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

class Element {
  constructor(){this.children=[];this.listeners={};this.attrs={};this.dataset={};this.classList={add(){}};this.hidden=false;this.textContent='';}
  append(...nodes){this.children.push(...nodes);}
  addEventListener(name,fn,options){(this.listeners[name]??=[]).push({fn,options});}
  setAttribute(name,value){this.attrs[name]=value;}
  removeAttribute(name){delete this.attrs[name];}
}

const stage=new Element(),result=new Element(),photo=new Element(),video=new Element();
photo.dataset.saveCapture='photo';video.dataset.saveCapture='video';
const timers=new Map(),observers=[];let nextTimer=0;
class MutationObserver {constructor(callback){this.callback=callback;observers.push(this);}observe(){} }
const document={
  getElementById:id=>id==='capture-result'?result:null,
  createElement:()=>new Element(),
  querySelector:selector=>selector==='.preview-stage,.overview-preview'?stage:null,
  querySelectorAll:selector=>selector==='[data-save-capture]'?[photo,video]:[],
  addEventListener(){},
};
const context={document,window:{},MutationObserver,navigator:{},Date,console,
  setTimeout(fn,delay){const id=++nextTimer;timers.set(id,{fn,delay});return id;},
  clearTimeout(id){timers.delete(id);}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/meowkeeper/app/web/static/js/monitor-ui.js'),'utf8'),context);

const toast=stage.children.find(child=>child.className==='capture-toast');
assert(toast,'preview contains the status snackbar');
assert.equal(toast.attrs.role,'status');
assert.equal(toast.attrs['aria-live'],'polite');
assert.equal(result.attrs['aria-live'],'off','the original result is not announced twice');
assert.equal(toast.children.length,0,'no manual close/copy controls are added');
const observer=observers[0];
function clickCapture(button){button.listeners.click.find(item=>item.options?.capture)?.fn();}
function publish(message){result.textContent=message;observer.callback();}
function activeTimer(){return [...timers.values()].at(-1);}

clickCapture(photo);publish('正在保存既有影音…');
assert.equal(toast.textContent,'正在保存截圖…');
assert.equal(activeTimer().delay,8000);
activeTimer().fn();assert.equal(toast.hidden,true,'pending notice auto-dismisses');

clickCapture(photo);publish('已保存至 photo/example.jpg（原始解析度 3840×2160）');
assert.equal(toast.textContent,'截圖已保存');
assert.equal(activeTimer().delay,5000);
activeTimer().fn();assert.equal(toast.hidden,true,'success notice auto-dismisses');

clickCapture(video);publish('影片保存失敗，請檢查資料夾');
assert.equal(toast.textContent,'保存未完成');
assert.equal(toast.dataset.kind,'error');
assert.equal(activeTimer().delay,7000);
activeTimer().fn();assert.equal(toast.hidden,true,'error notice auto-dismisses');
console.log('passed: concise snackbar, no controls, no duplicate live announcement, all states auto-dismiss');
