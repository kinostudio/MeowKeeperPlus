"""Safe release labels from this running checkout, never from a stale Host."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def release_information(root: Path | None = None) -> dict[str, str]:
    root = (root or Path(__file__).resolve().parents[4]).resolve()
    try:
        manifest = json.loads((root / "build_manifest.json").read_text(encoding="utf-8"))
        match = re.search(r"MeowKeeperPlus-portable-(\d{4}-\d{2}-\d{2})-(r\d+)(?:$|[/\\])",
                          str(manifest.get("output", "")))
        if manifest.get("package_type") != "windows_portable" or match is None:
            raise ValueError("release metadata unavailable")
        date, version = match.groups()
        installed_root = Path(os.getenv("PROGRAMDATA", "C:/ProgramData")) / "MeowKeeperPlus" / "App"
        mode = "正式安裝版" if root == installed_root.resolve() else "可攜版"
        return {"version": version, "date": date, "mode": mode, "cache_key": f"{date}-{version}"}
    except (OSError, ValueError, TypeError, AttributeError):
        digest = hashlib.sha256()
        static = root / "src" / "meowkeeper" / "app" / "web" / "static"
        try:
            for path in sorted(static.rglob("*")):
                if path.is_file() and not path.is_symlink() and path.suffix in {".css", ".js", ".svg"}:
                    digest.update(path.relative_to(static).as_posix().encode())
                    digest.update(path.read_bytes())
        except OSError:
            digest.update(b"unavailable")
        return {"version": "開發版", "date": "未打包", "mode": "本機開發",
                "cache_key": "dev-" + digest.hexdigest()[:16]}
