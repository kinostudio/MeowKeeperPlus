const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync(require('node:path').join(__dirname,'../../src/meowkeeper/app/web/static/js/xiaomi-wizard.js'),'utf8');
const start=code.indexOf('  function runtime(data)'),end=code.indexOf("  document.addEventListener('meowkeeper:status'",start);
assert(start>=0&&end>start);
const nodes={},el=id=>nodes[id]??={textContent:''},badges={};
const context={Date,Number,items:[{selected:true,display_name:'測試攝影機'}],el,badge:(id,label,kind)=>badges[id]={label,kind}};
vm.createContext(context);vm.runInContext(code.slice(start,end),context);
for(const [frame,status,connections,expected] of [
 ['2026-10-18T00:00:09Z','running',1,'ok'],
 ['2026-10-17T23:59:00Z','running',1,'warn'],
 [null,'running',1,'warn'],
 ['2026-10-18T00:00:09Z','error',1,'error'],
 ['2026-10-18T00:00:09Z','running',0,'warn']
]){
 context.runtime({checked_at:'2026-10-18T00:00:10Z',metrics:{source:{last_frame_at:frame,preview_saved_at:null,status,connections}}});
 assert.equal(badges['xiaomi-live-badge'].kind,expected);
 assert(!el('xiaomi-live-status').textContent.includes('JPEG'));
}
console.log('passed: camera state uses current ingest frame, connection and lifecycle independently of JPEG cache');
