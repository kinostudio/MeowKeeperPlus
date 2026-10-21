"""建立不要求終端使用者安裝 Python/pip 的 Windows 可攜式包。

這是開發／建置期工具，不會下載、安裝或修改 Python、驅動、CUDA、FFmpeg
或模型。建置者必須明確提供已準備好的 embedded Python runtime；缺少任何
必要檔案時以繁體中文失敗，避免產出看似完整但不能啟動的發布包。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from meowkeeper.core.media.youtube import YTDLP_PINNED_VERSION
from meowkeeper.runtime import (
    DEFAULT_IDENTITY_MODEL_RELEASE_TAG,
    default_model_file,
    model_manifest_path,
    validate_model_file,
)

RUNTIME_PACKAGES = (
    "numpy",
    "cv2",
    "ultralytics",
    "lap",  # PyPI distribution is lapx; its importable module is lap.
    "onnxruntime",
    "openvino",
    "torch",
    "torchvision",
    "yt_dlp",
    "yt_dlp_ejs",
    "fastapi",
    "uvicorn",
    "websockets",
    "aiortc",
    "av",
    "pydantic",
    "jinja2",
    "pystray",
    "PIL",
    "psutil",
    "Crypto",
    "multipart",
    "itsdangerous",
    "httpx",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="建立含 Python runtime、FFmpeg 與已驗證 YOLO 模型的 Windows 可攜式包。"
    )
    parser.add_argument(
        "--python-runtime",
        type=Path,
        default=ROOT / "runtime" / "python",
        help="已準備好的 embedded Python runtime 目錄；不會由本工具下載或安裝。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "dist" / "MeowKeeperPlus-portable",
        help="輸出目錄；已存在時必須明確指定 --force。",
    )
    parser.add_argument(
        "--include-experimental-models",
        action="store_true",
        help="同時包含 DINOv2 實驗模型；MVP 預設不包含。",
    )
    parser.add_argument("--force", action="store_true", help="明確允許覆寫既有輸出目錄。")
    parser.add_argument("--enable-verified-ptz", action="store_true", help="包含並選用已驗證位置讀回的獨立 PTZ 實驗版；保留官方版本。")
    parser.add_argument("--enable-cpu-temperature", action="store_true", help="包含已準備且校驗通過的實驗性CPU感測器，不安裝驅動或提升權限。")
    parser.add_argument("--dry-run", action="store_true", help="只驗證來源與列出建置計畫，不複製檔案。")
    return parser


def _python_executable(runtime: Path) -> Path:
    return runtime / ("python.exe" if os.name == "nt" else "python")


def _site_packages(runtime: Path) -> Path:
    return runtime / "Lib" / "site-packages"


def _validate_runtime(runtime: Path, *, dry_run: bool) -> list[str]:
    runtime = runtime.resolve()
    if not runtime.is_dir():
        raise ValueError(
            f"找不到 Python runtime：{runtime}。請在開發／建置階段準備官方 embedded runtime；"
            "不會由 MeowKeeper+ 偷偷安裝。"
        )
    executable = _python_executable(runtime)
    if not executable.is_file():
        raise ValueError(f"Python runtime 缺少 {executable.name}，無法建立可攜式包。")
    if os.name == "nt" and not (runtime / "pythonw.exe").is_file():
        raise ValueError("Python runtime 缺少 pythonw.exe，Tray 無法啟動。")
    missing: list[str] = []
    packages = _site_packages(runtime)
    if not dry_run:
        for package in RUNTIME_PACKAGES:
            if not (packages / package).exists() and not (packages / f"{package}.dist-info").exists():
                missing.append(package)
    if missing:
        raise ValueError(
            "Python runtime 缺少必要套件："
            + ", ".join(missing)
            + "。請由建置者準備 runtime；終端使用者不需要執行 pip。"
        )
    if not dry_run:
        try:
            with tempfile.TemporaryDirectory(prefix="meowkeeper-runtime-smoke-") as config_dir:
                environment = os.environ.copy()
                environment["YOLO_CONFIG_DIR"] = config_dir
                environment["MPLCONFIGDIR"] = config_dir
                smoke = subprocess.run(
                    [str(executable), "-c", "import " + ", ".join(RUNTIME_PACKAGES)
                     + "; import websockets.asyncio.client"
                     + "; import uvicorn.protocols.websockets.websockets_impl"
                     + "; from importlib.metadata import version"
                      + "; assert version('websockets') == '15.0.1'"
                      + "; assert version('aiortc') == '1.15.0'"
                      + "; assert version('av') == '17.1.0'"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    env=environment,
                )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Python runtime 套件匯入逾時，已停止建置。") from exc
        if smoke.returncode:
            raise ValueError(
                "Python runtime 必要套件無法由內附執行檔實際匯入；"
                "請在建置機修復後重試，不會產生不完整發布包。"
            )
        yt_dlp_cli = runtime / "Scripts" / "yt-dlp.exe"
        if not yt_dlp_cli.is_file():
            raise ValueError("Python runtime 缺少固定版 yt-dlp.exe 命令列入口，YouTube 來源無法啟動。")
        try:
            version_result = subprocess.run(
                [str(yt_dlp_cli), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("內附 yt-dlp.exe 無法執行或逾時，拒絕建置。") from exc
        version = (version_result.stdout or "").strip().splitlines()
        try:
            version_parts = tuple(int(part) for part in version[0].split("."))
        except (IndexError, ValueError):
            version_parts = ()
        pinned_parts = tuple(int(part) for part in YTDLP_PINNED_VERSION.split("."))
        if version_result.returncode or version_parts != pinned_parts:
            raise ValueError("內附 yt-dlp.exe 版本不符合固定版本，拒絕建置。")
    return [str(item) for item in (executable, packages) if item.exists()]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _copy_tree(source: Path, target: Path) -> None:
    ignored_names = {"__pycache__", ".git", ".venv", ".mypy_cache", ".ruff_cache"}
    required_data_parents = {
        (source / "Lib" / "site-packages" / "torch" / "utils").resolve(),
        (source / "Lib" / "site-packages" / "ultralytics").resolve(),
    }

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in ignored_names
            or name.endswith(".pyc")
            or name.startswith(".env")
            or (
                name in {"reports", "data", "tests"}
                and not (name == "data" and Path(_directory).resolve() in required_data_parents)
            )
        }

    shutil.copytree(source, target, ignore=ignore, dirs_exist_ok=True)


def _copy_file(source: Path, target: Path) -> None:
    if not source.is_file():
        raise ValueError(f"發布包缺少必要檔案：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


PUBLIC_PACKAGE_DOCS = ("README.md", "docs/PUBLIC_USER_GUIDE.md")

# Keep the runtime payload to the application entry points and helpers actually
# required by the installed/portable product. Repository-local diagnostics,
# migration traces, probes, and one-off recovery scripts must not ship.
PUBLIC_RUNTIME_TOOLS = (
    "__init__.py",
    "close_application.py",
    "create_task_scheduler_xml.py",
    "launcher.py",
    "migrate_legacy_app_data.py",
    "portable_start.py",
    "run_host.py",
    "run_tray.py",
    "verify_portable.py",
    "verify_windows_installation.py",
)


def _copy_public_package_docs(source_root: Path, target_root: Path) -> None:
    """Copy only user-facing documentation; internal test logs stay local."""
    for relative_path in PUBLIC_PACKAGE_DOCS:
        _copy_file(source_root / relative_path, target_root / relative_path)


def _copy_public_runtime_tools(source_root: Path, target_root: Path) -> None:
    """Copy only runtime entry points, never local-only diagnostic scripts."""
    for relative_path in PUBLIC_RUNTIME_TOOLS:
        _copy_file(source_root / "tools" / relative_path, target_root / "tools" / relative_path)


def _copy_powershell_script(source: Path, target: Path) -> None:
    """Write UTF-8 BOM so Windows PowerShell 5.1 parses Traditional Chinese."""

    if not source.is_file():
        raise ValueError(f"發布包缺少必要檔案：{source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8-sig"), encoding="utf-8-sig", newline="\r\n")


def _launcher_text() -> str:
    return r"""@echo off
setlocal
set "MEOWKEEPER_ROOT=%~dp0"
set "MEOWKEEPER_PYTHON=%MEOWKEEPER_ROOT%runtime\python\python.exe"
set "PYTHONPATH=%MEOWKEEPER_ROOT%src"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%MEOWKEEPER_PYTHON%" exit /b 2
cd /d "%MEOWKEEPER_ROOT%" || exit /b 2
"%MEOWKEEPER_PYTHON%" "%MEOWKEEPER_ROOT%tools\portable_start.py" %*
exit /b %errorlevel%
"""


def _web_launcher_text() -> str:
    return r"""@echo off
setlocal
set "MEOWKEEPER_ROOT=%~dp0"
set "MEOWKEEPER_PYTHON=%MEOWKEEPER_ROOT%runtime\python\python.exe"
set "PYTHONPATH=%MEOWKEEPER_ROOT%src"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%MEOWKEEPER_PYTHON%" exit /b 2
cd /d "%MEOWKEEPER_ROOT%" || exit /b 2
"%MEOWKEEPER_PYTHON%" "%MEOWKEEPER_ROOT%tools\run_host.py" %*
exit /b %errorlevel%
"""


def _tray_launcher_text() -> str:
    return r"""@echo off
setlocal
set "MEOWKEEPER_ROOT=%~dp0"
set "MEOWKEEPER_PYTHONW=%MEOWKEEPER_ROOT%runtime\python\pythonw.exe"
set "PYTHONPATH=%MEOWKEEPER_ROOT%src"
set "PYTHONDONTWRITEBYTECODE=1"
if not exist "%MEOWKEEPER_PYTHONW%" exit /b 2
cd /d "%MEOWKEEPER_ROOT%" || exit /b 2
"%MEOWKEEPER_PYTHONW%" "%MEOWKEEPER_ROOT%tools\run_tray.py" %*
exit /b %errorlevel%
"""


def _installer_launcher_text(script_name: str) -> str:
    return rf"""@echo off
setlocal
set "MEOWKEEPER_ROOT=%~dp0"
if not exist "%MEOWKEEPER_ROOT%scripts\{script_name}" exit /b 2
cd /d "%MEOWKEEPER_ROOT%" || exit /b 2
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%MEOWKEEPER_ROOT%scripts\{script_name}" %*
exit /b %errorlevel%
"""


def _required_files(include_experimental_models: bool) -> tuple[Path, ...]:
    files = (
        ROOT / "README.md",
        ROOT / "docs" / "BUILD_WINDOWS.md",
        ROOT / "configs" / "botsort_reid.yaml",
        ROOT / "models" / default_model_file().name,
        ROOT / "models" / model_manifest_path().name,
        ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe",
        ROOT / "vendor" / "ffmpeg" / "bin" / "ffprobe.exe",
        ROOT / "vendor" / "ffmpeg" / "LICENSE",
        ROOT / "vendor" / "javascript" / "node.exe",
        ROOT / "vendor" / "javascript" / "LICENSE",
        ROOT / "assets" / "tray" / "meowkeeper-earth-ready.ico",
        ROOT / "assets" / "tray" / "meowkeeper-earth-waiting.ico",
        ROOT / "assets" / "tray" / "meowkeeper-earth-error.ico",
        ROOT / "vendor" / "go2rtc" / "v1.9.14" / "go2rtc.exe",
        ROOT / "vendor" / "go2rtc" / "model_manifest.json",
        ROOT / "vendor" / "go2rtc" / "current.txt",
        ROOT / "vendor" / "go2rtc" / "miot" / "xiaomi-miot.exe",
        ROOT / "vendor" / "go2rtc" / "miot" / "manifest.json",
        ROOT / "vendor" / "go2rtc" / "miot" / "LICENSE",
        ROOT / "scripts" / "Install-MeowKeeperPlus.ps1",
        ROOT / "scripts" / "Manage-CpuTemperature.ps1",
        ROOT / "scripts" / "Manage-CpuSensorTask.ps1",
        ROOT / "Enable-CPU-Temperature.cmd",
        ROOT / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1",
        ROOT / "scripts" / "Uninstall-MeowKeeperPlus.ps1",
        ROOT / "scripts" / "Manage-XiaomiUdpFirewall.ps1",
        ROOT / "scripts" / "Xiaomi-Firewall-Wizard.ps1",
        ROOT / "scripts" / "Xiaomi-Firewall-Wizard.cmd",
        ROOT / "tools" / "verify_portable.py",
        ROOT / "tools" / "verify_windows_installation.py",
        ROOT / "tools" / "migrate_legacy_app_data.py",
    )
    if include_experimental_models:
        return files + (
            ROOT
            / "models"
            / "identity"
            / "dinov2_vits14"
            / "dinov2_vits14_pretrain.pth",
            ROOT / "models" / "identity" / "dinov2_vits14" / "model_manifest.json",
        )
    return files


def _identity_hub_source() -> Path:
    """Return the verified official DINOv2 source cache for offline loading."""

    commit = DEFAULT_IDENTITY_MODEL_RELEASE_TAG.rsplit("@", 1)[-1]
    return ROOT / "data" / "torch" / "hub" / f"facebookresearch_dinov2_{commit}"


def build_portable(
    *,
    python_runtime: Path,
    output: Path,
    include_experimental_models: bool = False,
    enable_verified_ptz: bool = False,
    enable_cpu_temperature: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    runtime = python_runtime.resolve()
    output = output.resolve()
    _validate_runtime(runtime, dry_run=dry_run)
    model_manifest = validate_model_file()
    required = _required_files(include_experimental_models)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError("發布包缺少必要檔案：" + "; ".join(missing))
    if include_experimental_models and not _identity_hub_source().is_dir():
        raise ValueError(
            "發布包缺少已驗證的官方 DINOv2 hub source cache；"
            "請先在建置機準備固定 commit，拒絕讓終端使用者線上下載。"
        )

    plan: dict[str, object] = {
        "package_type": "windows_portable",
        "dry_run": dry_run,
        # This manifest ships with the public package. Keep only the package
        # directory name so a developer's local username/path cannot leak.
        "output": output.name,
        "python_runtime": "runtime/python",
        "runtime_validation": [
            "runtime/python/python.exe",
            "runtime/python/Lib/site-packages",
        ],
        "runtime_source_for_dry_run": str(runtime) if dry_run else None,
        "model": {
            "path": "models/yolo11n.pt",
            "sha256": model_manifest["sha256"],
            "file_size_bytes": model_manifest["file_size_bytes"],
        },
        "experimental_models_included": include_experimental_models,
        "cpu_temperature_helper_included": enable_cpu_temperature,
        "excluded": [".env", "data（除官方 DINOv2 hub source cache）", "reports", "tests", "SQLite 私人資料"],
        "built_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if dry_run:
        return plan
    if output.exists() and not force:
        raise ValueError(f"輸出目錄已存在：{output}；若要覆寫請明確指定 --force。")
    # No cached green result and no skip switch: known incident tests must pass
    # against this exact source tree before creating or replacing a package.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools.verify_regression_gate import source_digest, verify_gate

    plan["regression_gate"] = verify_gate()
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f"{output.name}.staging-{uuid.uuid4().hex[:8]}")
    if staging.exists():
        shutil.rmtree(staging)
    try:
        _copy_tree(runtime, staging / "runtime" / "python")
        _copy_tree(SRC, staging / "src")
        (staging / "release_regression_gate.json").write_text(
            json.dumps(plan["regression_gate"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        _copy_public_runtime_tools(ROOT, staging)
        _copy_tree(ROOT / "vendor" / "ffmpeg", staging / "vendor" / "ffmpeg")
        _copy_file(ROOT / "vendor" / "javascript" / "node.exe", staging / "vendor" / "javascript" / "node.exe")
        _copy_file(ROOT / "vendor" / "javascript" / "LICENSE", staging / "vendor" / "javascript" / "LICENSE")
        _copy_tree(ROOT / "vendor" / "go2rtc", staging / "vendor" / "go2rtc")
        if enable_cpu_temperature:
            from meowkeeper.core.analytics.cpu_temperature import HelperCpuProvider
            sensor = HelperCpuProvider(ROOT / "vendor" / "cpu-sensor")
            sensor._validate()  # hashes only; no process/driver access during packaging
            helper_manifest = json.loads((sensor.folder / 'manifest.json').read_text(encoding='utf-8'))
            if helper_manifest.get('helper_source_sha256') != _file_sha256(ROOT / 'tools/cpu_sensor_helper/Program.cs').lower():
                raise ValueError('測溫 helper 與目前原始碼不一致，請先重新編譯與 stage。')
            _copy_tree(sensor.folder, staging / "vendor" / "cpu-sensor")
        if enable_verified_ptz:
            from meowkeeper.integrations.go2rtc.ptz_backend import PTZ_SHA256

            binary = ROOT / "reports" / "ptz-feedback-fix" / "go2rtc-ptz-feedback.exe"
            if _file_sha256(binary).upper() != PTZ_SHA256:
                raise ValueError("已驗證 PTZ binary 校驗失敗，停止建置。")
            destination = staging / "vendor" / "go2rtc" / "ptz-feedback"
            _copy_file(binary, destination / "go2rtc.exe")
            _copy_file(ROOT / "reports" / "ptz-feedback-fix" / "manifest.json", destination / "manifest.json")
            (destination.parent / "backend.json").write_text(
                json.dumps({"backend": "ptz-feedback"}) + "\n", encoding="utf-8"
            )
        _copy_tree(ROOT / "assets", staging / "assets")
        _copy_file(ROOT / "configs" / "botsort_reid.yaml", staging / "configs" / "botsort_reid.yaml")
        _copy_file(ROOT / "configs" / "release-regressions.json", staging / "configs" / "release-regressions.json")
        if (ROOT / "configs/update-public.pem").is_file():
            _copy_file(ROOT / "configs/update-public.pem", staging / "configs/update-public.pem")
        _copy_public_package_docs(ROOT, staging)
        _copy_file(ROOT / "models" / "yolo11n.pt", staging / "models" / "yolo11n.pt")
        _copy_file(ROOT / "models" / "model_manifest.json", staging / "models" / "model_manifest.json")
        from meowkeeper.core.inference.accelerated_yolo import verified_export
        if verified_export(default_model_file()) is None:
            raise ValueError("缺少已驗證 GPU 匯出模型；請於建置階段執行 tools/export_accelerated_model.py。")
        _copy_tree(ROOT / "models" / "accelerated", staging / "models" / "accelerated")
        # Repository Start-*.cmd/PowerShell launchers require .venv. They
        # must not appear in a portable package that only has runtime/python.
        if include_experimental_models:
            _copy_tree(ROOT / "models" / "identity", staging / "models" / "identity")
            _copy_tree(
                _identity_hub_source(),
                staging / "data" / "torch" / "hub" / _identity_hub_source().name,
            )
            # PyTorch writes this empty trust metadata file when torch.hub loads
            # the bundled official source. Include it before hashing so a local
            # model smoke test cannot make the installer reject an otherwise
            # unchanged package as having an unlisted runtime file.
            trusted_list = staging / "data" / "torch" / "hub" / "trusted_list"
            trusted_list.parent.mkdir(parents=True, exist_ok=True)
            trusted_list.write_bytes(b"")
        launcher = staging / "MeowKeeperPlus.cmd"
        _copy_powershell_script(ROOT / "scripts/New-PortableShortcut.ps1", staging / "scripts/New-PortableShortcut.ps1")
        (staging / "Create-Launcher-Icon.cmd").write_text(
            '@echo off\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\New-PortableShortcut.ps1"\r\n', encoding="utf-8")
        launcher.write_text(_launcher_text(), encoding="utf-8", newline="\r\n")
        (staging / "MeowKeeperPlusDashboard.cmd").write_text(
            _web_launcher_text(), encoding="utf-8", newline="\r\n"
        )
        (staging / "MeowKeeperPlusTray.cmd").write_text(
            _tray_launcher_text(), encoding="utf-8", newline="\r\n"
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Install-MeowKeeperPlus.ps1",
            staging / "scripts" / "Install-MeowKeeperPlus.ps1",
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Manage-CpuTemperature.ps1",
            staging / "scripts" / "Manage-CpuTemperature.ps1",
        )
        _copy_file(ROOT / "Enable-CPU-Temperature.cmd", staging / "Enable-CPU-Temperature.cmd")
        _copy_powershell_script(ROOT / "scripts/Manage-CpuSensorTask.ps1", staging / "scripts/Manage-CpuSensorTask.ps1")
        _copy_powershell_script(
            ROOT / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1",
            staging / "scripts" / "Launch-Installed-MeowKeeperPlus.ps1",
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Uninstall-MeowKeeperPlus.ps1",
            staging / "scripts" / "Uninstall-MeowKeeperPlus.ps1",
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Manage-XiaomiUdpFirewall.ps1",
            staging / "scripts" / "Manage-XiaomiUdpFirewall.ps1",
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Sync-PtzUdpFirewall.ps1",
            staging / "scripts" / "Sync-PtzUdpFirewall.ps1",
        )
        _copy_powershell_script(
            ROOT / "scripts" / "Xiaomi-Firewall-Wizard.ps1",
            staging / "scripts" / "Xiaomi-Firewall-Wizard.ps1",
        )
        _copy_file(ROOT / "scripts" / "Xiaomi-Firewall-Wizard.cmd", staging / "scripts" / "Xiaomi-Firewall-Wizard.cmd")
        (staging / "Install-MeowKeeperPlus.cmd").write_text(
            _installer_launcher_text("Install-MeowKeeperPlus.ps1"), encoding="utf-8", newline="\r\n"
        )
        (staging / "Uninstall-MeowKeeperPlus.cmd").write_text(
            _installer_launcher_text("Uninstall-MeowKeeperPlus.ps1"), encoding="utf-8", newline="\r\n"
        )
        if source_digest() != plan["regression_gate"]["source_sha256"]:
            raise ValueError("複製期間程式發生變更，發布證據失效；未替換舊發布包。")
        file_records = []
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name != "build_manifest.json":
                file_records.append(
                    {
                        "path": str(path.relative_to(staging)).replace("\\", "/"),
                        "size_bytes": path.stat().st_size,
                        "sha256": _file_sha256(path),
                    }
                )
        plan["files"] = file_records
        (staging / "build_manifest.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if output.exists():
            shutil.rmtree(output)
        staging.replace(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        plan = build_portable(
            python_runtime=args.python_runtime,
            output=args.output,
            include_experimental_models=args.include_experimental_models,
            enable_verified_ptz=args.enable_verified_ptz,
            enable_cpu_temperature=args.enable_cpu_temperature,
            force=args.force,
            dry_run=args.dry_run,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"可攜式建置失敗：{exc}", file=sys.stderr)
        return 2
    summary = plan if args.dry_run else {
        "package_type": plan["package_type"],
        "output": str(args.output.resolve()),
        "file_count": len(plan["files"]),
        "model_sha256": plan["model"]["sha256"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("已完成建置計畫驗證。" if args.dry_run else "Windows 可攜式包建置完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
