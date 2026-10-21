"""建立乾淨完整 ZIP 和 Ed25519 簽章；不自動上傳或安裝。"""
import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from verify_portable import verify_portable
from verify_regression_gate import verify_package_evidence

from meowkeeper.app.updates import MAX_ARCHIVE, REPOSITORY, canonical


def main():
    from Crypto.PublicKey import ECC
    from Crypto.Signature import eddsa

    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path)
    parser.add_argument("--version")
    parser.add_argument("--init-key", action="store_true")
    args = parser.parse_args()
    key_file = ROOT / "data/release-signing/ed25519-private.pem"
    public_file = ROOT / "configs/update-public.pem"
    if args.init_key:
        if key_file.exists() or public_file.exists():
            raise ValueError("簽章金鑰已存在；不得覆蓋更新信任根。")
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key = ECC.generate(curve="Ed25519")
        with key_file.open("x") as output:
            output.write(key.export_key(format="PEM"))
        with public_file.open("x") as output:
            output.write(key.public_key().export_key(format="PEM"))
        print("已建立更新信任根；私人金鑰只在被排除的 data 目錄，請離線保管。")
        return
    import re
    if not args.package or not re.fullmatch(r"r[1-9]\d*", args.version or ""):
        raise ValueError("必須指定發布包與版本。")
    verify_portable(args.package)
    verify_package_evidence(args.package)
    key = ECC.import_key(key_file.read_bytes())
    if key.public_key().export_key(format="PEM") != public_file.read_text().strip():
        raise ValueError("更新公私鑰不一致。")
    archive = args.package.parent / f"MeowKeeperPlus-{args.version}-windows-x64.zip"
    if archive.exists():
        raise ValueError("發布 ZIP 已存在，不覆蓋。")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for file in sorted(args.package.rglob("*")):
            if file.is_file():
                bundle.write(file, file.relative_to(args.package).as_posix())
    if archive.stat().st_size > MAX_ARCHIVE:
        archive.unlink()
        raise ValueError("ZIP 超過更新容量上限，未產生可發布 manifest。")
    digest = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    manifest = {"schema": 1, "repository": REPOSITORY, "version": args.version,
                "tag": args.version, "asset": archive.name, "size": archive.stat().st_size,
                "sha256": digest.hexdigest()}
    if archive.stat().st_size > 2 * 1024**3:
        parts = []
        with archive.open("rb") as source:
            index = 1
            while source.tell() < archive.stat().st_size:
                part = archive.with_name(archive.name + f".part{index:02d}")
                remaining, part_digest, size = 1024**3, hashlib.sha256(), 0
                with part.open("xb") as output:
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        part_digest.update(chunk)
                        remaining -= len(chunk)
                        size += len(chunk)
                parts.append({"name": part.name, "size": size, "sha256": part_digest.hexdigest()})
                index += 1
        manifest["parts"] = parts
    envelope = {"manifest": manifest, "signature": base64.b64encode(
        eddsa.new(key, "rfc8032").sign(canonical(manifest))).decode()}
    (args.package.parent / "update-manifest.json").write_text(json.dumps(envelope, indent=2), encoding="utf-8")
    print(json.dumps({"archive": str(archive), "size": manifest["size"], "sha256": manifest["sha256"]}))


if __name__ == "__main__":
    main()
