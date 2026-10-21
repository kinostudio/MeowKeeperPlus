"""只下載固定官方PawnIO供靜態審查；不執行安裝器或變更Windows。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe"
SHA256 = "1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032"


def main() -> int:
    import requests
    folder = ROOT / "reports/cpu-sensor-prototype/pawnio-audit"
    folder.mkdir(parents=True, exist_ok=True)
    installer = folder / "PawnIO_setup-2.2.0.exe"
    if installer.is_file() and installer.stat().st_size <= 4_000_000:
        content = installer.read_bytes()
    else:
        with requests.get(URL, timeout=30, stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > 4_000_000:
                    raise ValueError("官方驅動下載大小不符，未執行。")
                chunks.append(chunk)
        content = b"".join(chunks)
    if hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("官方驅動雜湊不符，未執行。")
    installer.write_bytes(content)
    powershell = Path(shutil.which("pwsh") or (Path(os.environ.get("WINDIR", r"C:\Windows"))
                                              / "System32/WindowsPowerShell/v1.0/powershell.exe"))
    # Direct read-only cmdlets; do not change machine/process execution policy.
    literal = str(installer).replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop'; $f=Get-Item -LiteralPath '" + literal + "';"
        "$s=Get-AuthenticodeSignature -LiteralPath $f.FullName;"
        "[ordered]@{status=[string]$s.Status;signer_subject=$s.SignerCertificate.Subject;"
        "signer_thumbprint=$s.SignerCertificate.Thumbprint;timestamp_signer=$s.TimeStamperCertificate.Subject;"
        "file_version=$f.VersionInfo.FileVersion;file_bytes=$f.Length;"
        "sha256=(Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant();"
        "executed=$false}|ConvertTo-Json -Depth 3 -Compress"
    )
    environment = os.environ.copy()
    # PowerShell 7's inherited module search path can break Windows PowerShell's
    # built-in Security module. Let this child use its own standard module path.
    environment.pop("PSModulePath", None)
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", command],
                            capture_output=True, timeout=30, shell=False, check=False,
                            env=environment,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or len(result.stdout) > 16384:
        raise ValueError("Windows簽章檢查未完成；未安裝驅動。")
    # PowerShell emits the computer's console encoding; JSON escapes are ASCII for these fields.
    report = json.loads(result.stdout.decode("utf-8-sig"))
    report.update(source_url=URL, version="2.2.0", expected_sha256=SHA256, installed=False)
    if report.get("sha256") != SHA256 or report.get("executed") is not False:
        raise ValueError("簽章審查回應不符，未安裝驅動。")
    (folder / "signature.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
