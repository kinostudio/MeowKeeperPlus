"""Explicit synthetic-only HTTP fixture for browser acceptance; never run_host."""
from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
sys.path.insert(0,str(Path(__file__).resolve().parent))

from PIL import Image, ImageDraw
from test_camera_connection_workflow import fixture_service

from meowkeeper.app.web.api import create_app
from meowkeeper.app.web.session import SessionManager
from meowkeeper.app.web.state import CoreStateFacade
from meowkeeper.storage.database import SQLiteStore


def build_fixture(root):
    store=SQLiteStore(root/"events.db");store.initialize()
    image=Image.new("RGB",(1280,720),"#dfd0ba")
    draw=ImageDraw.Draw(image)
    for x in range(0,1280,80):draw.line((x,0,x,720),fill="#bdab92",width=1)
    for y in range(0,720,80):draw.line((0,y,1280,y),fill="#bdab92",width=1)
    draw.rounded_rectangle((300,220,980,500),radius=30,fill="#f5efe6")
    draw.text((390,350),"ISOLATED CAMERA UI FIXTURE - NOT A REAL CAMERA",fill="#74452d")
    photo=root/"preview.jpg";image.save(photo)
    service,ready=fixture_service(root)
    ready.set()
    service.active_source_url=lambda:"rtsp://127.0.0.1:8554/camera_test"
    service._state.update(setup_stage="connected",selected_stream_name="camera_test",selected_profile="subtype=1")
    service._devices[0].update(selected=True,stream_name="camera_test")
    service._profiles["camera_test"]=[service.source_manager.verify_active_profile.return_value]
    service.experimental_ptz_enabled=True
    state={"connected":True,"position":{"ret":0,"angle":40,"elevation":50},
           "capabilities":{"absolute_move":True,"position":True,"stop":True},
           "limits":{"angle_min":1,"angle_max":101,"elevation_min":1,"elevation_max":101}}
    def ptz_action(_stream,action,**kwargs):
        if action=="position":state["position"].update(angle=kwargs["angle"],elevation=kwargs["elevation"])
        if action=="move":state["position"]["elevation"]+=1 if kwargs.get("direction")=="up" else -1
        return state
    service.ptz_client=SimpleNamespace(xiaomi_ptz_state=lambda *_args,**_kwargs:state,xiaomi_ptz_action=ptz_action)
    peers={"status":"ok","logged_in":True,"tailscale_ip":"100.64.0.10","device_list_available":True,
           "devices":[{"device_ref":"fixture-peer","hostname":"隔離觀看主機","online":True,"is_self":False,"tailnet_ips":["100.64.0.11"]}]}
    def snapshot():
        stamp=datetime.now(timezone.utc).isoformat()
        return {"status":"running","started_at":stamp,"metrics":{
            "source":{"status":"running","source_id":"隔離測試攝影機","connections":1,"resolution":"1280×720","width":1280,"height":720,"fps":5,"last_frame_at":stamp,"preview_saved_at":stamp,"preview_epoch":"fixture","stream_name":"camera_test"},
            "ai":{"backend":"合成 GPU 讀值","device":"fixture:gpu","fps":3.2,"inference_input_size":960,"model":"隔離測試模型","adaptive":{"effective_input_size":960,"effective_fps":5,"temperature_c":54,"load_percent":15,"reason_zh_tw":"隔離測試的分析狀態"}},
            "telegram":{"enabled":True,"status":"enabled","recipient_count":2,"queue_pending":0},"ring_buffer":{"seconds":60,"segment_count":12}}}
    facade=CoreStateFacade(store=store,event_directory=root,callbacks={"status":snapshot,"snapshot":lambda **_:photo,
        "tailscale_status":lambda:peers,"save_capture":lambda kind:{"status":"saved","message_zh_tw":f"已保存至 {root / ('photo/example.jpg' if kind=='photo' else 'video/example.mp4')}（原始解析度 3840×2160）","actual_duration_seconds":15}})
    facade._collect_application_resources=Mock(return_value={"rss_bytes":1000000})
    facade.set_latest_jpeg(photo)
    sessions=SessionManager();sessions.configure_admin("fixture-admin","fixture-password-only")
    app=create_app(facade=facade,integrations=service,token="fixture-api-only-0000000000000000",session_manager=sessions)
    app.state.templates.env.globals["preview_banner"]="隔離介面測試：攝影機與硬體數據均為合成資料。"
    recorder=app.state.resource_recorder
    recorder.start=lambda:None;recorder.stop=lambda:None
    import time
    recorder.live_snapshot=lambda:{"sampled_at":time.time(),"system_cpu_percent":26.5,"system_ram_percent":48.3,"system_gpu_percent":15,
        "cpu_temperature_c":67,"gpu_temperature_c":54,"ram_used_bytes":8e9,"ram_total_bytes":16e9,"gpu_name":"合成測試 GPU",
        "cpu_sensor":{"label":"合成 CPU 溫度"}}
    return app


if __name__=="__main__":
    import uvicorn
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=18979);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="meowkeeper-ui-fixture-") as directory:
        uvicorn.run(build_fixture(Path(directory)),host="127.0.0.1",port=args.port,log_level="warning")
