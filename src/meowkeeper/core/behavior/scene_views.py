"""視角與物件本機登錄；位置指令成功不是對位證據。"""

import json
import unicodedata
from dataclasses import asdict
from io import BytesIO
from uuid import uuid4

from .models import ZoneDefinition


def _safe_label(value):
    cleaned = "".join(char for char in str(value) if not unicodedata.category(char).startswith("C"))
    return " ".join(cleaned.split())[:80]


def safe_reference(jpeg):
    from PIL import Image, ImageOps
    if not isinstance(jpeg, bytes) or not 1 <= len(jpeg) <= 4*1024*1024:
        raise ValueError("請使用4MB內的參考照片。")
    with Image.open(BytesIO(jpeg)) as image:
        if image.format not in {"JPEG","PNG","WEBP"} or getattr(image,"n_frames",1) != 1:
            raise ValueError("請使用單張JPG、PNG或WebP照片。")
        if image.width*image.height > 16_000_000 or min(image.size) < 96:
            raise ValueError("參考照片過小或超過安全尺寸。")
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((640, 480))
        target = BytesIO()
        image.save(target, format="JPEG", quality=85)  # 不保留EXIF位置或相機資料。
        return target.getvalue()


def visual_alignment(reference, current):
    """保守的背景幾何對位；缺特徵、位移、縮放或旋轉均不當成有效ROI。"""
    import cv2
    import numpy as np
    def image(data):
        decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if decoded is None:
            raise ValueError("畫面無法解碼。")
        return cv2.resize(decoded, (320, 240))
    first, second = image(reference), image(current)
    detector = cv2.ORB_create(nfeatures=250)
    points_a, features_a = detector.detectAndCompute(first, None)
    points_b, features_b = detector.detectAndCompute(second, None)
    if features_a is None or features_b is None:
        return False
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(features_a, features_b)
    matches = [match for match in matches if match.distance < 40]
    if len(matches) < 20:
        return False
    source = np.float32([points_a[m.queryIdx].pt for m in matches])
    target = np.float32([points_b[m.trainIdx].pt for m in matches])
    matrix, inliers = cv2.estimateAffinePartial2D(source, target, method=cv2.RANSAC, ransacReprojThreshold=1.5)
    if matrix is None or inliers is None or int(inliers.sum()) < 20 or float(inliers.mean()) < .8:
        return False
    return bool(abs(matrix[0, 0]-1) < .015 and abs(matrix[1, 1]-1) < .015
                and abs(matrix[0, 1]) < .015 and abs(matrix[1, 0]) < .015
                and abs(matrix[0, 2]) < 3 and abs(matrix[1, 2]) < 2)


class SceneViewRegistry:
    def __init__(self, store):
        self.store = store

    def reference_revision(self, source_ref):
        """Return a small cache key for the enabled, user-marked object references."""
        self.store._validate_zone_source(source_ref)
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT o.object_id,o.revision,o.enabled,o.name,o.kind,v.view_id "
                "FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id "
                "WHERE v.source_ref=? ORDER BY v.created_at,o.created_at,o.object_id",
                (source_ref,),
            ).fetchall()
        return tuple(tuple(row) for row in rows)

    def reference_context(self, source_ref, *, maximum_objects=8):
        """Build a bounded, labelled contact sheet from explicitly enabled local ROIs."""
        self.store._validate_zone_source(source_ref)
        if type(maximum_objects) is not int or not 1 <= maximum_objects <= 8:
            raise ValueError("參考拼貼項目上限不正確。")
        with self.store._connection_lock:
            rows = self.store.connection.execute(
                "SELECT o.object_id,o.name,o.kind,o.polygon_json,v.name,v.reference_jpeg "
                "FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id "
                "WHERE v.source_ref=? AND o.enabled=1 AND o.kind<>'excluded' "
                "ORDER BY v.created_at DESC,o.created_at DESC,o.object_id LIMIT ?",
                (source_ref, maximum_objects),
            ).fetchall()
        if not rows:
            return None

        from PIL import Image, ImageDraw, ImageOps

        from meowkeeper.core.identity.reference_crop import crop_reference

        kind_labels = {
            "food": "食物／飯碗", "water": "飲水／水盆", "litter_box": "貓砂／如廁區",
            "bed": "睡覺／休息區", "play_area": "玩耍區", "excluded": "排除區",
        }
        columns, rows_count = 4, (len(rows) + 3) // 4
        tile_width, tile_height = 320, 280
        sheet = Image.new("RGB", (columns * tile_width, rows_count * tile_height), "#f4ede3")
        draw = ImageDraw.Draw(sheet)
        labels = []
        for row in rows:
            _object_id, name, kind, polygon_json, view_name, reference = row
            kind_label = kind_labels.get(kind)
            if kind_label is None:
                continue
            index = len(labels) + 1
            crop = crop_reference(bytes(reference), json.loads(polygon_json), minimum=1)
            with Image.open(BytesIO(crop)) as opened:
                thumb = ImageOps.contain(opened.convert("RGB"), (tile_width - 16, tile_height - 42))
            x, y = ((index - 1) % columns) * tile_width, ((index - 1) // columns) * tile_height
            sheet.paste(thumb, (x + (tile_width - thumb.width) // 2, y + 4))
            draw.text((x + 8, y + tile_height - 30), f"Reference {index:02d}", fill="#302a24")
            # Names are sent only after the user enables the separate scene-reference
            # option and confirms upload consent; strip control characters here too.
            safe_name, safe_view = _safe_label(name), _safe_label(view_name)
            labels.append(f"Reference {index:02d}: {kind_label}; view: {safe_view}; user label: {safe_name}")
        if not labels:
            return None
        output = BytesIO()
        sheet.save(output, format="JPEG", quality=78, optimize=True)
        value = output.getvalue()
        if len(value) > 512 * 1024:
            output = BytesIO()
            sheet.thumbnail((960, 420))
            sheet.save(output, format="JPEG", quality=68, optimize=True)
            value = output.getvalue()
        if not value or len(value) > 512 * 1024:
            return None
        return value, tuple(labels)

    def state(self, source_ref):
        self.store._validate_zone_source(source_ref)
        with self.store._connection_lock:
            row = self.store.connection.execute("SELECT epoch,state,active_view,reason FROM source_view_states WHERE source_ref=?", (source_ref,)).fetchone()
        return dict(row) if row else {"epoch":0, "state":"legacy_fixed_view_unverified", "active_view":None, "reason":"legacy"}

    def invalidate(self, source_ref, reason="movement"):
        self.store._validate_zone_source(source_ref)
        if reason not in {"movement", "profile_change", "resolution_change", "alignment_lost", "privacy", "registration"}:
            raise ValueError("視角失效原因不正確。")
        self.store._write("INSERT INTO source_view_states(source_ref,epoch,state,reason) VALUES(?,1,'unverified',?) ON CONFLICT(source_ref) DO UPDATE SET epoch=epoch+1,state='unverified',reason=excluded.reason", (source_ref, reason))

    def register(self, source_ref, name, jpeg, expected_epoch, position=None, *, verified_current=True):
        self.store._validate_zone_source(source_ref)
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or type(expected_epoch) is not int:
            raise ValueError("請填入視角名稱，並重新載入目前視角版本。")
        reference = safe_reference(jpeg)
        if position is not None and (not isinstance(position, dict) or set(position) != {"angle", "elevation"}
                                     or any(type(value) is not int or not 1 <= value <= 101 for value in position.values())):
            raise ValueError("位置讀回無法驗證。")
        if verified_current and not visual_alignment(reference, reference):
            raise ValueError("此畫面缺少足夠背景特徵，不能可靠校正區域；找貓仍正常。")
        view_id = str(uuid4())
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                if self.state(source_ref)["epoch"] != expected_epoch:
                    raise ValueError("鏡頭剛有移動，請確認畫面穩定後重新登錄。")
                if self.store.connection.execute("SELECT COUNT(*) FROM scene_views WHERE source_ref=?",(source_ref,)).fetchone()[0] >= 32:
                    raise ValueError("每個來源最多登錄32個參考視角。")
                zones = [asdict(zone) for zone in self.store.load_source_zones(source_ref)] if verified_current else []
                self.store.connection.execute("INSERT INTO scene_views(view_id,source_ref,name,position_json,reference_jpeg,zones_json) VALUES(?,?,?,?,?,?)",
                                              (view_id, source_ref, name.strip(), json.dumps(position), reference, json.dumps(zones)))
                if verified_current:
                    self.store.connection.execute("INSERT INTO source_view_states(source_ref,epoch,state,active_view,reason) VALUES(?,?,'aligned',?,'registration') ON CONFLICT(source_ref) DO UPDATE SET state='aligned',active_view=excluded.active_view,reason='registration'", (source_ref, expected_epoch, view_id))
                else:
                    self.store.connection.execute("INSERT OR IGNORE INTO source_view_states(source_ref,epoch,state,reason) VALUES(?,?,'unverified','registration')",(source_ref,expected_epoch))
                return view_id
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(operation)

    def list_views(self, source_ref):
        with self.store._connection_lock:
            rows=self.store.connection.execute("SELECT view_id,name,created_at,position_json FROM scene_views WHERE source_ref=? ORDER BY created_at DESC LIMIT 32", (source_ref,)).fetchall()
        result=[]
        for row in rows:
            value=dict(row);pose=json.loads(value.pop("position_json"))
            value["position_binding"]={key:pose[key] for key in ("angle","elevation","verified_at","preset_label") if key in pose} if isinstance(pose,dict) and "device_ref" in pose else None
            result.append(value)
        return result

    def bind_position(self, source_ref, view_id, pose, device_ref, expected_epoch, preset_label=""):
        if (not isinstance(pose,dict) or set(pose)!={"angle","elevation"}
                or any(type(value) is not int or not 1<=value<=101 for value in pose.values())
                or not isinstance(preset_label,str) or len(preset_label)>80):
            raise ValueError("位置或別名無法驗證。")
        from datetime import datetime, timezone
        value={**pose,"device_ref":device_ref,"preset_label":preset_label,"verified_at":datetime.now(timezone.utc).isoformat()}
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                if self.state(source_ref)["epoch"]!=expected_epoch:
                    raise ValueError("鏡頭已變更，未保存位置。")
                cursor=self.store.connection.execute("UPDATE scene_views SET position_json=? WHERE view_id=? AND source_ref=?",(json.dumps(value),view_id,source_ref))
                if cursor.rowcount!=1:
                    raise ValueError("參考圖不屬於目前來源。")
            except Exception:
                self.store.connection.rollback()
                raise
        self.store._transaction(operation)

    def bound_position(self, source_ref, view_id, device_ref):
        with self.store._connection_lock:
            row=self.store.connection.execute("SELECT position_json FROM scene_views WHERE source_ref=? AND view_id=?",(source_ref,view_id)).fetchone()
        value=json.loads(row[0]) if row else None
        if not isinstance(value,dict) or value.get("device_ref")!=device_ref:
            raise ValueError("沒有目前攝影機的已驗證位置綁定。")
        return {key:value[key] for key in ("angle","elevation")}

    def object_crop(self, source_ref, object_id):
        with self.store._connection_lock:
            row=self.store.connection.execute("SELECT v.reference_jpeg,o.polygon_json FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id WHERE v.source_ref=? AND o.object_id=?",(source_ref,object_id)).fetchone()
        if row is None:
            raise ValueError("找不到目前來源的物件參考圖。")
        from meowkeeper.core.identity.reference_crop import crop_reference
        return crop_reference(bytes(row[0]),json.loads(row[1]),minimum=1)

    def align(self, source_ref, view_id, jpeg, expected_epoch):
        with self.store._connection_lock:
            row = self.store.connection.execute("SELECT reference_jpeg FROM scene_views WHERE source_ref=? AND view_id=?", (source_ref, view_id)).fetchone()
        if row is None or not visual_alignment(row[0], safe_reference(jpeg)):
            raise ValueError("目前背景與此參考視角不一致，未啟用區域推論。")
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                if self.state(source_ref)["epoch"] != expected_epoch:
                    raise ValueError("核對期間鏡頭有移動，請重新核對。")
                self.store.connection.execute("UPDATE source_view_states SET active_view=?,state='aligned',reason='registration' WHERE source_ref=?", (view_id, source_ref))
            except Exception:
                self.store.connection.rollback()
                raise
        self.store._transaction(operation)

    def aligned_zones(self, source_ref, jpeg):
        state = self.state(source_ref)
        if state["state"] != "aligned" or not state["active_view"]:
            return ()
        with self.store._connection_lock:
            row = self.store.connection.execute("SELECT reference_jpeg,zones_json FROM scene_views WHERE view_id=? AND source_ref=?", (state["active_view"], source_ref)).fetchone()
            objects = self.store.connection.execute("SELECT object_id,name,kind,polygon_json,enabled FROM scene_objects WHERE view_id=?", (state["active_view"],)).fetchall()
        if row is None or not visual_alignment(row[0], jpeg):
            self.invalidate(source_ref, "alignment_lost")
            return ()
        zones = [ZoneDefinition(value["zone_id"], value["name"], value["zone_type"], tuple(map(tuple, value["polygon"])), value["enabled"]) for value in json.loads(row[1])]
        zones.extend(ZoneDefinition(obj[0], obj[1], obj[2], tuple(map(tuple, json.loads(obj[3]))), bool(obj[4])) for obj in objects)
        return tuple(zones)

    def objects(self, source_ref):
        with self.store._connection_lock:
            rows = self.store.connection.execute("SELECT o.object_id,o.logical_object_id,o.view_id,o.revision,o.name,o.kind,o.polygon_json,o.enabled FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id WHERE v.source_ref=? ORDER BY o.created_at LIMIT 128", (source_ref,)).fetchall()
        return [{**dict(row), "polygon":json.loads(row["polygon_json"])} for row in rows]

    def has_excluded_regions(self, source_ref):
        with self.store._connection_lock:
            return bool(self.store.connection.execute("SELECT EXISTS(SELECT 1 FROM source_zones WHERE source_ref=? AND zone_type='excluded' AND enabled=1) OR EXISTS(SELECT 1 FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id WHERE v.source_ref=? AND o.kind='excluded' AND o.enabled=1)",(source_ref,source_ref)).fetchone()[0])

    def save_object(self, source_ref, payload):
        required = {"object_id", "view_id", "expected_revision", "name", "kind", "polygon", "enabled"}
        if not isinstance(payload, dict) or not required.issubset(payload) or set(payload)-required-{"logical_object_id"} or type(payload["expected_revision"]) is not int:
            raise ValueError("物件登錄欄位不正確。")
        if (not isinstance(payload["name"], str) or not 1 <= len(payload["name"].strip()) <= 80
                or type(payload["enabled"]) is not bool or not isinstance(payload["polygon"], list)
                or not 3 <= len(payload["polygon"]) <= 64
                or any(not isinstance(point, list) or len(point) != 2 or any(type(value) not in {int,float} for value in point) for point in payload["polygon"])):
            raise ValueError("請填入物件名稱與畫面內的有效邊界。")
        object_id = str(payload["object_id"] or uuid4())
        from uuid import UUID
        UUID(object_id)
        logical_id = payload.get("logical_object_id") or object_id
        if not isinstance(logical_id,str):
            raise TypeError("物件連結識別不正確。")
        UUID(logical_id)
        zone = ZoneDefinition(object_id, payload["name"].strip(), payload["kind"], tuple(map(tuple, payload["polygon"])), payload["enabled"])
        zone.validate()
        area = abs(sum(x*zone.polygon[(index+1)%len(zone.polygon)][1]-zone.polygon[(index+1)%len(zone.polygon)][0]*y
                       for index,(x,y) in enumerate(zone.polygon)))/2
        if area <= .000001:
            raise ValueError("請框出實際範圍，不可只選一條線或一個點。")
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                view = self.store.connection.execute("SELECT 1 FROM scene_views WHERE view_id=? AND source_ref=?", (payload["view_id"], source_ref)).fetchone()
                if not view:
                    raise ValueError("物件視角不屬於目前來源。")
                old = self.store.connection.execute("SELECT revision,view_id,COALESCE(logical_object_id,object_id) FROM scene_objects WHERE object_id=?", (object_id,)).fetchone()
                if (old and (old[1] != payload["view_id"] or old[0] != payload["expected_revision"])) or (not old and payload["expected_revision"] != 0):
                    raise ValueError("物件版本已變更，請重新載入；舊歷史保留。")
                if old and old[2] != logical_id:
                    raise ValueError("既有物件的邏輯識別不可默默改綁。")
                if logical_id != object_id:
                    linked = self.store.connection.execute("SELECT o.kind FROM scene_objects o JOIN scene_views v ON v.view_id=o.view_id WHERE v.source_ref=? AND COALESCE(o.logical_object_id,o.object_id)=? LIMIT 1",(source_ref,logical_id)).fetchone()
                    if linked is None or linked[0] != zone.zone_type:
                        raise ValueError("跨視角物件需屬於同一來源、同一物件用途。")
                if not old and self.store.connection.execute("SELECT COUNT(*) FROM scene_objects WHERE view_id=?", (payload["view_id"],)).fetchone()[0] >= 64:
                    raise ValueError("每個視角最多64個物件。")
                revision = payload["expected_revision"]+1
                self.store.connection.execute("INSERT INTO scene_objects(object_id,logical_object_id,view_id,revision,name,kind,polygon_json,enabled) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(object_id) DO UPDATE SET revision=excluded.revision,name=excluded.name,kind=excluded.kind,polygon_json=excluded.polygon_json,enabled=excluded.enabled",
                    (object_id, logical_id,payload["view_id"], revision, zone.name, zone.zone_type, json.dumps(zone.polygon), int(zone.enabled)))
                self.store.connection.execute("INSERT INTO scene_object_history(object_id,revision,value_json) VALUES(?,?,?)", (object_id, revision, json.dumps({**asdict(zone),"logical_object_id":logical_id}, ensure_ascii=False)))
                self.store.connection.execute("UPDATE source_view_states SET epoch=epoch+1,state='unverified',reason='registration' WHERE source_ref=?",(source_ref,))
                return {"object_id":object_id, "revision":revision}
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(operation)
