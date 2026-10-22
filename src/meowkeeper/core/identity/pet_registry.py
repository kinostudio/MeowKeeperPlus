"""可配置寵物資料與本地參考特徵保存。"""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_pet_id(pet_id: str) -> None:
    if not isinstance(pet_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pet_id):
        raise ValueError("pet_id 只能包含英數字、底線或連字號，長度最多 64。")


@dataclass
class PetProfile:
    pet_id: str
    display_name: str
    coat_description: str = ""
    pattern_description: str = ""
    reference_images: list[str] = field(default_factory=list)
    created_at_utc: str = field(default_factory=_now_utc)
    identity_model_version: str = "not-trained"
    embedding_count: int = 0
    enabled: bool = True
    semantic_traits: list[str] = field(default_factory=list)

    def validate(self) -> None:
        _validate_pet_id(self.pet_id)
        if not self.display_name.strip():
            raise ValueError("寵物顯示名稱不可為空白。")
        if self.embedding_count < 0:
            raise ValueError("寵物 embedding 數量不可小於零。")

    def effective_traits(self) -> list[str]:
        """傳回結構化語意文字特徵（如：橘白、虎斑、米克斯），若無則由毛色與花紋衍生。"""
        if self.semantic_traits:
            return [str(t).strip() for t in self.semantic_traits if str(t).strip()]
        derived: list[str] = []
        for part in (self.coat_description, self.pattern_description):
            if part and part.strip():
                for sub in re.split(r"[,、/ ]+", part.strip()):
                    if sub and sub not in derived:
                        derived.append(sub)
        return derived

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


class PetRegistry:
    """管理 data/pets 下的 profile、參考影像與多個 embedding。"""

    def __init__(self, root_dir: str | Path = "data/pets") -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def register_pet(
        self,
        display_name: str,
        *,
        coat_description: str = "",
        pattern_description: str = "",
        pet_id: str | None = None,
        identity_model_version: str = "not-trained",
    ) -> PetProfile:
        profile = PetProfile(
            pet_id=pet_id or str(uuid.uuid4()),
            display_name=display_name.strip(),
            coat_description=coat_description.strip(),
            pattern_description=pattern_description.strip(),
            identity_model_version=identity_model_version,
        )
        profile.validate()
        directory = self.root_dir / profile.pet_id
        if directory.exists():
            raise ValueError("指定的寵物 pet_id 已存在。")
        (directory / "reference_images").mkdir(parents=True, exist_ok=False)
        self._write_profile(profile)
        return profile

    def list_pets(self, *, enabled_only: bool = False) -> list[PetProfile]:
        profiles: list[PetProfile] = []
        for path in sorted(self.root_dir.glob("*/profile.json")):
            profile = PetProfile(**json.loads(path.read_text(encoding="utf-8")))
            if not enabled_only or profile.enabled:
                profiles.append(profile)
        return profiles

    def get_pet(self, pet_id: str) -> PetProfile | None:
        _validate_pet_id(pet_id)
        path = self.root_dir / pet_id / "profile.json"
        if not path.is_file():
            return None
        profile = PetProfile(**json.loads(path.read_text(encoding="utf-8")))
        profile.validate()
        if profile.pet_id != pet_id:
            raise ValueError("寵物資料與目錄識別不一致。")
        return profile

    def update_profile(
        self,
        pet_id: str,
        *,
        display_name: str | None = None,
        coat_description: str | None = None,
        pattern_description: str | None = None,
        enabled: bool | None = None,
        semantic_traits: list[str] | None = None,
    ) -> PetProfile:
        """更新可編輯的寵物資料，保留照片、embedding 與建立時間。"""

        profile = self.get_pet(pet_id)
        if profile is None:
            raise KeyError(f"找不到寵物：{pet_id}")
        if display_name is not None:
            profile.display_name = display_name.strip()
        if coat_description is not None:
            profile.coat_description = coat_description.strip()
        if pattern_description is not None:
            profile.pattern_description = pattern_description.strip()
        if enabled is not None:
            profile.enabled = bool(enabled)
        if semantic_traits is not None:
            profile.semantic_traits = [str(t).strip() for t in semantic_traits if str(t).strip()]
        profile.validate()
        self._write_profile(profile)
        return profile

    def add_reference_image(self, pet_id: str, jpeg_bytes: bytes, *, filename: str | None = None) -> Path:
        profile = self.get_pet(pet_id)
        if profile is None:
            raise KeyError(f"找不到寵物：{pet_id}")
        if not jpeg_bytes:
            raise ValueError("參考影像不可為空白。")
        directory = self.root_dir / pet_id / "reference_images"
        directory.mkdir(parents=True, exist_ok=True)
        name = filename or f"reference_{uuid.uuid4().hex}.jpg"
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.jpg", name):
            raise ValueError("參考影像名稱須為安全的 JPG 檔名。")
        target = directory / name
        with target.open("xb") as image_file:
            image_file.write(jpeg_bytes)
        profile.reference_images.append(str(target.relative_to(self.root_dir / pet_id)))
        self._write_profile(profile)
        return target

    def reference_image_path(self, pet_id: str, image_name: str) -> Path:
        profile = self.get_pet(pet_id)
        if profile is None:
            raise KeyError("找不到寵物。")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.jpg", image_name):
            raise ValueError("照片識別格式不正確。")
        matches = [item for item in profile.reference_images if Path(item).name == image_name]
        if len(matches) != 1:
            raise KeyError("找不到指定參考照片。")
        pet_root = (self.root_dir / pet_id).resolve()
        root = (pet_root / "reference_images").resolve()
        if pet_root.parent != self.root_dir.resolve() or root.parent != pet_root:
            raise ValueError("參考照片目錄不在允許範圍。")
        unresolved = self.root_dir / pet_id / matches[0]
        candidate = unresolved.resolve()
        if candidate.parent != root or unresolved.is_symlink():
            raise ValueError("參考照片不在允許目錄。")
        return candidate

    def change_reference_image(self, pet_id: str, image_name: str, jpeg_bytes: bytes | None = None) -> None:
        """Replace/delete one reference and invalidate its derived gallery.

        Feedback evidence and feedback embeddings are separate and untouched.
        The caller must prevent mutations while the embedding job is running.
        """
        target = self.reference_image_path(pet_id, image_name)
        profile = self.get_pet(pet_id)
        replacement = None
        if jpeg_bytes is not None:
            if not jpeg_bytes:
                raise ValueError("參考照片不可為空白。")
            replacement = target.with_name(f"reference_{uuid.uuid4().hex}.jpg")
            replacement.write_bytes(jpeg_bytes)
            profile.reference_images = [str(replacement.relative_to(self.root_dir / pet_id))
                                        if Path(item).name == image_name else item for item in profile.reference_images]
        else:
            profile.reference_images = [item for item in profile.reference_images if Path(item).name != image_name]
        profile.embedding_count = 0
        profile.identity_model_version = "not-trained"
        try:
            self._write_profile(profile)
        except (OSError, ValueError):
            if replacement is not None:
                replacement.unlink(missing_ok=True)
            raise
        (self.root_dir / pet_id / "embeddings.npz").unlink(missing_ok=True)
        target.unlink(missing_ok=True)

    def add_embedding(
        self,
        pet_id: str,
        vector: Any,
        *,
        color_vector: Any | None = None,
        model_version: str,
    ) -> int:
        profile = self.get_pet(pet_id)
        if profile is None:
            raise KeyError(f"找不到寵物：{pet_id}")
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("保存身分 embedding 需要 numpy。") from exc
        embedding_path = self.root_dir / pet_id / "embeddings.npz"
        if embedding_path.is_file() and profile.embedding_count > 0:
            with np.load(embedding_path, allow_pickle=False) as existing:
                embeddings = existing["embeddings"].copy()
                colors = existing["colors"].copy() if "colors" in existing.files else np.empty((0, 128), dtype=np.float32)
        else:
            embeddings = np.empty((0, len(np.asarray(vector).reshape(-1))), dtype=np.float32)
            colors = np.empty((0, len(np.asarray(color_vector).reshape(-1)) if color_vector is not None else 0), dtype=np.float32)
        normalized = np.asarray(vector, dtype=np.float32).reshape(1, -1)
        norm = float(np.linalg.norm(normalized))
        if norm > 0:
            normalized = normalized / norm
        embeddings = np.concatenate([embeddings, normalized], axis=0)
        if color_vector is not None:
            color = np.asarray(color_vector, dtype=np.float32).reshape(1, -1)
            if colors.shape[1] == 0:
                colors = np.zeros((embeddings.shape[0] - 1, color.shape[1]), dtype=np.float32)
            elif colors.shape[0] < embeddings.shape[0] - 1:
                padding = np.zeros(
                    (embeddings.shape[0] - 1 - colors.shape[0], colors.shape[1]),
                    dtype=np.float32,
                )
                colors = np.concatenate([colors, padding], axis=0)
            colors = np.concatenate([colors, color], axis=0)
        elif colors.shape[1] > 0:
            colors = np.concatenate([colors, np.zeros((1, colors.shape[1]), dtype=np.float32)], axis=0)
        temporary = embedding_path.with_name(f".embeddings-{uuid.uuid4().hex}.npz")
        try:
            np.savez_compressed(temporary, embeddings=embeddings, colors=colors,
                                model_version=model_version, preprocess_version="dinov2-default-v1")
            os.replace(temporary, embedding_path)
        finally:
            temporary.unlink(missing_ok=True)
        profile.embedding_count = int(embeddings.shape[0])
        profile.identity_model_version = model_version
        self._write_profile(profile)
        return profile.embedding_count

    def get_embeddings(self, pet_id: str, *, include_feedback: bool = True) -> tuple[Any, Any | None]:
        _validate_pet_id(pet_id)
        profile = self.get_pet(pet_id)
        if profile is None or profile.embedding_count == 0:
            return [], None
        path = self.root_dir / pet_id / "embeddings.npz"
        if not path.is_file():
            return [], None
        try:
            import numpy as np  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("讀取身分 embedding 需要 numpy。") from exc
        with np.load(path, allow_pickle=False) as data:
            if "model_version" in data.files and str(data["model_version"]) != profile.identity_model_version:
                return [], None
            embeddings = data["embeddings"].copy()
            colors = data["colors"].copy() if "colors" in data.files and data["colors"].size else None
        learned_path = self.root_dir / pet_id / "feedback_embeddings.npz"
        if include_feedback and learned_path.is_file():
            try:
                with np.load(learned_path, allow_pickle=False) as learned:
                    profile = self.get_pet(pet_id)
                    vectors = learned["embeddings"]
                    learned_colors = learned["colors"]
                    if (profile and str(learned["model_version"]) == profile.identity_model_version
                            and vectors.ndim == 2 and vectors.shape[1] == embeddings.shape[1]
                            and np.isfinite(vectors).all() and np.isfinite(learned_colors).all() and learned_colors.ndim == 2
                            and len(learned_colors) == len(vectors)):
                        if colors is None:
                            colors = np.zeros((len(embeddings), learned_colors.shape[1]), dtype=np.float32)
                        if colors.shape[1] == learned_colors.shape[1]:
                            embeddings = np.concatenate([embeddings, vectors])
                            colors = np.concatenate([colors, learned_colors])
            except (OSError, ValueError, KeyError):
                pass  # Corrupt optional learned gallery cannot stop detection.
        return embeddings, colors

    @staticmethod
    def feature_version(vectors, colors, model_version: str) -> str:
        import hashlib

        digest = hashlib.sha256(model_version.encode())
        for values in (vectors, colors):
            if values is not None and hasattr(values, "tobytes"):
                digest.update(str(values.shape).encode())
                digest.update(values.tobytes())
        return digest.hexdigest()

    def gallery_summary(self, pet_id: str) -> dict:

        profile = self.get_pet(pet_id)
        if profile is None:
            raise KeyError("找不到寵物資料。")
        feedback_count = 0
        for name in ("embeddings.npz", "feedback_embeddings.npz"):
            path = self.root_dir / pet_id / name
            if path.is_file() and name == "feedback_embeddings.npz":
                try:
                    import numpy as np
                    with np.load(path, allow_pickle=False) as features:
                        if str(features["model_version"]) == profile.identity_model_version:
                            feedback_count = len(features["embeddings"])
                except (OSError, ValueError, KeyError):
                    pass
        vectors, colors = self.get_embeddings(pet_id)
        return {"gallery_version": self.feature_version(vectors, colors, profile.identity_model_version),
                "feedback_feature_count": feedback_count}

    def _write_profile(self, profile: PetProfile) -> None:
        profile.validate()
        target = self.root_dir / profile.pet_id / "profile.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
