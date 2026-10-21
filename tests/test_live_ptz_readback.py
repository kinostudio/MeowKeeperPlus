"""執行實際頁面 JavaScript；不送出真實攝影機指令。"""
import subprocess
import unittest
from pathlib import Path


class LivePtzReadbackTests(unittest.TestCase):
    def test_initial_move_readback_and_transient_hud(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
const fs=require('fs'), vm=require('vm'), assert=require('assert');
const text=fs.readFileSync(process.argv[1],'utf8');
const nodes={}, elements=()=>({hidden:false,disabled:false,textContent:'',value:'position',attributes:{},
 setAttribute(k,v){this.attributes[k]=v},removeAttribute(k){delete this.attributes[k]},querySelectorAll:()=>[],className:''});
const get=id=>nodes[id] ||= elements();
const calls=[], timers=new Map(); let timerId=0, position={angle:40,elevation:50};
let failRead=false, failStop=false, stale=false, cancelDuringRead=false;
const c={document:{getElementById:get,querySelectorAll:()=>[]},AbortSignal,encodeURIComponent,Number,String,csrfToken:()=>'',drawTarget(){},
 ptzMoveStarted:false,ptzStopVersion:0,window:{clearTimeout:id=>timers.delete(id),setTimeout:(fn,ms)=>{
   if(ms===300){fn();return 0;} const id=++timerId;timers.set(id,{fn,ms});return id;
 }}, activePtzDeviceId:'camera',ptzRequestInFlight:false,qualityRequestInFlight:false,
 ptzControls:elements(),directionControls:elements(),ptzMessage:elements(),ptzStatus:elements(),
 ptzDevice:elements(),renderQuality(){},setPtzControlsEnabled(){},setPositionControls(){},
 nativeStandbyAvailable:false,setPtzMessage:(message,status)=>{
 c.ptzMessage.textContent=message;c.ptzStatus.textContent=status;
 },fetch:async(url,options={})=>{
  if(url.endsWith('/devices'))return {ok:true,json:async()=>({items:[{selected:true,internal_id:'camera',ptz:{enabled:true}}]})};
  if(url.includes('/preview/status'))return {ok:true,json:async()=>({})};
  if(url.endsWith('/privacy'))return {ok:true,json:async()=>({})};
  const body=options.body?JSON.parse(options.body):null;
  calls.push(body?.action||'read');
  if(!body&&cancelDuringRead)c.ptzStopVersion++;
  if(body?.action==='move')position={angle:40,elevation:53};
  if(stale)c.activePtzDeviceId='other';
  return {ok:true,json:async()=>body?{success:!(failStop&&body.action==='stop')}:
   {success:!failRead,position_readback_verified:!failRead,state:{position:{...position},capabilities:{absolute_move:true}}}};
 }};
vm.createContext(c);
vm.runInContext(text.slice(text.indexOf('  let positionReadbackVerified'),text.indexOf('  function renderPositionReference')),c);
// Keep the real coordinate validator and SVG rendering.
vm.runInContext(text.slice(text.indexOf('  function renderPositionReference'),text.indexOf('  let nativeStandbyAvailable')),c);
vm.runInContext(text.slice(text.indexOf('  async function loadPtz()'),text.indexOf('  ptzControls.querySelectorAll("[data-privacy-action]").forEach((button) => {\n    button.addEventListener')),c);
(async()=>{
 await c.loadPtz();assert.deepEqual(calls,['read']);
 assert(get('ptz-position-result').textContent.includes('水平 40／垂直 50'));
 await c.loadPtz();assert.deepEqual(calls,['read']); // no position polling
 calls.length=0;
 await c.sendPtz({action:'move',direction:'up'},elements());
 assert.deepEqual(calls,['read','move','stop','read']);
 assert(get('ptz-position-hud-text').textContent.includes('垂直 +3'));
 assert.equal(get('ptz-position-hud').hidden,false);
 assert.equal(get('ptz-position-hud-map').hidden,false);
 assert.equal(get('ptz-position-hud-dot').attributes.visibility,'visible');
 assert.equal(get('ptz-position-hud-dot').attributes.cx,String(10+40/101*100));
 assert.equal(get('ptz-position-hud-dot').attributes.cy,String(72-53/101*64));
 assert.equal(c.ptzStatus.textContent,'已確認位置變化');
 assert(c.ptzMessage.textContent.includes('已讀回位置變化'));
 assert.equal([...timers.values()].at(-1).ms,4000);
 [...timers.values()].at(-1).fn();assert.equal(get('ptz-position-hud').hidden,true);
 const before={...position}; await c.readPtzPosition('camera',{before,overlay:true});
 assert(get('ptz-position-hud-text').textContent.includes('座標未變'));
 assert.equal(timers.size,1); // a newer read replaces the old hide timer
 failRead=true; await c.readPtzPosition('camera',{overlay:true});
 assert(get('ptz-position-hud-text').textContent.includes('讀回失敗'));
 assert.equal(get('ptz-position-hud-map').hidden,true);
 assert.equal(get('ptz-position-dot').textContent,'');
 failRead=false;failStop=true;
 await c.sendPtz({action:'move',direction:'up'},elements());
 assert(c.ptzMessage.textContent.includes('停止指令未確認'));
 calls.length=0;cancelDuringRead=true;
 await c.sendPtz({action:'move',direction:'up'},elements());
 assert(!calls.includes('move'));assert(calls.includes('stop'));
 cancelDuringRead=false;
 const old=get('ptz-position-result').textContent;stale=true;
 await c.readPtzPosition('camera',{overlay:true});
 assert.equal(get('ptz-position-result').textContent,old);
 console.log('initial, move/stop/read, visual coordinate HUD, delta, unchanged, failure, stale source, 4s HUD passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(
            [str(root / "vendor/javascript/node.exe"), "-e", program,
             str(root / "src/meowkeeper/app/web/templates/live.html")],
            capture_output=True, text=True, timeout=20, shell=False, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("4s HUD passed", result.stdout)
