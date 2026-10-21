"""位置參考圖只顯示驗證座標，不模擬硬體讀回成功。"""
import subprocess
import unittest
from pathlib import Path


class PositionVisualTests(unittest.TestCase):
    def test_verified_coordinates_failure_and_boundaries(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const text = fs.readFileSync(process.argv[1], 'utf8');
const code = text.slice(text.indexOf('  function renderPositionReference('), text.indexOf('  let nativeStandbyAvailable'));
const dot = {values:{}, setAttribute(k,v){this.values[k]=v}, removeAttribute(){}}, caption = {};
const context = {absoluteMoveVerified:false, document:{getElementById:id=>id==='ptz-position-dot'?dot:caption}};
vm.createContext(context); vm.runInContext(code,context);
context.renderPositionReference({angle:0,elevation:101},true);
assert.equal(dot.values.visibility,'visible'); assert.equal(dot.values.cx,'30'); assert.equal(dot.values.cy,'15');
assert(caption.textContent.includes('不是角度'));
assert(caption.textContent.includes('點選定位已停用'));
for(const position of [null,{angle:102,elevation:0},{angle:50.5,elevation:0},{angle:'50',elevation:0}]){
 context.renderPositionReference(position,true); assert.equal(dot.values.visibility,'hidden');
}
context.renderPositionReference({angle:75,elevation:29},false); assert.equal(dot.values.visibility,'hidden');
assert(text.includes('renderPositionReference(null, false)'));
console.log('position reference passed');
'''
        result = subprocess.run(
            [str(root / "vendor/javascript/node.exe"), "-e", program,
             str(root / "src/meowkeeper/app/web/templates/live.html")],
            capture_output=True, text=True, timeout=15, shell=False, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        template = (root / "src/meowkeeper/app/web/templates/live.html").read_text(encoding="utf-8")
        css = (root / "src/meowkeeper/app/web/static/css/experience.css").read_text(encoding="utf-8")
        self.assertIn('id="ptz-position-hud-map"', template)
        self.assertIn('id="ptz-position-hud-dot"', template)
        self.assertIn('圖示線性映射裝置 0～101 座標', template)
        self.assertIn('點選定位已停用', template)
        self.assertIn('}, 4000);', template)
        self.assertIn("rgba(34,29,24,.66)", css)
