"""SYSTEM worker-side verifier for a Dashboard-requested signed release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

def _is_reparse(path: Path) -> bool:
    try:
        attributes = path.stat(follow_symlinks=False).st_file_attributes
    except (AttributeError, OSError):
        return path.is_symlink()
    return bool(attributes & 0x400)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_and_stage(
    archive: Path,
    envelope_path: Path,
    *,
    app_root: Path,
    stage_root: Path,
) -> dict[str, str]:
    """Re-verify copied bytes under the protected updater directory, then extract."""
    for path in (app_root, stage_root, archive, envelope_path):
        if _is_reparse(path):
            raise ValueError("更新驗證路徑無效。")
    app_root = app_root.resolve(strict=True)
    stage_root = stage_root.resolve(strict=True)
    archive = archive.resolve(strict=True)
    envelope_path = envelope_path.resolve(strict=True)
    if archive.parent != stage_root or envelope_path.parent != stage_root:
        raise ValueError("更新封存檔必須位於受保護的更新暫存目錄。")
    for path in (app_root / "configs" / "update-public.pem", stage_root):
        if _is_reparse(path) or not path.exists():
            raise ValueError("更新驗證路徑無效。")
    archive_size = archive.stat().st_size
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    if envelope.get("manifest", {}).get("schema") == 2:
        import importlib.util
        module_path = ROOT / "src/meowkeeper/app/delta_update.py"
        spec = importlib.util.spec_from_file_location("signed_delta_verifier", module_path)
        delta = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(delta)
        manifest = delta.verify_delta(envelope, (app_root / "configs/update-public.pem").read_bytes())
        package = stage_root / "app"
        delta.assemble(app_root, archive, manifest, package)
        return {"status": "staged", "version": manifest["version"], "package_path": str(package)}
    from meowkeeper.app.updates import safe_extract, verify_envelope
    from meowkeeper.app.web.release_info import release_information

    manifest = verify_envelope(envelope, (app_root / "configs" / "update-public.pem").read_bytes())
    if archive_size != manifest["size"] or _digest(archive) != manifest["sha256"].lower():
        raise ValueError("官方更新套件簽章或 SHA-256 校驗不符。")
    installed = release_information(app_root)["version"]
    if re.fullmatch(r"r\d+", installed) and int(manifest["version"][1:]) <= int(installed[1:]):
        raise ValueError("更新版本不比目前安裝版本新；已拒絕降版或重複套用。")
    package = stage_root / "app"
    if package.exists() or _is_reparse(package):
        raise ValueError("更新工作暫存目錄已存在；拒絕覆寫。")
    safe_extract(archive, package)
    verifier_path = app_root / "tools" / "verify_portable.py"
    if _is_reparse(verifier_path):
        raise ValueError("本機安裝驗證工具路徑無效。")
    import importlib.util

    spec = importlib.util.spec_from_file_location("meowkeeper_hotfix_verifier", verifier_path)
    if spec is None or spec.loader is None:
        raise ValueError("本機安裝驗證工具無法載入。")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify_portable(package)
    if release_information(package)["version"] != manifest["version"]:
        raise ValueError("套件版本與官方簽章清單不符。")
    return {"status": "staged", "version": manifest["version"], "package_path": str(package)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="驗證官方更新並建立受保護的安裝暫存。")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--stage-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if os.name != "nt":
            raise ValueError("安全更新工作僅支援 Windows 安裝版。")
        program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData")).resolve()
        expected_app = program_data / "MeowKeeperPlus" / "App"
        expected_updater = program_data / "MeowKeeperPlus" / "Updater"
        expected_stage = args.stage_root.resolve(strict=True)
        if (args.app_root.resolve(strict=True) != expected_app.resolve(strict=True)
                or expected_stage.parent != expected_updater.resolve(strict=True)
                or not re.fullmatch(r"work-[a-f0-9]{32}", expected_stage.name)):
            raise ValueError("安全更新工作路徑不符合安裝邊界。")
        result = verify_and_stage(args.archive, args.envelope, app_root=args.app_root, stage_root=args.stage_root)
    except Exception as exc:  # noqa: BLE001 - emit only a safe error class to the privileged launcher.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
