"""Authenticated Web event review; append-only and separate from Telegram identity."""

from __future__ import annotations

import json
import math
from uuid import UUID

from meowkeeper.core.notifications.pet_feedback_wizard import BEHAVIORS, MOTIONS
from meowkeeper.core.notifications.track_tag_feedback import FEEDBACK_TAGS
from meowkeeper.storage.database import SQLiteStore


class ReviewConflict(ValueError):
    pass


class WebEventReview:
    def __init__(self, store: SQLiteStore, registry) -> None:
        self.store, self.registry = store, registry

    def _event(self, event_id):
        event = self.store.get_media_event(event_id)
        if event is None or event["event_type"] != "cat_detected" or event["trigger_origin"] != "detector":
            raise ValueError("此事件不是可人工校正的模型貓咪事件。")
        return event

    def view(self, event_id, reviewer_ref):
        event = self._event(event_id)
        self.store._validate_opaque_ref(reviewer_ref)
        with self.store._connection_lock:
            rows = list(self.store.connection.execute(
                "SELECT revision,label,pets_json,scope_json,created_at FROM web_media_reviews "
                "WHERE event_id=? AND reviewer_ref=? ORDER BY revision", (event_id, reviewer_ref)))
            telegram_rows = list(self.store.connection.execute(
                "SELECT label,received_at FROM media_event_feedback_history WHERE event_id=? ORDER BY received_at",
                (event_id,)))
            telegram_pets = list(self.store.connection.execute(
                "SELECT h.pet_id,h.behavior,h.motion,h.created_at FROM telegram_pet_review_history h "
                "JOIN telegram_pet_reviews r ON h.review_id=r.review_id WHERE r.event_id=? ORDER BY h.created_at",
                (event_id,)))
            telegram_tags = list(self.store.connection.execute(
                "SELECT d.identity_snapshot,d.name_snapshot,d.selected_json,h.created_at,e.track_id,d.reviewer_ref,d.review_id "
                "FROM telegram_track_tag_drafts d JOIN telegram_track_tag_history h ON h.review_id=d.review_id "
                "JOIN behavior_events e ON e.event_id=d.event_id "
                "JOIN media_event_tracks mt ON mt.track_id=e.track_id "
                "WHERE mt.event_id=? AND h.state='submitted' ORDER BY h.created_at,h.rowid", (event_id,)))
            labels = {row[0] for row in self.store.connection.execute(
                "SELECT label FROM media_event_feedback WHERE event_id=? UNION "
                "SELECT w.label FROM web_media_reviews w WHERE w.event_id=? AND json_extract(w.scope_json,'$.segment') IS NULL AND w.revision="
                "(SELECT MAX(revision) FROM web_media_reviews WHERE event_id=w.event_id AND reviewer_ref=w.reviewer_ref AND json_extract(scope_json,'$.segment') IS NULL)",
                (event_id,event_id))}
        pets = self.registry.list_pets(enabled_only=True)
        from meowkeeper.storage.semantic_jobs import SemanticJobJournal
        active_rows = [row for row in rows if row["revision"] not in {
            json.loads(new["scope_json"]).get("supersedes_revision") for new in rows}
            and not json.loads(row["scope_json"]).get("withdrawn")]
        semantic_history=self.store.event_semantic_history(event_id)
        latest_candidate=next((row["result"] for row in reversed(semantic_history) if row["result"].get("status")=="completed"),self.store.event_semantics(event_id))
        delivery_history=self.store.event_delivery_history(event_id)
        jobs=SemanticJobJournal(self.store).list_event(event_id)
        with self.store._connection_lock:
            track_ids=[row[0] for row in self.store.connection.execute("SELECT track_id FROM media_event_tracks WHERE event_id=? ORDER BY track_id LIMIT 8",(event_id,))]
        identity=self.store.identity_evidence(event_id)
        candidate_reviews=self.candidate_history(event_id,reviewer_ref)
        latest_telegram={}
        for row in telegram_tags:
            latest_telegram[(row["reviewer_ref"],row["track_id"])]=row
        return {"event_id": event_id, "revision": rows[-1]["revision"] if rows else 0,
                "assessment":{"version":1,"event_id":event_id,"created_at":event["trigger_at"],
                              "media":{"state":event["clip_status"],"duration_seconds":event["actual_duration_seconds"]},
                              "tracks":[{"track_ref":track,"identity_evidence":[entry for entry in identity if entry.get("track_id")==track],
                                          "identity_state":"experimental_evidence" if any(entry.get("track_id")==track for entry in identity) else "unknown",
                                          "unknown_reason":None if any(entry.get("track_id")==track for entry in identity) else "no_bound_identity_evidence"} for track in track_ids],
                              "analysis":{"jobs":jobs,"revisions":semantic_history,"latest_candidate":latest_candidate,"human_candidate_reviews":candidate_reviews},
                              "notifications":delivery_history,
                              "human_review":{"status":"reviewed" if active_rows or latest_telegram else "not_reviewed",
                                              "telegram_current":[{"track_ref":row["track_id"],"identity":row["identity_snapshot"],
                                                                   "name":row["name_snapshot"],"tags":json.loads(row["selected_json"]),"created_at":row["created_at"]} for row in latest_telegram.values()],
                                              "active_segments":[{"revision":row["revision"],"label":row["label"],"pets":json.loads(row["pets_json"]),
                                                                  "segment":json.loads(row["scope_json"])["segment"]} for row in active_rows if "segment" in json.loads(row["scope_json"])],
                                              "latest_video_review":next(({"revision":row["revision"],"label":row["label"],"pets":json.loads(row["pets_json"])} for row in reversed(active_rows) if "segment" not in json.loads(row["scope_json"])),None)},
                              "candidate_status":"ai_candidate","identity_status":"experimental",
                              "message_zh_tw":"目前有效回報排除已修訂或撤回片段；原候選與所有修訂仍保留。這不是自動重訓模型。"},
                "analysis_jobs": jobs,
                "candidate_reviews": candidate_reviews,
                "validity_history": self.validity_history(event_id, reviewer_ref),
                "conflict": len(labels) > 1,
                "pets": [{"pet_id": p.pet_id, "name": p.display_name} for p in pets],
                "behaviors": BEHAVIORS, "motions": MOTIONS,
                "tags": FEEDBACK_TAGS, "semantic_candidates": latest_candidate,
                "semantic_history": semantic_history,
                "delivery_history": delivery_history,
                "history": [{"channel": "Web", "revision": row["revision"], "label": row["label"],
                             "pets": json.loads(row["pets_json"]), "scope": json.loads(row["scope_json"]),
                             "created_at": row["created_at"]} for row in rows],
                "telegram_history": [{"channel": "Telegram", "label": row["label"],
                                      "created_at": row["received_at"]} for row in telegram_rows],
                "telegram_pet_history": [dict(row) for row in telegram_pets],
                "telegram_tag_history": [{"channel": "Telegram", "label": "not_cat" if row["identity_snapshot"] == "not_cat" else "cat", "created_at": row["created_at"],
                                          "pets": [{"pet_id": row["identity_snapshot"] or "unknown", "name": row["name_snapshot"] or "未確認身分（舊版回報）",
                                                    "tags": json.loads(row["selected_json"])}],
                                          "scope": {"track_id": row["track_id"], "time_quality": "video_level"}}
                                         for row in telegram_tags]}

    def candidate_history(self, event_id, reviewer_ref):
        with self.store._connection_lock:
            return [dict(row) for row in self.store.connection.execute(
                "SELECT revision,analysis_revision,track_id,tag_code,decision,created_at FROM web_semantic_reviews WHERE event_id=? AND reviewer_ref=? ORDER BY revision",
                (event_id, reviewer_ref))]

    def validity_history(self, event_id, reviewer_ref):
        with self.store._connection_lock:
            return [dict(row) for row in self.store.connection.execute("SELECT revision,validity,created_at FROM web_event_validity_reviews WHERE event_id=? AND reviewer_ref=? ORDER BY revision",(event_id,reviewer_ref))]

    def review_validity(self, event_id, reviewer_ref, payload):
        self._event(event_id)
        self.store._validate_opaque_ref(reviewer_ref)
        if (not isinstance(payload,dict) or set(payload) != {"request_id","expected_revision","validity"}
                or type(payload["expected_revision"]) is not int or payload["expected_revision"] < 0
                or payload["validity"] not in {"valid","invalid","unclear"}):
            raise ValueError("事件有效性覆核格式不正確。")
        request_id = str(UUID(payload["request_id"]))
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                prior = self.store.connection.execute("SELECT event_id,reviewer_ref,validity,revision FROM web_event_validity_reviews WHERE request_id=?",(request_id,)).fetchone()
                if prior:
                    if tuple(prior[:3]) != (event_id,reviewer_ref,payload["validity"]):
                        raise ReviewConflict("此提交識別已用於其他覆核。")
                    return {"status":"saved","revision":prior[3]}
                latest = self.store.connection.execute("SELECT COALESCE(MAX(revision),0) FROM web_event_validity_reviews WHERE event_id=? AND reviewer_ref=?",(event_id,reviewer_ref)).fetchone()[0]
                if latest != payload["expected_revision"]:
                    raise ReviewConflict("事件有效性已更新，請重新載入。")
                self.store.connection.execute("INSERT INTO web_event_validity_reviews(request_id,event_id,reviewer_ref,revision,validity) VALUES(?,?,?,?,?)",(request_id,event_id,reviewer_ref,latest+1,payload["validity"]))
                return {"status":"saved","revision":latest+1}
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(operation)

    def review_candidate(self, event_id, reviewer_ref, payload):
        self._event(event_id)
        self.store._validate_opaque_ref(reviewer_ref)
        fields = {"request_id", "expected_revision", "analysis_revision", "track_id", "tag_code", "decision"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("候選覆核欄位不正確。")
        request_id = str(UUID(str(payload["request_id"])))
        if any(type(payload[key]) is not int or payload[key] < 0 for key in ("expected_revision", "analysis_revision")):
            raise ValueError("候選覆核版本不正確。")
        if payload["decision"] not in {"accepted", "rejected"}:
            raise ValueError("請選擇接受或駁回候選。")
        def operation():
            try:
                self.store.connection.execute("BEGIN IMMEDIATE")
                prior = self.store.connection.execute("SELECT * FROM web_semantic_reviews WHERE request_id=?", (request_id,)).fetchone()
                if prior:
                    if any(prior[key] != payload[key] for key in ("analysis_revision", "track_id", "tag_code", "decision")) or prior["event_id"] != event_id or prior["reviewer_ref"] != reviewer_ref:
                        raise ReviewConflict("此提交識別已用於不同覆核。")
                    return {"status": "saved", "revision": prior["revision"]}
                latest = self.store.connection.execute("SELECT COALESCE(MAX(revision),0) FROM web_semantic_reviews WHERE event_id=? AND reviewer_ref=?", (event_id, reviewer_ref)).fetchone()[0]
                if payload["expected_revision"] != latest:
                    raise ReviewConflict("覆核已更新，請重新載入後再確認。")
                row = self.store.connection.execute("SELECT candidate_json FROM event_semantic_revisions WHERE event_id=? AND revision=? AND status='completed'", (event_id, payload["analysis_revision"])).fetchone()
                result = json.loads(row[0]) if row else {}
                if not any(track.get("track_id") == payload["track_id"] and payload["tag_code"] in track.get("tags", []) for track in result.get("tracks", [])):
                    raise ValueError("此標籤不是該事件、軌跡與分析版本的候選。")
                self.store.connection.execute("INSERT INTO web_semantic_reviews(request_id,event_id,reviewer_ref,revision,analysis_revision,track_id,tag_code,decision) VALUES(?,?,?,?,?,?,?,?)",
                    (request_id, event_id, reviewer_ref, latest+1, payload["analysis_revision"], payload["track_id"], payload["tag_code"], payload["decision"]))
                return {"status": "saved", "revision": latest+1}
            except Exception:
                self.store.connection.rollback()
                raise
        return self.store._transaction(operation)

    def submit(self, event_id, reviewer_ref, payload):
        self.store._validate_opaque_ref(reviewer_ref)
        required = {"request_id", "expected_revision", "label", "pets"}
        if not required.issubset(payload) or set(payload) - required - {"track_ref", "use_for_learning", "segment", "supersedes_revision", "withdrawn"}:
            raise ValueError("回報欄位不完整或包含未支援欄位。")
        supersedes = payload.get("supersedes_revision")
        withdrawn = payload.get("withdrawn", False)
        if (type(withdrawn) is not bool or supersedes is not None and (type(supersedes) is not int or supersedes < 1)
                or withdrawn and supersedes is None):
            raise ValueError("片段修訂或撤回格式不正確。")
        request_id = str(UUID(str(payload["request_id"])))
        expected = payload["expected_revision"]
        if type(expected) is not int or expected < 0:
            raise ValueError("回報修訂版本不正確。")
        label, selected = payload["label"], payload["pets"]
        scope = payload.get("track_ref")
        consent = payload.get("use_for_learning", False)
        segment = payload.get("segment")
        normalized_segment = None
        if segment is not None:
            event = self._event(event_id)
            if not isinstance(segment, dict) or set(segment) != {"start_seconds", "end_seconds"}:
                raise ValueError("影片片段範圍格式不正確。")
            start, end = segment["start_seconds"], segment["end_seconds"]
            duration = event["actual_duration_seconds"]
            if (type(start) not in {int, float} or type(end) not in {int, float}
                    or not math.isfinite(start) or not math.isfinite(end)
                    or type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0
                    or not 0 <= start < end <= float(duration) + .25):
                raise ValueError("請選擇影片時長內的有效片段範圍。")
            normalized_segment = {"start_seconds": float(start), "end_seconds": float(end)}
        if type(consent) is not bool or scope is not None and not isinstance(scope, str):
            raise ValueError("軌跡或特徵學習同意格式不合法。")
        if consent and (scope is None or label != "cat"):
            raise ValueError("只有明確對應的單隻貓軌跡才能同意特徵學習。")
        if scope is not None:
            if scope not in {item["behavior_event_id"] for item in self.store.identity_evidence(event_id)}:
                raise ValueError("軌跡不屬於此事件的已保存證據。")
            if label == "cat" and (not isinstance(selected, list) or len(selected) > 1):
                raise ValueError("一條軌跡只可指定一隻已登錄貓咪。")
            if consent:
                if not selected:
                    raise ValueError("其他貓不能加入已登錄個體的特徵庫。")
                with self.store._connection_lock:
                    crop = self.store.connection.execute("SELECT 1 FROM identity_feedback_evidence WHERE event_id=?", (scope,)).fetchone()
                if crop is None:
                    raise ValueError("此軌跡裁切已清理或未保存，不能加入特徵庫。")
        if label not in {"cat", "not_cat"} or not isinstance(selected, list):
            raise ValueError("請先確認貓咪或非貓。")
        if label == "not_cat" and selected:
            raise ValueError("非貓事件不可同時指定貓咪。")
        if label == "cat" and not 1 <= len(selected) <= 8 and not (scope is not None and selected == []):
            raise ValueError("請選擇 1～8 隻已登錄貓咪。")
        valid_pets = {p.pet_id for p in self.registry.list_pets(enabled_only=True)}
        seen, normalized = set(), []
        for item in selected:
            if (not isinstance(item, dict) or not {"pet_id", "behavior", "motion"}.issubset(item)
                    or set(item) - {"pet_id", "behavior", "motion", "tags"}):
                raise ValueError("逐貓回報欄位不正確。")
            pet_id, behavior, motion = item["pet_id"], item["behavior"], item["motion"]
            tags = item.get("tags", [])
            if (not isinstance(tags, list) or len(tags) > len(FEEDBACK_TAGS)
                    or any(not isinstance(tag, str) or tag not in FEEDBACK_TAGS for tag in tags)
                    or "unknown" in tags and len(tags) != 1):
                raise ValueError("動作多選欄位不正確。")
            if not isinstance(pet_id, str) or pet_id not in valid_pets or pet_id in seen:
                raise ValueError("寵物已變更或重複，請重新選擇。")
            if behavior not in BEHAVIORS and behavior is not None:
                raise ValueError("行為選項不正確。")
            if motion not in MOTIONS and motion is not None:
                raise ValueError("動作選項不正確。")
            if behavior is None and motion is None and not tags and scope is None:
                raise ValueError("每隻貓至少須選擇一項行為或動作。")
            seen.add(pet_id); normalized.append({"pet_id": pet_id, "behavior": behavior, "motion": motion})
            if "tags" in item:
                normalized[-1]["tags"] = list(dict.fromkeys(tags))

        def operation():
            self._event(event_id)
            previous = self.store.connection.execute(
                "SELECT event_id,reviewer_ref,revision,label,pets_json,scope_json FROM web_media_reviews WHERE request_id=?",
                (request_id,)).fetchone()
            serialized = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
            scope_value = {"track_ref": scope, "use_for_learning": consent} if scope else {}
            if normalized_segment is not None:
                scope_value["segment"] = normalized_segment
            if supersedes is not None:
                prior = self.store.connection.execute("SELECT scope_json FROM web_media_reviews WHERE event_id=? AND reviewer_ref=? AND revision=?", (event_id, reviewer_ref, supersedes)).fetchone()
                if prior is None or "segment" not in json.loads(prior[0]) or scope is not None:
                    raise ValueError("只能修改或撤回自己已保存的影片片段。")
                used = self.store.connection.execute("SELECT 1 FROM web_media_reviews WHERE event_id=? AND reviewer_ref=? AND json_extract(scope_json,'$.supersedes_revision')=? LIMIT 1", (event_id, reviewer_ref, supersedes)).fetchone()
                if used and previous is None:
                    raise ReviewConflict("此片段已被修訂或撤回，請重新載入並選擇最新版。")
                scope_value.update(supersedes_revision=supersedes, withdrawn=withdrawn)
            scope_json = json.dumps(scope_value, sort_keys=True)
            if previous:
                if (previous["event_id"], previous["reviewer_ref"], previous["label"], previous["pets_json"]) != (
                    event_id, reviewer_ref, label, serialized):
                    raise ReviewConflict("重試識別已用於其他回報，請重新載入。")
                if previous["scope_json"] != scope_json:
                    raise ReviewConflict("此重試識別已用於不同軌跡或學習同意。")
                return {"status": "duplicate", "revision": previous["revision"]}
            latest = self.store.connection.execute(
                "SELECT COALESCE(MAX(revision),0) FROM web_media_reviews WHERE event_id=? AND reviewer_ref=?",
                (event_id, reviewer_ref)).fetchone()[0]
            if latest != expected:
                raise ReviewConflict("此事件已有較新的回報；請重新載入，歷史未被覆寫。")
            self.store.connection.execute(
                "INSERT INTO web_media_reviews(request_id,event_id,reviewer_ref,revision,label,pets_json,scope_json) VALUES (?,?,?,?,?,?,?)",
                (request_id,event_id,reviewer_ref,latest+1,label,serialized,scope_json))
            if scope is not None:
                labels = [("cat_presence", label)]
                if label == "cat":
                    labels.append(("identity", normalized[0]["pet_id"] if normalized else "unknown"))
                for dimension, value in labels:
                    self.store.connection.execute(
                        """INSERT INTO behavior_event_annotations(event_id,reviewer_ref,dimension,label,learning_consent)
                           VALUES (?,?,?,?,?) ON CONFLICT(event_id,reviewer_ref,dimension)
                           DO UPDATE SET label=excluded.label,learning_consent=excluded.learning_consent,updated_at=CURRENT_TIMESTAMP""",
                        (scope, reviewer_ref, dimension, value, int(consent if dimension == "identity" else False)))
                if label == "not_cat":
                    self.store.connection.execute(
                        "DELETE FROM behavior_event_annotations WHERE event_id=? AND reviewer_ref=? AND dimension IN ('identity','behavior','motion')",
                        (scope, reviewer_ref))
            return {"status": "saved", "revision": latest+1}
        return self.store._transaction(operation)
