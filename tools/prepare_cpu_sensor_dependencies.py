"""固定官方LHM發布檔的隔離依賴準備；不執行GUI或安裝驅動。"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip"
SHA256 = "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001"
# Explicit CPU library closure candidate, not the GUI's controls/plot/task libraries.
LIBRARIES = {
    "BlackSharp.Core.dll", "DiskInfoToolkit.dll", "HidSharp.dll", "RAMSPDToolkit-NDD.dll", "Microsoft.Bcl.AsyncInterfaces.dll",
    "Microsoft.Bcl.HashCode.dll", "System.Buffers.dll", "System.CodeDom.dll", "System.Collections.Immutable.dll",
    "System.Formats.Nrbf.dll", "System.IO.Pipelines.dll", "System.Memory.dll", "System.Numerics.Vectors.dll",
    "System.Reflection.Metadata.dll", "System.Resources.Extensions.dll", "System.Runtime.CompilerServices.Unsafe.dll",
    "System.Security.AccessControl.dll", "System.Security.Principal.Windows.dll", "System.Text.Encodings.Web.dll",
    "System.Threading.AccessControl.dll", "System.Text.Json.dll", "System.Threading.Tasks.Extensions.dll",
}


def main() -> int:
    import requests
    folder = ROOT / "reports/cpu-sensor-prototype"
    archive = folder / "official-lhm-v0.9.6.zip"
    if archive.is_file() and archive.stat().st_size <= 10_000_000:
        content = archive.read_bytes()
    else:
        response = requests.get(URL, timeout=30)
        response.raise_for_status()
        content = response.content
    if len(content) > 10_000_000 or hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("官方感測器依賴校驗失敗，未執行。")
    archive.write_bytes(content)
    compiler = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    executable = folder / "MeowKeeperCpuSensor.exe"
    result = subprocess.run([str(compiler), "/nologo", "/platform:x64", "/target:exe",
                             "/r:System.Web.Extensions.dll", f"/out:{executable}",
                             str(ROOT / "tools/cpu_sensor_helper/Program.cs")],
                            capture_output=True, timeout=30, shell=False, check=False)
    if result.returncode:
        raise ValueError("CPU原型編譯失敗。")
    entries = {}
    with zipfile.ZipFile(io.BytesIO(content)) as package:
        for name in sorted(LIBRARIES):
            data = package.read(name)
            if len(data) > 10_000_000:
                raise ValueError("感測器依賴大小不符。")
            (folder / name).write_bytes(data)
            entries[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    # No driver access occurs during ReflectionOnlyLoadFrom.
    metadata = {}
    for name in ["LibreHardwareMonitorLib.dll", *sorted(LIBRARIES)]:
        result = subprocess.run([str(executable), "--inspect", name], capture_output=True, timeout=5,
                                shell=False, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode or len(result.stdout) > 65536:
            raise ValueError("感測器依賴中繼資料無法讀取。")
        metadata[name] = json.loads(result.stdout.decode("utf-8-sig"))
    provided = {item["name"] for item in metadata.values()}
    framework = {"System", "System.Core", "System.Management", "System.Numerics", "System.ValueTuple", "mscorlib"}
    unresolved = {reference for item in metadata.values() for reference in item["references"]} - provided - framework
    if unresolved:
        raise ValueError("感測器仍有未審查的依賴；未接入Host。")
    report = {"source_url": URL, "release_sha256": SHA256, "files": entries, "metadata": metadata,
              "additional_dll_bytes": sum(item["bytes"] for item in entries.values()),
              "framework_references": sorted(framework), "unresolved_library_names": sorted(unresolved),
              "runtime_added": False, "gui_executed": False, "driver_installed": False}
    (folder / "dependencies.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("release_sha256", "additional_dll_bytes", "runtime_added", "gui_executed", "driver_installed")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
