"""三項缺口的API→執行端→SQLite接線；硬體／雲端使用明確替身。"""
import hashlib
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from meowkeeper.app.core_controller import RepositoryCoreController
from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.behavior.scene_views import SceneViewRegistry
from meowkeeper.core.event_semantics import (
    EventSemanticService,
    ProviderReply,
    SemanticRequest,
    SemanticResult,
)
from meowkeeper.core.events.web_review import WebEventReview
from meowkeeper.core.identity.pet_registry import PetRegistry
from meowkeeper.core.inference.adaptive_analyzer import AdaptiveAnalyzer
from meowkeeper.core.media.event_clip import EventClipService
from meowkeeper.storage.database import SCHEMA_VERSION, SQLiteStore
from meowkeeper.storage.semantic_jobs import SemanticJobJournal


class V43CompletionWiringTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=Path(temporary.name)
        self.store=SQLiteStore(self.root / "events.db");self.addCleanup(self.store.close)
        self.event=str(uuid4());self.source="source-"+"a"*24
        self.store.create_media_event(event_id=self.event,event_type="cat_detected",trigger_at=datetime.now(timezone.utc),
            source_id="synthetic",track_id=None,pet_id=None,confidence=.8,requested_pre_seconds=0,requested_post_seconds=5)
        self.store.update_media_event(self.event,actual_duration_seconds=10)
        target=BytesIO();Image.fromarray(np.random.default_rng(42).integers(0,256,(480,640,3),dtype=np.uint8)).save(target,format="JPEG")
        self.jpeg=target.getvalue();self.frame=self.root/"shared.jpg";self.frame.write_bytes(self.jpeg)
        self.registry=PetRegistry(self.root/"pets");self.registry.register_pet("測試貓",pet_id="test-cat")
        token="t"*32;self.headers={"Authorization":"Bearer "+token,"X-MeowKeeper-CSRF":hashlib.sha256((token+":csrf").encode()).hexdigest()}
        self.token=token

    def app(self,callbacks=None,integration=None):
        def snapshot():
            self.frame.touch();return self.frame
        facade=CoreStateFacade(store=self.store,event_directory=self.root/"events",callbacks={"preview_snapshot":snapshot,"behavior_zones":lambda:{"source_ref":self.source},**(callbacks or {})})
        return create_app(facade=facade,token=self.token,integrations=integration)

    def test_cancel_queue_and_running_through_core_callbacks_without_duplicate_api(self):
        entered=threading.Event();release=threading.Event();calls=[]
        def analyze(request,**kwargs):
            calls.append(request.event_id);entered.set()
            if not release.wait(30):
                raise TimeoutError("測試取消流程未在期限內釋放替身。")
            return ProviderReply({"tracks":[{"track_id":"scene","tags":["walking"]}]},request_dispatched=True,http_status=200)
        service=EventSemanticService(SimpleNamespace(analyze=analyze),journal=SemanticJobJournal(self.store),result_sink=self.store.record_event_semantics)
        self.addCleanup(service.close);self.addCleanup(release.set)
        controller=RepositoryCoreController(store=self.store,ring_directory=self.root/"ring",event_directory=self.root/"events",preview_directory=self.root/"preview")
        controller._semantic_service=service
        with TestClient(self.app(controller.callbacks())) as client:
            first=service.submit(SemanticRequest(self.event,b"one"));self.assertTrue(entered.wait(1))
            second=service.submit(SemanticRequest(self.event,b"two"))
            url=f"/api/v1/events/{self.event}/analysis/{second.semantic_job_id}/cancel"
            self.assertEqual(client.post(url).status_code,401)
            self.assertEqual(client.post(url,headers=self.headers).status_code,200)
            self.assertEqual(second.result(1).status,"cancelled")
            runtime=client.get("/api/v1/vision/runtime",headers=self.headers).json();self.assertTrue(runtime["worker_active"])
            self.assertEqual(client.post(f"/api/v1/events/{self.event}/analysis/{first.semantic_job_id}/cancel",headers=self.headers).status_code,200)
            release.set();result=first.result(2)
            self.assertEqual(result.status,"cancelled");self.assertTrue(result.request_dispatched);self.assertEqual(len(calls),1)
            self.assertEqual(self.store.connection.execute("SELECT terminal_reason FROM semantic_work_jobs WHERE job_id=?",(first.semantic_job_id,)).fetchone()[0],"user_cancelled")

    def test_real_adaptive_state_skips_cloud_but_does_not_call_or_stop_local_analyzer(self):
        analyzer=AdaptiveAnalyzer(SimpleNamespace(inference_backend="CPU"),fps=5,logger=lambda _:None)
        controller=RepositoryCoreController(store=self.store,ring_directory=self.root/"ring",event_directory=self.root/"events",preview_directory=self.root/"preview")
        controller._adaptive_analyzer=analyzer
        controller._clip_service=SimpleNamespace(media_busy=True)
        self.assertEqual(controller.semantic_pressure(),"media_preparing")
        controller._clip_service=None
        provider=SimpleNamespace(analyze=lambda *_a,**_k:self.fail("不應外送"))
        for state,expected in (("_temperature","thermal_pressure"),("_load","compute_pressure"),("_pressure","memory_pressure")):
            analyzer._temperature=None;analyzer._load=None;analyzer._pressure=False
            setattr(analyzer,state,True if state=="_pressure" else 90)
            self.assertEqual(analyzer.cloud_pressure_reason(),expected)
            service=EventSemanticService(provider,journal=SemanticJobJournal(self.store))
            try:
                service.pressure_probe=controller.semantic_pressure
                result=service.submit(SemanticRequest(self.event,expected.encode())).result(2)
                self.assertEqual(result.status,"skipped_pressure");self.assertFalse(result.request_dispatched)
            finally:service.close()

    def test_media_preparation_flag_is_released_even_when_transcode_fails(self):
        service=EventClipService.__new__(EventClipService)
        service._media_lock=threading.Lock();service._media_tasks=0
        def failing_transcode():
            self.assertTrue(service.media_busy)
            raise OSError("合成測試失敗，不含來源")
        with self.assertRaises(OSError):
            service._media_task(failing_transcode)
        self.assertFalse(service.media_busy)

    def test_schema_24_upgrade_preserves_work_and_repeats_safely(self):
        journal=SemanticJobJournal(self.store);job=str(uuid4())
        journal.register(SemanticRequest(self.event,b"old-input"),job,None,60)
        self.store.connection.execute("ALTER TABLE semantic_work_jobs DROP COLUMN cancel_requested")
        self.store.connection.execute("ALTER TABLE semantic_work_jobs DROP COLUMN terminal_reason")
        self.store.connection.execute("UPDATE schema_meta SET value='24' WHERE key='schema_version'")
        self.store.connection.commit();self.store.initialize();self.store.initialize()
        row=self.store.connection.execute("SELECT cancel_requested,terminal_reason FROM semantic_work_jobs WHERE job_id=?",(job,)).fetchone()
        self.assertEqual(tuple(row),(0,None))
        self.assertEqual(self.store.connection.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0],str(SCHEMA_VERSION))
        self.assertIsNotNone(self.store.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vision_request_decisions'").fetchone())

    def test_latest_assessment_removes_replaced_and_withdrawn_segments_but_keeps_history(self):
        review=WebEventReview(self.store,self.registry);actor="a"*64
        base={"request_id":str(uuid4()),"expected_revision":0,"label":"cat","pets":[{"pet_id":"test-cat","behavior":None,"motion":"walking"}],"segment":{"start_seconds":0,"end_seconds":2}}
        review.submit(self.event,actor,base)
        revised={**base,"request_id":str(uuid4()),"expected_revision":1,"supersedes_revision":1,"segment":{"start_seconds":1,"end_seconds":3}}
        review.submit(self.event,actor,revised)
        view=review.view(self.event,actor);self.assertEqual([row["revision"] for row in view["assessment"]["human_review"]["active_segments"]],[2])
        review.submit(self.event,actor,{**revised,"request_id":str(uuid4()),"expected_revision":2,"supersedes_revision":2,"withdrawn":True})
        view=review.view(self.event,actor);self.assertEqual(view["assessment"]["human_review"]["active_segments"],[]);self.assertEqual(len(view["history"]),3)
        self.store.record_event_semantics(self.event,SemanticResult("completed",(("scene",("walking",)),),job_id=str(uuid4())))
        self.store.record_event_semantics(self.event,SemanticResult("cancelled",job_id=str(uuid4()),request_dispatched=False))
        view=review.view(self.event,actor)
        self.assertEqual(view["semantic_candidates"]["status"],"completed")
        self.assertEqual(view["assessment"]["analysis"]["revisions"][-1]["result"]["status"],"cancelled")

    def test_pet_crop_endpoint_rebuilds_gallery_preserves_original_and_rejects_tiny_crop(self):
        path=self.registry.add_reference_image("test-cat",self.jpeg)
        url=f"/api/v1/pets/test-cat/images/{path.name}/crop"
        body={"confirm_single_cat":True,"polygon":[[.1,.1],[.8,.1],[.7,.8],[.1,.7]]}
        embedder=SimpleNamespace(model_version="test-only",embed=lambda _:np.ones(8,dtype=np.float32))
        with TestClient(self.app()) as client,patch("meowkeeper.core.identity.dinov2_embedder.shared_cpu_embedder",return_value=embedder):
            self.assertEqual(client.post(url,json=body).status_code,401)
            self.assertEqual(client.post(url,json={**body,"confirm_single_cat":False},headers=self.headers).status_code,428)
            response=client.post(url,json=body,headers=self.headers);self.assertEqual(response.status_code,200,response.text)
            profile=self.registry.get_pet("test-cat");self.assertEqual(profile.embedding_count,1)
            self.assertEqual((self.root/"pets"/"test-cat"/"reference_originals"/path.name).read_bytes(),self.jpeg)
            new_id=Path(profile.reference_images[0]).name
            tiny={**body,"polygon":[[.1,.1],[.11,.1],[.11,.11],[.1,.11]]}
            self.assertEqual(client.post(f"/api/v1/pets/test-cat/images/{new_id}/crop",json=tiny,headers=self.headers).status_code,422)

    def test_position_binding_requires_real_readback_correct_source_and_visual_alignment(self):
        scenes=SceneViewRegistry(self.store);view=scenes.register(self.source,"合成參考圖",self.jpeg,0)
        device=["local-device"];moves=[]
        def read(_device):
            return {"success":True,"position_readback_verified":True,"state":{"connected":True,"position":{"ret":0,"angle":30,"elevation":50,"updated_at":datetime.now(timezone.utc).isoformat()}}}
        adapter=SimpleNamespace(device=lambda:device[0],read=read,move=lambda _device,**pose:moves.append(pose) or {"success":True})
        with TestClient(self.app(integration=SimpleNamespace(privacy_adapter=adapter))) as client:
            url=f"/api/v1/scene-registry/views/{view}/position"
            response=client.post(url,json={"expected_epoch":0,"preset_label":"食盆方向"},headers=self.headers);self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(moves,[])
            self.assertEqual(client.post(f"/api/v1/scene-registry/views/{view}/restore-position",headers=self.headers).status_code,200)
            self.assertEqual(moves,[{"angle":30,"elevation":50}])
            device[0]="other-device"
            self.assertEqual(client.post(f"/api/v1/scene-registry/views/{view}/restore-position",headers=self.headers).status_code,422)
            self.assertEqual(len(moves),1)
            adapter.read=lambda _: {"success":False}
            self.assertEqual(client.post(url,json={"expected_epoch":1,"preset_label":"不可捏造"},headers=self.headers).status_code,422)
        device[0]="local-device";adapter.read=read
        with TestClient(self.app(callbacks={"preview_snapshot":lambda:self.frame},integration=SimpleNamespace(privacy_adapter=adapter))) as client:
            response=client.post(f"/api/v1/scene-registry/views/{view}/restore-position",headers=self.headers)
            self.assertEqual(response.status_code,422)
            self.assertEqual(scenes.state(self.source)["state"],"unverified")


if __name__=="__main__":unittest.main()
