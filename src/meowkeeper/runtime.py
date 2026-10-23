"""MeowKeeper+ 內含 runtime 與已核准模型的路徑／完整性契約。"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_MODEL_NAME = "yolo11n.pt"
DEFAULT_MODEL_SOURCE_REPOSITORY = "ultralytics/assets"
DEFAULT_MODEL_RELEASE_TAG = "v8.4.0"
DEFAULT_MODEL_URL = (
    "https://github.com/ultralytics/assets/releases/download/"
    f"{DEFAULT_MODEL_RELEASE_TAG}/{DEFAULT_MODEL_NAME}"
)
DEFAULT_MODEL_SHA256 = "0EBBC80D4A7680D14987A577CD21342B65ECFD94632BD9A8DA63AE6417644EE1"
DEFAULT_MODEL_LICENSE = "AGPL-3.0 (Ultralytics Enterprise License available)"

DEFAULT_IDENTITY_MODEL_NAME = "dinov2_vits14_pretrain.pth"
DEFAULT_IDENTITY_MODEL_SOURCE_REPOSITORY = "facebookresearch/dinov2"
DEFAULT_IDENTITY_MODEL_RELEASE_TAG = "main@7764ea0f912e53c92e82eb78a2a1631e92725fc"
DEFAULT_IDENTITY_MODEL_URL = (
    "https://dl.fbaipublicfiles.com/dinov2/dinov2_vits14/"
    f"{DEFAULT_IDENTITY_MODEL_NAME}"
)
DEFAULT_IDENTITY_MODEL_SHA256 = "B938BF1BC15CD2EC0FEACFE3A1BB553FE8EA9CA46A7E1D8D00217F29AEF60CD9"
DEFAULT_IDENTITY_MODEL_LICENSE = "Apache-2.0"

DEFAULT_POSTURE_MODEL_NAME = "posture_mlp_v1.pth"
DEFAULT_POSTURE_MODEL_VERSION = "cat_posture_mlp@v1.0.0"


class ModelValidationError(RuntimeError):
    """預設模型或其來源 manifest 不符合核准契約。"""


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_model_file(name: str = DEFAULT_MODEL_NAME) -> Path:
    """取得預設模型的預期路徑，即使檔案尚未存在也回傳路徑。"""

    return project_root() / "models" / name


def default_model_path(name: str = DEFAULT_MODEL_NAME) -> str | None:
    """取得專案內已核准的預設模型；找不到時回傳 None。"""

    candidate = default_model_file(name)
    return str(candidate) if candidate.is_file() else None


def model_manifest_path() -> Path:
    """取得預設模型 manifest 路徑。"""

    return project_root() / "models" / "model_manifest.json"


def identity_model_file() -> Path:
    """取得官方 DINOv2 ViT-S/14 checkpoint 的預期路徑。"""

    return project_root() / "models" / "identity" / "dinov2_vits14" / DEFAULT_IDENTITY_MODEL_NAME


def identity_model_manifest_path() -> Path:
    """取得 DINOv2 身分模型 manifest 路徑。"""

    return identity_model_file().parent / "model_manifest.json"




def posture_model_file() -> Path:
    """取得輕量姿態分類器模型權重路徑。"""
    return project_root() / "models" / "behavior" / DEFAULT_POSTURE_MODEL_NAME


def posture_model_manifest_path() -> Path:
    """取得輕量姿態分類器 manifest 路徑。"""
    return posture_model_file().parent / "model_manifest.json"


def sha256_file(path: Path) -> str:
    """以串流方式計算檔案 SHA-256，避免將模型整個載入記憶體。"""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_model_manifest(
    model_path: Path | None = None,
    *,
    manifest_path: Path | None = None,
    downloaded_at_utc: str | None = None,
) -> dict[str, Any]:
    """建立預設模型的可追溯 manifest；寫入前會再次核對固定 SHA-256。"""

    path = (model_path or default_model_file()).resolve()
    if not path.is_file():
        raise ModelValidationError(f"找不到預設 YOLO 模型：{path}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != DEFAULT_MODEL_SHA256:
        raise ModelValidationError("預設 YOLO 模型 SHA-256 不符合官方核准值，拒絕建立 manifest。")
    manifest = {
        "schema_version": 1,
        "model_name": DEFAULT_MODEL_NAME,
        "source_repository": DEFAULT_MODEL_SOURCE_REPOSITORY,
        "source_url": DEFAULT_MODEL_URL,
        "release_tag": DEFAULT_MODEL_RELEASE_TAG,
        "file_size_bytes": path.stat().st_size,
        "sha256": actual_sha256,
        "license": DEFAULT_MODEL_LICENSE,
        "downloaded_at_utc": downloaded_at_utc or _utc_now(),
    }
    target = (manifest_path or model_manifest_path()).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def write_identity_model_manifest(
    model_path: Path | None = None,
    *,
    manifest_path: Path | None = None,
    downloaded_at_utc: str | None = None,
) -> dict[str, Any]:
    """建立官方 DINOv2 身分模型 manifest，寫入前固定核對 SHA-256。"""

    path = (model_path or identity_model_file()).resolve()
    if not path.is_file():
        raise ModelValidationError(f"找不到 DINOv2 身分模型：{path}")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != DEFAULT_IDENTITY_MODEL_SHA256:
        raise ModelValidationError("DINOv2 身分模型 SHA-256 不符合官方核准值。")
    manifest = {
        "schema_version": 1,
        "model_name": DEFAULT_IDENTITY_MODEL_NAME,
        "source_repository": DEFAULT_IDENTITY_MODEL_SOURCE_REPOSITORY,
        "source_url": DEFAULT_IDENTITY_MODEL_URL,
        "release_tag": DEFAULT_IDENTITY_MODEL_RELEASE_TAG,
        "file_size_bytes": path.stat().st_size,
        "sha256": actual_sha256,
        "license": DEFAULT_IDENTITY_MODEL_LICENSE,
        "downloaded_at_utc": downloaded_at_utc or _utc_now(),
    }
    target = (manifest_path or identity_model_manifest_path()).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def validate_model_file(
    model_path: Path | None = None,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """驗證預設模型存在、來源欄位、大小與 SHA-256。"""

    path = (model_path or default_model_file()).resolve()
    if not path.is_file():
        raise ModelValidationError(
            f"找不到預設 YOLO 權重：{path}；請在開發／建置階段執行 tools\\fetch_model.py。"
        )
    target = (manifest_path or model_manifest_path()).resolve()
    if not target.is_file():
        raise ModelValidationError(
            f"找不到模型完整性 manifest：{target}；請重新執行 tools\\fetch_model.py。"
        )
    try:
        manifest = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelValidationError(f"模型 manifest 無法讀取或不是有效 JSON：{target}") from exc
    if not isinstance(manifest, dict):
        raise ModelValidationError("模型 manifest 格式不正確，必須是 JSON 物件。")

    expected_fields = {
        "schema_version": 1,
        "model_name": DEFAULT_MODEL_NAME,
        "source_repository": DEFAULT_MODEL_SOURCE_REPOSITORY,
        "source_url": DEFAULT_MODEL_URL,
        "release_tag": DEFAULT_MODEL_RELEASE_TAG,
        "sha256": DEFAULT_MODEL_SHA256,
        "license": DEFAULT_MODEL_LICENSE,
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise ModelValidationError(f"模型 manifest 的 {field} 不符合官方預設值。")
    if not isinstance(manifest.get("downloaded_at_utc"), str) or not manifest["downloaded_at_utc"].strip():
        raise ModelValidationError("模型 manifest 缺少有效的 UTC 下載日期。")
    if path.name != manifest.get("model_name"):
        raise ModelValidationError("模型檔名與 manifest 不一致。")

    actual_size = path.stat().st_size
    if manifest.get("file_size_bytes") != actual_size:
        raise ModelValidationError("模型檔案大小與 manifest 不一致，可能已損毀。")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != manifest.get("sha256"):
        raise ModelValidationError("模型 SHA-256 與 manifest 不一致，可能已被替換。")
    return manifest


def validate_identity_model_file(
    model_path: Path | None = None,
    *,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """驗證 DINOv2 身分模型的官方來源、大小與 SHA-256。"""

    path = (model_path or identity_model_file()).resolve()
    if not path.is_file():
        raise ModelValidationError(
            f"找不到 DINOv2 身分模型：{path}；請在開發／建置階段執行 tools\\fetch_identity_model.py。"
        )
    target = (manifest_path or identity_model_manifest_path()).resolve()
    if not target.is_file():
        raise ModelValidationError(f"找不到 DINOv2 身分模型 manifest：{target}")
    try:
        manifest = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelValidationError(f"DINOv2 身分模型 manifest 無法讀取：{target}") from exc
    if not isinstance(manifest, dict):
        raise ModelValidationError("DINOv2 身分模型 manifest 格式不正確。")
    expected = {
        "schema_version": 1,
        "model_name": DEFAULT_IDENTITY_MODEL_NAME,
        "source_repository": DEFAULT_IDENTITY_MODEL_SOURCE_REPOSITORY,
        "source_url": DEFAULT_IDENTITY_MODEL_URL,
        "release_tag": DEFAULT_IDENTITY_MODEL_RELEASE_TAG,
        "sha256": DEFAULT_IDENTITY_MODEL_SHA256,
        "license": DEFAULT_IDENTITY_MODEL_LICENSE,
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ModelValidationError(f"DINOv2 manifest 的 {field} 不符合官方固定值。")
    if not isinstance(manifest.get("downloaded_at_utc"), str):
        raise ModelValidationError("DINOv2 manifest 缺少 UTC 下載日期。")
    if path.name != manifest.get("model_name"):
        raise ModelValidationError("DINOv2 模型檔名與 manifest 不一致。")
    if manifest.get("file_size_bytes") != path.stat().st_size:
        raise ModelValidationError("DINOv2 模型大小與 manifest 不一致。")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != manifest.get("sha256"):
        raise ModelValidationError("DINOv2 模型 SHA-256 與 manifest 不一致。")
    return manifest


def bundled_binary(name: str) -> str | None:
    """優先使用專案內 vendor runtime，再退回 PATH。"""

    candidates = [
        project_root() / "vendor" / "ffmpeg" / "bin" / f"{name}.exe",
        project_root() / "vendor" / "ffmpeg" / "bin" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def bundled_javascript_runtime() -> str | None:
    """優先使用發布包內的 JavaScript runtime，再退回開發機 PATH。"""

    candidates = (
        project_root() / "vendor" / "javascript" / "node.exe",
        project_root() / "vendor" / "javascript" / "deno.exe",
        project_root() / "vendor" / "javascript" / "bun.exe",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("deno") or shutil.which("node") or shutil.which("bun")


def installed_script(name: str) -> str | None:
    """尋找專案 venv、目前 Python 環境或 PATH 中的 CLI。"""

    executable_name = f"{name}.exe" if sys.platform == "win32" else name
    candidates = [
        Path(sys.executable).resolve().parent / executable_name,
        Path(sys.executable).resolve().parent / "Scripts" / executable_name,
        project_root() / ".venv" / "Scripts" / executable_name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)
