"""版本相容特徵、固定 per-pet top-K 與 Unknown 安全閘。"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from .base import EmbeddingBackend, IdentityPrediction, IdentityThresholds
from .color_features import cosine_similarity, extract_hsv_histogram
from .pet_registry import PetRegistry


class IdentityClassifier:
    def __init__(self, registry: PetRegistry, embedder: EmbeddingBackend, *,
                 thresholds: IdentityThresholds | None = None, strict_single_pet: bool = False) -> None:
        self.registry, self.embedder = registry, embedder
        self.thresholds = thresholds or IdentityThresholds()
        self.thresholds.validate()
        self.strict_single_pet = strict_single_pet

    def classify(self, crop: bytes | Any, *, track_id: str | int | None = None) -> IdentityPrediction:
        import numpy as np

        def unknown(code, *, candidates=(), score=0., second=0.):
            return IdentityPrediction(None, None, score, second, score-second,
                                      max(0., min(1., 1-score)), self.embedder.model_version,
                                      track_id, False, "身分尚未確認。", tuple(candidates), code)
        try:
            vector = np.asarray(self.embedder.embed(crop), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if not np.isfinite(vector).all() or norm <= 0:
                return unknown("feature_unavailable")
            vector /= norm
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return unknown("model_unavailable")
        try:
            color = extract_hsv_histogram(crop)
        except (RuntimeError, ValueError):
            color = None
        candidates = []
        incompatible = False
        for pet in self.registry.list_pets(enabled_only=True):
            if pet.identity_model_version != self.embedder.model_version:
                incompatible = incompatible or pet.embedding_count > 0
                continue
            try:
                vectors, colors = self.registry.get_embeddings(pet.pet_id)
                vectors = np.asarray(vectors, dtype=np.float32)
                if not vectors.size:
                    continue
                if vectors.ndim != 2 or vectors.shape[1] != vector.size or not np.isfinite(vectors).all():
                    incompatible = True
                    continue
                norms = np.linalg.norm(vectors, axis=1)
                if np.any(norms <= 0):
                    incompatible = True
                    continue
                scores = np.clip((vectors / norms[:, None]) @ vector, -1, 1)
                indices = np.argsort(scores)[::-1][:self.thresholds.top_k_embeddings]
                visual = float(np.mean(scores[indices]))
                fused = visual
                if color is not None and colors is not None and len(colors):
                    color_scores = [cosine_similarity(color, c) for c in colors
                                    if np.isfinite(c).all() and np.linalg.norm(c) > 0]
                    if color_scores:
                        t = self.thresholds
                        fused = (visual*t.embedding_weight + max(color_scores)*t.color_weight) / (
                            t.embedding_weight+t.color_weight)
                matches = [{"reference_image_id": Path(pet.reference_images[int(i)]).name,
                            "cosine_score": float(scores[i]), "source": "manual_upload"}
                           for i in indices if int(i) < len(pet.reference_images)]
                is_calibrated = len(vectors) > len(pet.reference_images)
                candidates.append({"pet_id": pet.pet_id, "name": pet.display_name,
                                   "pet_score": fused, "visual_score": visual,
                                   "gallery_version": self.registry.feature_version(vectors, colors, self.embedder.model_version),
                                   "reference_matches": matches,
                                   "calibrated": is_calibrated})
            except (OSError, ValueError, KeyError, TypeError):
                incompatible = True
        candidates.sort(key=lambda c: c["pet_score"], reverse=True)
        if not candidates:
            return unknown("model_mismatch" if incompatible else "no_gallery")
        best = candidates[0]
        score = best["pet_score"]
        second = candidates[1]["pet_score"] if len(candidates)>1 else 0.
        minimum = self.thresholds.accepted_threshold
        if len(candidates) == 1 and self.strict_single_pet:
            return unknown("calibration_required", candidates=candidates, score=score, second=second)
        code = "low_similarity" if score < minimum else (
            "small_margin" if score-second < self.thresholds.minimum_margin else None)
        if code:
            return unknown(code, candidates=candidates[:3], score=score, second=second)
        is_calibrated = bool(best.get("calibrated"))
        reason = "AI 身分候選（已納入家庭回饋特徵校準）。" if is_calibrated else "實驗性身分候選，尚非家庭校準結果。"
        return IdentityPrediction(best["pet_id"], best["name"], score, second, score-second,
                                  max(0., min(1., 1-score)), self.embedder.model_version,
                                  track_id, is_calibrated, reason,
                                  tuple(candidates[:3]))


class LazyIdentityClassifier:
    """在低優先工作中載入一次 DINO；新增特徵後不必重啟來源。"""

    def __init__(self, registry: PetRegistry, thresholds: IdentityThresholds | None = None):
        self.registry = registry
        self.thresholds = thresholds or IdentityThresholds()
        self._delegate = None
        self._lock = threading.Lock()
        self._retry_at = 0.0
        owner = self

        class Backend:
            @property
            def model_version(self):
                if owner._delegate is not None:
                    return owner._delegate.embedder.model_version
                from meowkeeper.runtime import DEFAULT_IDENTITY_MODEL_RELEASE_TAG
                return "dinov2_vits14@" + DEFAULT_IDENTITY_MODEL_RELEASE_TAG

            @property
            def backend_name(self):
                return owner._delegate.embedder.backend_name if owner._delegate else "waiting_for_features"

            @property
            def last_latency_ms(self):
                return getattr(owner._delegate.embedder, "last_latency_ms", None) if owner._delegate else None

            def embed(self, crop):
                return owner._ready().embedder.embed(crop)

        self.embedder = Backend()

    def _ready(self):
        with self._lock:
            if self._delegate is not None:
                return self._delegate
            if time.monotonic() < self._retry_at:
                raise RuntimeError("身分模型冷卻中。")
            try:
                from .dinov2_embedder import shared_cpu_embedder
                self._delegate = IdentityClassifier(self.registry, shared_cpu_embedder(), thresholds=self.thresholds, strict_single_pet=True)
            except (OSError, RuntimeError, ValueError, ImportError):
                self._retry_at = time.monotonic()+60
                raise
            return self._delegate

    def update_thresholds(self, thresholds: IdentityThresholds) -> None:
        with self._lock:
            self.thresholds = thresholds
            if self._delegate is not None:
                self._delegate.thresholds = thresholds

    def classify(self, crop, *, track_id=None):
        if not any(p.embedding_count for p in self.registry.list_pets(enabled_only=True)):
            return IdentityPrediction(None, None, 0, 0, 0, 1, self.embedder.model_version,
                                      track_id=track_id, unknown_reason="no_gallery", reason="尚未建立寵物特徵。")
        try:
            return self._ready().classify(crop, track_id=track_id)
        except (OSError, RuntimeError, ValueError, ImportError):
            return IdentityPrediction(None, None, 0, 0, 0, 1, self.embedder.model_version,
                                      track_id=track_id, unknown_reason="model_unavailable", reason="身分模型尚未可用。")
