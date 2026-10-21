"""合成背景對位與視角/物件版本；不冒充實際PTZ電機驗收。"""

import hashlib
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from meowkeeper.app.web import CoreStateFacade, create_app
from meowkeeper.core.behavior.models import ZoneDefinition
from meowkeeper.core.behavior.scene_views import SceneViewRegistry, visual_alignment
from meowkeeper.storage.database import SQLiteStore


class SceneViewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = SQLiteStore(self.root / "events.db")
        self.addCleanup(self.store.close)
        self.scenes = SceneViewRegistry(self.store)
        self.source = "source-"+"a"*24
        pixels = np.random.default_rng(42).integers(0, 256, (480, 640, 3), dtype=np.uint8)
        target = BytesIO()
        Image.fromarray(pixels).save(target, format="JPEG", quality=90)
        self.jpeg = target.getvalue()

    def test_legacy_zones_stay_preserved_but_not_used_until_calibration(self):
        self.store.replace_source_zones(self.source, [ZoneDefinition.rectangle("water", "水碗", "water", .1, .1, .3, .3)])
        self.assertEqual(self.scenes.state(self.source)["state"], "legacy_fixed_view_unverified")
        self.assertEqual(self.scenes.aligned_zones(self.source, self.jpeg), ())
        self.scenes.register(self.source, "固定背景", self.jpeg, 0)
        self.assertEqual(self.scenes.aligned_zones(self.source, self.jpeg)[0].zone_id, "water")
        self.scenes.invalidate(self.source)
        self.assertEqual(self.scenes.aligned_zones(self.source, self.jpeg), ())
        self.assertEqual(len(self.store.load_source_zones(self.source)), 1)
        self.assertEqual(self.scenes.state(self.source)["epoch"], 1)
        view = self.scenes.list_views(self.source)[0]["view_id"]
        self.scenes.align(self.source, view, self.jpeg, 1)
        self.assertEqual(self.scenes.state(self.source)["state"], "aligned")
        with self.assertRaises(ValueError):
            self.scenes.register(self.source, "舊版本", self.jpeg, 0)

    def test_background_shift_and_insufficient_features_fail_closed(self):
        self.assertTrue(visual_alignment(self.jpeg, self.jpeg))
        with Image.open(BytesIO(self.jpeg)) as image:
            shifted = Image.new("RGB", image.size)
            shifted.paste(image, (40, 0))
            out = BytesIO()
            shifted.save(out, format="JPEG")
        self.assertFalse(visual_alignment(self.jpeg, out.getvalue()))
        self.scenes.register(self.source, "背景", self.jpeg, 0)
        self.assertEqual(self.scenes.aligned_zones(self.source, out.getvalue()), ())
        self.assertEqual(self.scenes.state(self.source)["reason"], "alignment_lost")
        plain = BytesIO()
        Image.new("RGB", (640, 480), "white").save(plain, format="JPEG")
        with self.assertRaises(ValueError):
            self.scenes.register(self.source, "無特徵", plain.getvalue(), 1)

    def test_object_revision_disable_and_wrong_view_protection(self):
        view = self.scenes.register(self.source, "背景", self.jpeg, 0)
        payload = {"object_id":None, "view_id":view, "expected_revision":0, "name":"食盆", "kind":"food",
                   "polygon":[[.1,.1],[.3,.1],[.3,.3],[.1,.3]], "enabled":True}
        result = self.scenes.save_object(self.source, payload)
        for changes in ({"logical_object_id":123},{"polygon":[[.1,.1],[.2,.2],[.3,.3]]}):
            with self.assertRaises((ValueError,TypeError)):
                self.scenes.save_object(self.source,{**payload,**changes})
        self.assertEqual(self.scenes.state(self.source)["state"], "unverified")
        self.assertEqual(self.scenes.aligned_zones(self.source, self.jpeg), ())
        self.scenes.align(self.source, view, self.jpeg, 1)
        self.assertTrue(any(zone.zone_type == "food" for zone in self.scenes.aligned_zones(self.source, self.jpeg)))
        self.scenes.save_object(self.source, {**payload, "object_id":result["object_id"], "expected_revision":1, "enabled":False})
        self.scenes.align(self.source, view, self.jpeg, 2)
        self.assertFalse(self.scenes.aligned_zones(self.source, self.jpeg)[0].enabled)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM scene_object_history").fetchone()[0], 2)
        with self.assertRaises(ValueError):
            self.scenes.save_object(self.source, {**payload, "object_id":result["object_id"], "expected_revision":1})
        with self.assertRaises(ValueError):
            self.scenes.save_object(self.source, {**payload, "view_id":str(uuid4())})
        self.store.initialize()
        self.assertEqual(len(self.scenes.objects(self.source)), 1)

    def test_reference_collage_uses_only_enabled_local_object_crops(self):
        view = self.scenes.register(self.source, "客廳", self.jpeg, 0, verified_current=False)
        payload = {"object_id":None, "view_id":view, "expected_revision":0, "name":"飯碗", "kind":"food",
                   "polygon":[[.1,.1],[.4,.1],[.4,.4],[.1,.4]], "enabled":True}
        self.scenes.save_object(self.source, payload)
        self.scenes.save_object(self.source, {**payload, "name":"隱私區", "kind":"excluded",
                                               "polygon":[[.5,.5],[.8,.5],[.8,.8],[.5,.8]]})
        collage, labels = self.scenes.reference_context(self.source)
        self.assertLessEqual(len(collage), 512 * 1024)
        self.assertEqual(labels, ("Reference 01: 食物／飯碗; view: 客廳; user label: 飯碗",))
        self.assertNotIn("隱私區", " ".join(labels))
        with Image.open(BytesIO(collage)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertLessEqual(image.width, 1280)
        self.assertEqual(len(self.scenes.reference_revision(self.source)), 2)
        food = next(row for row in self.scenes.objects(self.source) if row["kind"] == "food")
        self.scenes.save_object(self.source, {**payload, "object_id":food["object_id"],
                                               "expected_revision":1, "enabled":False})
        self.assertIsNone(self.scenes.reference_context(self.source))

    def test_cross_view_object_link_requires_same_source_and_kind(self):
        view = self.scenes.register(self.source, "背景一", self.jpeg, 0)
        payload = {"object_id":None,"view_id":view,"expected_revision":0,"name":"飲水機","kind":"water",
                   "polygon":[[.1,.1],[.3,.1],[.3,.3],[.1,.3]],"enabled":True}
        first = self.scenes.save_object(self.source, payload)["object_id"]
        second_view = self.scenes.register(self.source, "背景二", self.jpeg, 1)
        second = self.scenes.save_object(self.source, {**payload,"view_id":second_view,"logical_object_id":first})
        objects = self.scenes.objects(self.source)
        self.assertEqual({row["logical_object_id"] for row in objects}, {first})
        with self.assertRaises(ValueError):
            self.scenes.save_object(self.source, {**payload,"view_id":second_view,"kind":"food","logical_object_id":first})
        with self.assertRaises(ValueError):
            self.scenes.save_object(self.source, {**payload,"view_id":second_view,"object_id":second["object_id"],"expected_revision":1})
        other = "source-"+"b"*24
        other_view = self.scenes.register(other,"另一攝影機",self.jpeg,0)
        with self.assertRaises(ValueError):
            self.scenes.save_object(other,{**payload,"view_id":other_view,"logical_object_id":first})

    def test_reference_and_writes_require_login_csrf_and_fresh_frame(self):
        path = self.root / "shared.jpg"
        path.write_bytes(self.jpeg)
        token = "v"*32
        facade = CoreStateFacade(store=self.store, callbacks={"behavior_zones":lambda:{"source_ref":self.source}, "preview_snapshot":lambda:path})
        app = create_app(facade=facade, token=token)
        payload = {"source_ref":self.source,"name":"視角","expected_epoch":0,"confirm_current_frame":True}
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/v1/scene-registry").status_code, 401)
            self.assertEqual(client.get("/api/v1/scene-registry/reference-collage").status_code, 401)
            headers = {"Authorization":"Bearer "+token}
            self.assertEqual(client.post("/api/v1/scene-registry/views",json=payload,headers=headers).status_code, 403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token+":csrf").encode()).hexdigest()
            response = client.post("/api/v1/scene-registry/views",json=payload,headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            url = f"/api/v1/scene-registry/views/{response.json()['view_id']}/reference"
            self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(client.get(url,headers=headers).headers["content-type"], "image/jpeg")
            object_payload = {"object_id":None,"view_id":response.json()["view_id"],"expected_revision":0,
                              "name":"水盆","kind":"water","polygon":[[.1,.1],[.4,.1],[.4,.4],[.1,.4]],"enabled":True}
            saved = client.post("/api/v1/scene-registry/objects",json=object_payload,headers=headers)
            self.assertEqual(saved.status_code,200,saved.text)
            collage = client.get("/api/v1/scene-registry/reference-collage",headers=headers)
            self.assertEqual(collage.status_code,200,collage.text)
            self.assertEqual(collage.headers["content-type"],"image/jpeg")
            self.assertEqual(collage.headers["x-scene-reference-count"],"1")
            path.unlink()
            self.assertEqual(client.post("/api/v1/scene-registry/views",json=payload,headers=headers).status_code, 422)


    def test_uploaded_reference_remains_local_unverified_until_real_frame_alignment(self):
        token = "u"*32
        facade = CoreStateFacade(store=self.store,callbacks={"behavior_zones":lambda:{"source_ref":self.source}})
        app = create_app(facade=facade,token=token)
        url = "/api/v1/scene-registry/views/upload"
        headers = {"Authorization":"Bearer "+token}
        form = {"name":"上傳飲水機參考","expected_epoch":"0"}
        with TestClient(app) as client:
            self.assertEqual(client.post(url,data=form,files={"image":("ref.jpg",self.jpeg,"image/jpeg")}).status_code,401)
            self.assertEqual(client.post(url,data=form,files={"image":("ref.jpg",self.jpeg,"image/jpeg")},headers=headers).status_code,403)
            headers["X-MeowKeeper-CSRF"] = hashlib.sha256((token+":csrf").encode()).hexdigest()
            response = client.post(url,data=form,files={"image":("ref.jpg",self.jpeg,"image/jpeg")},headers=headers)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()["status"],"saved_unverified")
            self.assertEqual(self.scenes.state(self.source)["state"],"unverified")
            self.assertIsNone(self.scenes.state(self.source)["active_view"])
            self.assertEqual(self.scenes.aligned_zones(self.source,self.jpeg),())
            view = response.json()["view_id"]
            self.scenes.align(self.source,view,self.jpeg,0)
            self.assertEqual(self.scenes.state(self.source)["state"],"aligned")
            for content in (b"<svg>not a photograph</svg>",b"x"*(4*1024*1024+1)):
                result = client.post(url,data=form,files={"image":("ref.jpg",content,"image/jpeg")},headers=headers)
                self.assertEqual(result.status_code,422)
            reference = client.get(f"/api/v1/scene-registry/views/{view}/reference",headers=headers)
            with Image.open(BytesIO(reference.content)) as image:
                self.assertEqual(image.format,"JPEG")
                self.assertFalse(image.getexif())


if __name__ == "__main__":
    unittest.main()
