"""準備repository內CPU感測器元件，不建包、不安裝驅動或啟動Host。"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    source, target = ROOT / "reports/cpu-sensor-prototype", ROOT / "vendor/cpu-sensor"
    if target.exists() and target.resolve() != target.absolute():
        raise ValueError("感測器元件目錄不可是外部連結。")
    report = json.loads((source / "dependencies.json").read_text(encoding="utf-8"))
    if report.get("release_sha256") != "086d9f1b5a99e643edc2cfaaac16051685b551e4c5ac0b32a57c58c0e529c001":
        raise ValueError("感測器依賴來源校驗失敗。")
    entries = {name: value["sha256"] for name, value in report["files"].items()}
    entries["LibreHardwareMonitorLib.dll"] = "6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0"
    entries["MeowKeeperCpuSensor.exe"] = hashlib.sha256((source / "MeowKeeperCpuSensor.exe").read_bytes()).hexdigest()
    for name, digest in entries.items():
        if Path(name).name != name or hashlib.sha256((source / name).read_bytes()).hexdigest() != digest:
            raise ValueError("感測器元件校驗失敗。")
    target.mkdir(parents=True, exist_ok=True)
    for name in (*entries, "LICENSE", "THIRD-PARTY-NOTICES.txt"):
        shutil.copyfile(source / name, target / name)
    manifest = {"schema_version": 1, "files": entries, "driver_bundled": False,
                "helper_source_sha256": hashlib.sha256((ROOT / 'tools/cpu_sensor_helper/Program.cs').read_bytes()).hexdigest(),
                "runtime_bundled": False, "framework_required": "4.7.2+",
                "source": "LibreHardwareMonitorLib 0.9.6", "experimental": True}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "file_count": len(entries), "driver_installed": False,
                      "package_built": False, "target": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
