"""單張貓咪裁切的 AI 身分比對；只讀取特徵，不保存或訓練。"""
from __future__ import annotations

from urllib.parse import quote

import numpy as np

from .identity_classifier import IdentityClassifier


def compare_photo(registry, embedder, jpeg: bytes, *, thresholds=None) -> dict:
    vector = np.asarray(embedder.embed(jpeg), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(vector).all() or norm <= 0:
        raise ValueError("照片無法產生有效身分特徵。")
    normalized = vector / norm

    class CachedEmbedder:
        model_version = embedder.model_version
        backend_name = embedder.backend_name

        def embed(self, _crop):
            return vector

    prediction = IdentityClassifier(registry, CachedEmbedder(), thresholds=thresholds, strict_single_pet=True).classify(jpeg)
    candidates = []
    for pet in registry.list_pets(enabled_only=True):
        if pet.identity_model_version != embedder.model_version:
            continue
        reference, _ = registry.get_embeddings(pet.pet_id, include_feedback=False)
        if not reference.size or reference.shape[1] != normalized.size:
            continue
        scores = reference @ normalized
        index = int(np.argmax(scores))
        image_id = None
        if index < len(pet.reference_images):
            from pathlib import Path

            image_id = Path(pet.reference_images[index]).name
        candidates.append({
            "pet_id": pet.pet_id, "name": pet.display_name,
            "reference_similarity": float(scores[index]),
            "reference_url": (f"/api/v1/pets/{quote(pet.pet_id, safe='')}/images/"
                              f"{quote(image_id, safe='')}" if image_id else None),
        })
    ranking = {item["pet_id"]: item for item in prediction.candidates}
    for candidate in candidates:
        ranked = ranking.get(candidate["pet_id"], {})
        candidate["pet_score"] = ranked.get("pet_score", candidate["reference_similarity"])
        candidate["reference_matches"] = ranked.get("reference_matches", [])
    candidates.sort(key=lambda item: item["pet_score"], reverse=True)
    valid_ids = {item["pet_id"] for item in candidates}
    accepted = prediction.predicted_pet_id in valid_ids
    return {
        "experimental": True, "status": "candidate" if accepted else "unknown",
        "predicted_pet_id": prediction.predicted_pet_id if accepted else None,
        "predicted_name": prediction.predicted_name if accepted else None,
        "similarity": prediction.similarity, "margin": prediction.confidence_margin,
        "unknown_reason": prediction.unknown_reason,
        "candidates": candidates[:3], "model_version": embedder.model_version,
        "message_zh_tw": "相似度不是準確率；單張結果未經時間投票，不會修改特徵或重新訓練模型。",
    }
