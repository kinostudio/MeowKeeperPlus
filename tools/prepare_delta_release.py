"""Create only changed-file payloads, bound to two exact verified packages."""
from __future__ import annotations

import argparse
import base64
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools")]
from verify_portable import verify_portable
from verify_regression_gate import verify_package_evidence

from meowkeeper.app.delta_update import digest, records, validate_manifest, version

R116_RELEASE_SOURCE_BASELINE = {
    "src/meowkeeper/app/web/state.py": (31065, "6c76ccd9482c006d8c67f232b5f121978f05c813d5b1700c055148867b4ba6fa"),
}


def validate_release_baseline_overrides(base_version: str, target_version: str, overrides: dict) -> None:
    target_number = int(target_version.removeprefix("r"))
    if base_version != "r116" or target_number < 118:
        return
    if set(overrides) != set(R116_RELEASE_SOURCE_BASELINE):
        raise ValueError("r116 差量必須且只能包含已知 state.py 基準修正；請停止發布並補上精確來源覆寫。")
    for name, (expected_size, expected_sha256) in R116_RELEASE_SOURCE_BASELINE.items():
        path = overrides[name]
        if not path.is_file() or path.stat().st_size != expected_size or digest(path) != expected_sha256:
            raise ValueError("r116 差量的 state.py 基準修正大小或 SHA-256 不符；停止發布。")


def build_delta(base: Path, target: Path, output: Path, key, *, base_overrides=None) -> dict:
    old_manifest = json.loads((base / "build_manifest.json").read_text(encoding="utf-8"))
    base_version = version(old_manifest)
    verify_portable(base, allow_retired_r116_launchers=base_version == "r116")
    verify_portable(target)
    verify_package_evidence(target)
    old = records(old_manifest)
    overrides = [{"path": name, "size_bytes": path.stat().st_size, "sha256": digest(path)}
                 for name, path in (base_overrides or {}).items()]
    if overrides:
        old.update(records({"files": overrides}))
    new = records(json.loads((target / "build_manifest.json").read_text(encoding="utf-8")))
    target_version = version(json.loads((target / "build_manifest.json").read_text(encoding="utf-8")))
    changed = sorted({name for name, record in new.items() if old.get(name) != record} | {"build_manifest.json"})
    deleted = sorted(set(old) - set(new))
    output.mkdir(parents=True, exist_ok=True)
    asset = f"MeowKeeperPlus-{base_version}-to-{target_version}-delta.zip"
    archive = output / asset
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for name in changed:
            bundle.write(target / name, name)
    manifest = {
        "schema": 2, "kind": "file_delta", "repository": "kinostudio/MeowKeeperPlus",
        "base_version": base_version, "version": target_version, "tag": target_version, "asset": asset,
        "base_manifest_sha256": digest(base / "build_manifest.json"),
        "base_overrides": overrides,
        "target_manifest_sha256": digest(target / "build_manifest.json"),
        "size": archive.stat().st_size, "sha256": digest(archive),
        "changed": [{"path": name, "size_bytes": (target / name).stat().st_size, "sha256": digest(target / name)} for name in changed],
        "deleted": deleted,
    }
    validate_manifest(manifest)
    from Crypto.Signature import eddsa
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    envelope = {"manifest": manifest, "signature": base64.b64encode(eddsa.new(key, "rfc8032").sign(canonical)).decode()}
    # Separate name: `update-manifest.json` is reserved for the full signed package.
    with (output / "update-manifest-delta.json").open("x", encoding="utf-8") as destination:
        json.dump(envelope, destination, ensure_ascii=False, indent=2)
    return {"status": "passed", "asset": asset, "bytes": manifest["size"], "changed_files": len(changed),
            "deleted_files": len(deleted), "heavy_files": [name for name in changed if name.startswith(("runtime/", "vendor/", "models/"))]}


def main():
    parser = argparse.ArgumentParser(description="製作具官方簽章與固定基準的差量包；不安裝、不上傳。")
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--signing-root", type=Path, default=ROOT)
    parser.add_argument("--base-source-override", action="append", default=[], help="核准已知第一方公開修正：src/相對路徑=來源檔案")
    args = parser.parse_args()
    overrides = {}
    for value in args.base_source_override:
        name, file = value.split("=", 1)
        overrides[name] = Path(file)
    base_manifest = json.loads((args.base / "build_manifest.json").read_text(encoding="utf-8"))
    target_manifest = json.loads((args.target / "build_manifest.json").read_text(encoding="utf-8"))
    validate_release_baseline_overrides(version(base_manifest), version(target_manifest), overrides)
    from Crypto.PublicKey import ECC
    key = ECC.import_key((args.signing_root / "data/release-signing/ed25519-private.pem").read_bytes())
    if key.public_key().export_key(format="PEM") != (ROOT / "configs/update-public.pem").read_text().strip():
        raise ValueError("更新信任根不一致，停止差量簽署。")
    print(json.dumps(build_delta(args.base, args.target, args.output, key, base_overrides=overrides)))


if __name__ == "__main__":
    main()
