import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from meowkeeper.app.source_config import load_runtime_settings, save_runtime_settings
from meowkeeper.app.web.contracts import SettingsValidationError, validate_settings_with_pydantic
from meowkeeper.app.web.main import create_app
from meowkeeper.tray.client import TrayApiClient
from meowkeeper.tray.main import status_payload, status_summary


class DeviceAndTrayAuthTests(unittest.TestCase):
    def test_browser_js_loads_and_submits_device_as_string(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const items={}; const el=id=>items[id]||(items[id]={value:'1',listeners:{},addEventListener(k,f){this.listeners[k]=f},replaceChildren(){},appendChild(){}});
const calls=[];
const context={document:{cookie:'',hidden:true,getElementById:el,createElement:()=>({}),addEventListener(){}},
 window:{setInterval(){},confirm:()=>true},fetch:async(path,options={})=>{
 calls.push({path,options});return {ok:true,json:async()=>path==='/api/v1/settings'?{settings:{inference_device:'intel:gpu'}}:{}};
}};
vm.createContext(context);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
(async()=>{await new Promise(r=>setTimeout(r,10));assert.equal(el('ai-inference-device').value,'intel:gpu');
el('ai-inference-device').value='onnx:cuda';
await el('ai-settings-form').listeners.submit({preventDefault(){}});
const saved=calls.find(c=>c.options.method==='PUT');assert.equal(JSON.parse(saved.options.body).inference_device,'onnx:cuda');
console.log('device form roundtrip passed');})().catch(e=>{console.error(e);process.exitCode=1});
'''
        result = subprocess.run([str(root / "vendor/javascript/node.exe"), "-e", program,
                                 str(root / "src/meowkeeper/app/web/static/js/ai.js")],
                                capture_output=True, text=True, timeout=15, shell=False, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_modes_persist_and_invalid_values_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            self.assertEqual(load_runtime_settings(path)["inference_device"], "auto")
            for device in ("auto", "cpu", "intel:gpu", "onnx:cuda"):
                valid = validate_settings_with_pydantic({"inference_device": device})
                save_runtime_settings(path, valid)
                self.assertEqual(load_runtime_settings(path)["inference_device"], device)
            for value in ("cuda:9", "shell", 1, True, ""):
                with self.assertRaises(SettingsValidationError):
                    validate_settings_with_pydantic({"inference_device": value})
                with self.assertRaises(ValueError):
                    save_runtime_settings(path, {"inference_device": value})

    def test_no_token_never_requests_protected_status(self):
        client = TrayApiClient(token=None)
        with patch.object(TrayApiClient, "health", return_value={"status": "ok", "core_status": "running"}), \
             patch.object(TrayApiClient, "status") as status:
            payload = status_payload(client)
        status.assert_not_called()
        self.assertTrue(payload["limited"])
        self.assertIn("詳細狀態未授權", status_summary(payload))

    def test_authenticated_client_still_reads_full_status(self):
        client = Mock(token="test-only-token")
        client.health.return_value = {"status": "ok"}
        client.status.return_value = {"status": "running"}
        self.assertNotIn("limited", status_payload(client))
        client.status.assert_called_once()

    def test_api_login_guard_unchanged_and_ui_has_device_options(self):
        app = create_app(token="t" * 32)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            self.assertEqual(client.get("/api/v1/status").status_code, 401)
            response = client.get("/settings/ai", headers={"Authorization": "Bearer " + "t" * 32})
            for option in ('value="cpu"', 'value="intel:gpu"', 'value="onnx:cuda"'):
                self.assertIn(option, response.text)
            self.assertEqual(response.status_code, 200)
            token = "t" * 32
            import hashlib
            headers = {"Authorization": "Bearer " + token,
                       "X-MeowKeeper-CSRF": hashlib.sha256((token + ":csrf").encode()).hexdigest()}
            for device in ("auto", "cpu", "intel:gpu", "onnx:cuda"):
                saved = client.put("/api/v1/settings", headers=headers, json={"inference_device": device})
                self.assertEqual(saved.status_code, 200)
                self.assertTrue(saved.json()["restart_required"])
                self.assertEqual(saved.json()["settings"]["inference_device"], device)
