const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.style={};this.attrs={};this.listeners={};this.hidden=false;}
  append(...nodes){this.children.push(...nodes);}
  after(node){this.afterNode=node;}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.listeners[k]=v;}
  remove(){this.removed=true;}
  getBoundingClientRect(){return {left:10,top:20,width:960,height:540};}
}
const stage=new Element(),hud=new Element(),timers=new Map();let serial=0,now=10000;
const context={window:{},document:{createElement:()=>new Element()},localStorage:{getItem:()=>null,setItem(){}},
  Date:{now:()=>now,parse:Date.parse},setInterval(fn){timers.set(++serial,fn);return serial;},clearInterval(id){timers.delete(id);}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/meowkeeper/app/web/static/js/ai-overlay.js'),'utf8'),context);
const overlay=context.window.MeowKeeperOverlay.mount(stage,hud),layer=stage.children[0];
const media=new Element();media.naturalWidth=1920;media.naturalHeight=1080;
assert.equal(timers.size,0,'mount does not start unnecessary draw timer');
overlay.update({captured_at:new Date(now).toISOString(),detections:[{bbox:[.1,.2,.4,.5],confidence:.9,identity:'<img src=x>',behavior:'行走'},{bbox:[-1,0,1,1]}]},media);
assert.equal(layer.children.length,1);
assert.equal(layer.children[0].style.left,'96px');
assert.equal(layer.children[0].style.top,'108px');
hud.children.find(x=>x.textContent==='身分候選').listeners.click();
assert.match(layer.children[0].children[0].textContent,/<img src=x>/,'text remains literal, not HTML');
assert.equal(timers.size,1);
now=13000;[...timers.values()][0]();
assert.equal(layer.children.length,0);assert.equal(timers.size,0,'expired evidence stops drawing');
overlay.update({captured_at:new Date(now).toISOString(),detections:[]},media);
assert.equal(layer.children.length,1,'recent detection box is held for 0.5~1s across empty poll');
assert.equal(timers.size,1);
overlay.clear();assert.equal(timers.size,0,'sleep/hidden stops all drawing');
media.naturalWidth=3840;media.naturalHeight=2160;
overlay.update({captured_at:new Date(now).toISOString(),detections:[{
  // Normalized YOLO box measured from the user-provided 3840x2160 event clip.
  bbox:[1929.4/3840,185.1/2160,2295.3/3840,405.7/2160],confidence:.6855,
}]},media);
assert.equal(layer.children.length,1,'event-video cat candidate is rendered');
assert.ok(Math.abs(parseFloat(layer.children[0].style.left)-482.35)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.top)-46.275)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.width)-91.475)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.height)-55.15)<.1);
overlay.close();assert.equal(stage.afterNode.removed,true);
console.log('passed: normalized placement, event-video bbox geometry, literal labels, invalid bounds, expiry and idle cleanup');
