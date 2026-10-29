const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
class Element {
  constructor(){this.children=[];this.style={};this.attrs={};this.listeners={};this.hidden=false;this.classes=new Set();
    this.classList={contains:name=>this.classes.has(name),add:name=>this.classes.add(name),remove:name=>this.classes.delete(name),
      toggle:(name,force)=>{const enabled=force===undefined?!this.classes.has(name):Boolean(force);if(enabled)this.classes.add(name);else this.classes.delete(name);return enabled;}};}
  append(...nodes){this.children.push(...nodes);}
  appendChild(node){this.children.push(node);return node;}
  contains(node){return this.children.includes(node);}
  after(node){this.afterNode=node;}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.listeners[k]=v;}
  remove(){this.removed=true;}
  getBoundingClientRect(){return {left:10,top:20,width:960,height:540};}
}
const stage=new Element(),hud=new Element(),timers=new Map();let serial=0,now=10000;
const context={window:{},document:{createElement:()=>new Element(),addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},
  Date:{now:()=>now,parse:Date.parse},setInterval(fn){timers.set(++serial,fn);return serial;},clearInterval(id){timers.delete(id);}};
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../src/meowkeeper/app/web/static/js/ai-overlay.js'),'utf8'),context);
const overlay=context.window.MeowKeeperOverlay.mount(stage,hud),layer=stage.children[0];
const media=new Element();media.naturalWidth=1920;media.naturalHeight=1080;
assert.equal(timers.size,0,'mount does not start unnecessary draw timer');
overlay.update({captured_at:new Date(now).toISOString(),detections:[{bbox:[.1,.2,.4,.5],confidence:.9,identity:'<img src=x>',behavior:'行走'},{bbox:[-1,0,1,1]}]},media);
assert.equal(layer.children.length,1);
assert.equal(layer.children[0].style.left,'96px');
assert.equal(layer.children[0].style.top,'108px');
const identityToggle=hud.children[0].children[1].children.find(x=>x.children[1].textContent.startsWith('身分偵測顯示')).children[0];
identityToggle.checked=false;identityToggle.listeners.change();
assert.doesNotMatch(layer.children[0].children[0].textContent,/身分偵測：/);
identityToggle.checked=true;identityToggle.listeners.change();
assert.match(layer.children[0].children[0].textContent,/<img src=x>/,'text remains literal, not HTML');
assert.match(layer.children[0].children[0].textContent,/身分偵測：/,'identity is independently toggleable');
assert.equal(timers.size,1);
now=10500;
overlay.update({captured_at:new Date(now).toISOString(),detections:[]},media);
assert.equal(layer.children.length,1,'recent detection is held across a short empty poll');
now=10900;[...timers.values()][0]();
assert.equal(layer.children.length,0,'empty detection hold expires after its bounded interval');
overlay.update({captured_at:new Date(now).toISOString(),detections:[{bbox:[.1,.2,.4,.5],confidence:.9}]},media);
now=13000;[...timers.values()][0]();
now=14000;[...timers.values()][0]();
assert.equal(layer.children.length,0);assert.equal(timers.size,0,'expired evidence stops drawing');
overlay.update({captured_at:new Date(now).toISOString(),detections:[]},media);
assert.equal(layer.children.length,0,'an empty poll cannot revive expired evidence');
assert.equal(timers.size,1);
overlay.clear();assert.equal(timers.size,0,'sleep/hidden stops all drawing');
media.naturalWidth=3840;media.naturalHeight=2160;
overlay.update({captured_at:new Date(now).toISOString(),detections:[{
  // Synthetic normalized box verifies 4K geometry without a household image.
  bbox:[.5,.1,.6,.2],confidence:.7,
}]},media);
assert.equal(layer.children.length,1,'event-video cat candidate is rendered');
assert.ok(Math.abs(parseFloat(layer.children[0].style.left)-480)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.top)-54)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.width)-96)<.1);
assert.ok(Math.abs(parseFloat(layer.children[0].style.height)-54)<.1);
overlay.close();assert.equal(stage.afterNode.removed,true);
console.log('passed: normalized placement, event-video bbox geometry, literal labels, invalid bounds, expiry and idle cleanup');
