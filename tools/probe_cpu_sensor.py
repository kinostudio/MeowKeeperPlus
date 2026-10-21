"""隔離CPU溫度原型：不接入Host、不提權、不安裝驅動或修改正式包。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NUGET_URL = "https://api.nuget.org/v3-flatcontainer/librehardwaremonitorlib/0.9.6/librehardwaremonitorlib.0.9.6.nupkg"
NUGET_SHA256 = "60db5e0d9135f4658b385cae139d3b9eaeac0a8e867d322d20b5dbe4c4039197"
DLL_MEMBER = "runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll"


def prepare(folder: Path) -> dict:
    """Explicit prototype-only preparation from official NuGet, pinned hash."""
    import requests
    response = requests.get(NUGET_URL, timeout=20)
    response.raise_for_status()
    data = response.content
    if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != NUGET_SHA256:
        raise ValueError("感測器元件校驗失敗；未執行。")
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as package:
        dll = package.read(DLL_MEMBER)
        (folder / "LibreHardwareMonitorLib.dll").write_bytes(dll)
        (folder / "LibreHardwareMonitorLib.nuspec").write_bytes(package.read("LibreHardwareMonitorLib.nuspec"))
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    result = subprocess.run([str(compiler), "/nologo", "/platform:x64", "/target:exe",
                             "/r:System.Web.Extensions.dll", f"/out:{folder / 'MeowKeeperCpuSensor.exe'}",
                             str(ROOT / "tools/cpu_sensor_helper/Program.cs")],
                            capture_output=True, timeout=30, shell=False, check=False)
    if result.returncode:
        raise RuntimeError("CPU感測器原型編譯失敗；未啟動。")
    return {"nuget_sha256": NUGET_SHA256, "nuget_download_bytes": len(data),
            "dll_sha256": hashlib.sha256(dll).hexdigest(), "dll_bytes": len(dll)}


def probe(folder: Path) -> dict:
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    result = subprocess.run([str(folder / "MeowKeeperCpuSensor.exe")],
                            input=f"sample {request_id}\nstop\n", capture_output=True, text=True,
                            encoding="utf-8", timeout=10, shell=False, check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > 65536:
        raise ValueError("感測器原型回應失敗；未接入Host。")
    data = json.loads(result.stdout.lstrip("\ufeff"))
    if data.get("request_id") != request_id:
        raise ValueError("感測器原型回應識別不符。")
    data.pop("request_id")
    data["probe_total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="下載固定NuGet及編譯隔離原型，不安裝驅動。")
    args = parser.parse_args()
    folder = ROOT / "reports/cpu-sensor-prototype"
    evidence = prepare(folder) if args.prepare else {}
    evidence.update(probe(folder))
    evidence["scope"] = "isolated_prototype"
    evidence["host_integrated"] = False
    evidence["driver_install_performed_by_probe"] = False
    evidence["runtime_added_bytes"] = 0
    evidence["prototype_files_bytes"] = sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
    (folder / "result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
