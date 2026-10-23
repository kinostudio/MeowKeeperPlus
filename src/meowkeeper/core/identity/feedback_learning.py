"""受控特徵庫擴充；共用身分 embedder，不重訓或修改正式參考特徵。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .color_features import extract_hsv_histogram


@dataclass(frozen=True)
class FeedbackLearningPolicy:
    interval_seconds: float = 30.0
    max_evidence: int = 512
    max_crop_bytes: int = 256 * 1024
    retention_days: int = 30
    minimum_side: int = 96
    minimum_blur_score: float = 25.0
    max_samples_per_pet: int = 100
    max_new_samples_per_run: int = 1
    duplicate_similarity: float = 0.995

    def __post_init__(self):
        if (min(self.interval_seconds, self.max_evidence, self.max_crop_bytes,
                self.retention_days, self.minimum_side, self.max_samples_per_pet,
                self.max_new_samples_per_run) <= 0
                or not 0 < self.duplicate_similarity <= 1
                or self.minimum_blur_score < 0):
            raise ValueError("回饋學習容量、間隔與品質門檻設定不合法。")


def save_track_evidence(store, event, observation, policy=None, *, observations=()):
    policy = policy or FeedbackLearningPolicy()
    crop = observation.crop_jpeg
    if (not crop or len(crop) > policy.max_crop_bytes
            or str(event.track_id) != str(observation.track_id)
            or observation.captured_at < event.started_at
            or event.ended_at is not None and observation.captured_at > event.ended_at):
        return False
    box = observation.bbox
    for other in observations:
        if other.track_id == observation.track_id:
            continue
        b = other.bbox
        margin_x = max(box.x2 - box.x1, 10.0) * 0.15
        margin_y = max(box.y2 - box.y1, 10.0) * 0.15
        if (min(box.x2 + margin_x, b.x2) > max(box.x1 - margin_x, b.x1) and
                min(box.y2 + margin_y, b.y2) > max(box.y1 - margin_y, b.y1)):
            return False  # Overlapping or tightly adjacent cats cannot establish a clean single-pet sample.
    with Image.open(io.BytesIO(crop)) as image:
        if min(image.size) < policy.minimum_side:
            return False
        image.verify()

    def save():
        store.connection.execute(
            """DELETE FROM identity_feedback_evidence
               WHERE (created_at < datetime('now', ?)
                      AND learning_status != 'eligible'
                      AND feature_json IS NULL
                      AND event_id NOT IN (
                          SELECT DISTINCT event_id FROM behavior_event_annotations
                          WHERE dimension = 'identity' AND label NOT IN ('unknown', '')
                      ))""",
            (f"-{policy.retention_days} days",),
        )
        store.connection.execute(
            "INSERT OR IGNORE INTO identity_feedback_evidence(event_id,crop_sha256,crop_jpeg,captured_at) VALUES(?,?,?,?)",
            (event.event_id, hashlib.sha256(crop).hexdigest(), crop, observation.captured_at.isoformat()),
        )
        store.connection.execute(
            """DELETE FROM identity_feedback_evidence
               WHERE learning_status != 'eligible'
                 AND feature_json IS NULL
                 AND event_id NOT IN (
                     SELECT DISTINCT event_id FROM behavior_event_annotations
                     WHERE dimension = 'identity' AND label NOT IN ('unknown', '')
                 )
                 AND event_id NOT IN (
                     SELECT e.event_id
                     FROM identity_feedback_evidence e
                     LEFT JOIN (
                         SELECT DISTINCT event_id FROM behavior_event_annotations
                         WHERE dimension = 'identity' AND label NOT IN ('unknown', '')
                     ) a ON a.event_id = e.event_id
                     ORDER BY (a.event_id IS NOT NULL OR e.learning_status = 'eligible' OR e.feature_json IS NOT NULL) DESC,
                              e.created_at DESC, e.event_id DESC
                     LIMIT ?
                 )""",
            (policy.max_evidence,),
        )
    store._transaction(save)
    return True


def upgrade_pending_learning_consent(store) -> int:
    """將已標註有效寵物身分但尚處於 awaiting_learning_consent 的軌跡轉為有效學習同意。"""
    with store._connection_lock:
        cursor = store.connection.execute(
            """UPDATE behavior_event_annotations
               SET learning_consent = 1, updated_at = CURRENT_TIMESTAMP
               WHERE dimension = 'identity'
                 AND label NOT IN ('unknown', 'not_cat', '')
                 AND learning_consent = 0"""
        )
        store.connection.commit()
        return int(cursor.rowcount or 0)


class FeedbackIdentityLearner:
    def __init__(self, store, registry, embedder, policy=None):
        self.store, self.registry, self.embedder = store, registry, embedder
        self.policy = policy or FeedbackLearningPolicy()

    def refresh(self, *, max_new_samples: int | None = None, upgrade_pending_consent: bool = False):
        """僅接受逐軌跡一致人工標籤；重新發布時撤除衝突／否定樣本。"""
        if upgrade_pending_consent:
            upgrade_pending_learning_consent(self.store)
        budget = self.policy.max_new_samples_per_run if max_new_samples is None else max(1, int(max_new_samples))
        self.store._write(
            """DELETE FROM identity_feedback_evidence
               WHERE (created_at < datetime('now', ?)
                      AND learning_status != 'eligible'
                      AND feature_json IS NULL
                      AND event_id NOT IN (
                          SELECT DISTINCT event_id FROM behavior_event_annotations
                          WHERE dimension = 'identity' AND label NOT IN ('unknown', '')
                      ))""",
            (f"-{self.policy.retention_days} days",),
        )
        with self.store._connection_lock:
            evidence = self.store.connection.execute("SELECT * FROM identity_feedback_evidence ORDER BY created_at DESC").fetchall()
            labels = self.store.connection.execute("SELECT a.event_id,a.reviewer_ref,a.dimension,a.label,a.learning_consent FROM behavior_event_annotations a JOIN identity_feedback_evidence e USING(event_id)").fetchall()
        by_event = {}
        for row in labels:
            by_event.setdefault(row["event_id"], {}).setdefault(row["reviewer_ref"], {})[row["dimension"]] = row["label"]
            if row["dimension"] == "identity":
                by_event[row["event_id"]][row["reviewer_ref"]]["learning_consent"] = bool(row["learning_consent"])
        galleries = {p.pet_id: [] for p in self.registry.list_pets(enabled_only=True)}
        new_count = 0
        for item in evidence:
            reviews = list(by_event.get(item["event_id"], {}).values())
            identities = {r.get("identity") for r in reviews if r.get("cat_presence") == "cat" and r.get("identity") not in {None, "unknown"}}
            if len(identities) != 1 or any(r.get("cat_presence") == "not_cat" for r in reviews):
                self._status(item["event_id"], "unconfirmed_or_conflicting")
                continue
            pet_id = next(iter(identities))
            if not any(r.get("learning_consent") and r.get("identity") == pet_id for r in reviews):
                self._status(item["event_id"], "awaiting_learning_consent")
                continue
            if pet_id not in galleries or len(galleries[pet_id]) >= self.policy.max_samples_per_pet:
                continue
            profile = self.registry.get_pet(pet_id)
            if profile.identity_model_version != self.embedder.model_version:
                self._status(item["event_id"], "model_version_mismatch")
                continue
            payload = None
            if item["feature_json"] and item["feature_model_version"] == self.embedder.model_version:
                try:
                    payload = json.loads(item["feature_json"])
                except (ValueError, TypeError):
                    self._status(item["event_id"], "invalid_feature")
                    continue
            elif new_count < budget:
                import cv2

                crop = bytes(item["crop_jpeg"])
                if hashlib.sha256(crop).hexdigest() != item["crop_sha256"]:
                    self._status(item["event_id"], "checksum_failed")
                    continue
                image = cv2.imdecode(np.frombuffer(crop, dtype=np.uint8), cv2.IMREAD_COLOR)
                if image is None or min(image.shape[:2]) < self.policy.minimum_side:
                    self._status(item["event_id"], "invalid_crop")
                    continue
                if cv2.Laplacian(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < self.policy.minimum_blur_score:
                    self._status(item["event_id"], "blurred")
                    continue
                vector = np.asarray(self.embedder.embed(crop), dtype=np.float32).reshape(-1)
                norm = float(np.linalg.norm(vector))
                if not np.isfinite(vector).all() or norm <= 0:
                    self._status(item["event_id"], "invalid_feature")
                    continue
                payload = {"vector": (vector / norm).tolist(), "color": extract_hsv_histogram(crop).tolist()}
                self.store._write(
                    "UPDATE identity_feedback_evidence SET feature_json=?,feature_model_version=? WHERE event_id=?",
                    (json.dumps(payload), self.embedder.model_version, item["event_id"]),
                )
                new_count += 1
            if payload is None:
                continue
            try:
                vector = np.asarray(payload["vector"], dtype=np.float32)
                color = np.asarray(payload["color"], dtype=np.float32)
                if (vector.ndim != 1 or not vector.size or not np.isfinite(vector).all()
                        or not np.isfinite(color).all() or color.shape != (128,)
                        or not np.isclose(np.linalg.norm(vector), 1, atol=0.001)):
                    raise ValueError("無效特徵")
            except (ValueError, TypeError, KeyError):
                self._status(item["event_id"], "invalid_feature")
                continue
            galleries[pet_id].append({**payload, "event_id": item["event_id"]})
            self._status(item["event_id"], "eligible")
        # Re-read labels after embedding: feedback can change while inference runs.
        with self.store._connection_lock:
            published = sum(self._publish(pet_id, samples) for pet_id, samples in galleries.items())
        return {"new_features": new_count, "published_samples": published}

    def _status(self, event_id, status):
        self.store._write("UPDATE identity_feedback_evidence SET learning_status=? WHERE event_id=? AND learning_status!=?", (status, event_id, status))

    def _publish(self, pet_id, samples):
        # Independent gallery: reference photos and manually enrolled vectors stay unchanged.
        reference, _ = self.registry.get_embeddings(pet_id, include_feedback=False)
        accepted = []
        for sample in samples:
            reviews = self.store.connection.execute(
                "SELECT reviewer_ref,dimension,label,learning_consent FROM behavior_event_annotations WHERE event_id=?",
                (sample["event_id"],),
            ).fetchall()
            current = {}
            for row in reviews:
                current.setdefault(row["reviewer_ref"], {})[row["dimension"]] = row["label"]
            identities = {r.get("identity") for r in current.values()
                          if r.get("cat_presence") == "cat" and r.get("identity") not in {None, "unknown"}}
            if identities != {pet_id} or any(r.get("cat_presence") == "not_cat" for r in current.values()):
                continue
            vector = np.asarray(sample["vector"], dtype=np.float32)
            if not any(row["dimension"] == "identity" and row["label"] == pet_id and row["learning_consent"] for row in reviews):
                continue
            if len(reference) and reference.shape[1] != vector.size:
                continue
            comparisons = [*reference, *(np.asarray(s["vector"]) for s in accepted)]
            if any(float(np.dot(vector, existing)) >= self.policy.duplicate_similarity for existing in comparisons):
                continue
            accepted.append(sample)
        target = self.registry.root_dir / pet_id / "feedback_embeddings.npz"
        vectors = np.asarray([s["vector"] for s in accepted], dtype=np.float32)
        colors = np.asarray([s["color"] for s in accepted], dtype=np.float32)
        if target.is_file():
            try:
                with np.load(target, allow_pickle=False) as previous:
                    if (str(previous["model_version"]) == self.embedder.model_version
                            and np.array_equal(previous["embeddings"], vectors)
                            and np.array_equal(previous["colors"], colors)):
                        return len(accepted)
            except (OSError, ValueError, KeyError):
                pass
        descriptor, temporary = tempfile.mkstemp(prefix=".feedback-", suffix=".npz", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                np.savez_compressed(output, embeddings=vectors, colors=colors,
                                    event_ids=np.asarray([s["event_id"] for s in accepted], dtype="U36"),
                                    model_version=self.embedder.model_version)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            from pathlib import Path
            Path(temporary).unlink(missing_ok=True)
        return len(accepted)


def behavior_feedback_report(store):
    with store._connection_lock:
        rows = store.connection.execute(
            "SELECT e.behavior_state,a.label,COUNT(*) AS n FROM behavior_event_annotations a JOIN behavior_events e USING(event_id) WHERE a.dimension='behavior' AND a.label NOT IN ('unknown','out_of_view') GROUP BY e.behavior_state,a.label"
        ).fetchall()
        scene_count = store.connection.execute("SELECT COUNT(*) FROM telegram_pet_review_history").fetchone()[0]
    return {"experimental": True, "scope": "reviewed_track_events_only", "count_unit": "reviewer_votes_not_unique_events",
            "comparisons": [{"predicted": r[0], "reported": r[1], "count": r[2]} for r in rows],
            "scene_labels_without_track_mapping": scene_count,
            "automatic_rule_changes": False, "automatic_training": False}
