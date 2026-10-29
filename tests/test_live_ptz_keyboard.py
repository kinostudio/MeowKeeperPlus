"""Execute the real keyboard listeners with a minimal DOM, without moving cameras."""
import shutil
import subprocess
import unittest
from pathlib import Path


class LivePtzKeyboardTests(unittest.TestCase):
    def test_keyboard_boundaries_and_release_stop(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("未安裝 Node；鍵盤 JavaScript 執行驗證未執行。")
        template = Path(__file__).resolve().parents[1] / "src/meowkeeper/app/web/templates/live.html"
        program = r'''
const fs = require("fs"), vm = require("vm"), assert = require("assert");
const text = fs.readFileSync(process.argv[1], "utf8");
const down = text.slice(text.indexOf('  document.addEventListener("keydown"'), text.indexOf('  async function emergencyStop'));
const up = text.slice(text.indexOf('  document.addEventListener("keyup"'), text.indexOf('  qualityApply?.addEventListener'));
const listeners = {}, windowListeners = {}, clicks = [];
let stops = 0;
const context = { document: {hidden: false, addEventListener: (key, fn) => listeners[key] = fn, querySelectorAll: () => []},
  window: {addEventListener: (key, fn) => windowListeners[key] = fn},
  directionControls: {hidden: false, querySelector: s => ({disabled: false, click: () => clicks.push(s)}), querySelectorAll: () => []},
  ptzControls: {querySelectorAll: () => []},
  activePtzDeviceId: "test-camera", ptzRequestInFlight: false, ptzMoveStarted: true, emergencyStop: () => stops++ };
vm.createContext(context); vm.runInContext(down + up, context);
function event(key, extra={}) { return {key, repeat:false, target:{closest:()=>null}, preventDefault(){this.prevented=true;}, ...extra}; }
const first = event("ArrowUp"); listeners.keydown(first);
assert.equal(clicks.length, 1); assert(first.prevented); assert(clicks[0].includes('"up"'));
for (const blocked of [event("ArrowDown", {repeat:true}), event("ArrowDown", {ctrlKey:true}),
  event("ArrowDown", {target:{closest:()=>({})}})]) listeners.keydown(blocked);
assert.equal(clicks.length, 1);
context.document.hidden = true; listeners.keydown(event("ArrowLeft")); assert.equal(clicks.length, 1);
context.document.hidden = false; context.ptzRequestInFlight = true;
listeners.keydown(event("ArrowRight")); assert.equal(clicks.length, 1);
listeners.keyup(event("ArrowUp")); assert.equal(stops, 1);
windowListeners.blur(); assert.equal(stops, 2);
listeners.keydown(event("Escape")); assert.equal(stops, 3);
context.directionControls.hidden = true; context.ptzRequestInFlight = false;
listeners.keydown(event("ArrowUp")); assert.equal(clicks.length, 1);
console.log("keyboard boundaries passed");
'''
        result = subprocess.run([node, "-e", program, str(template)], capture_output=True, text=True,
                                timeout=15, shell=False, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("keyboard boundaries passed", result.stdout)
