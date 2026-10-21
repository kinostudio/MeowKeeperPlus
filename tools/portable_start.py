"""One portable entry: bundled Tray starts/reuses Host and opens Dashboard."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from meowkeeper.runtime_paths import default_dashboard_config_path, default_data_root


def dashboard_port(root: Path) -> int:
    data_root = default_data_root(root)
    path = default_dashboard_config_path(root, data_root)
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        port = data.get("dashboard", {}).get("port", 8000)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Dashboard 通訊埠設定不合法。")
        return port
    return 8000


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    pythonw = root / "runtime/python/pythonw.exe"
    try:
        if not pythonw.is_file():
            raise RuntimeError("發布包缺少內附 Python 視窗 runtime；請重新取得完整可攜包。")
        args = list(sys.argv[1:] if argv is None else argv)
        command = [str(pythonw), str(root / "tools/run_tray.py"), "--background", "--open-dashboard"]
        if "--base-url" not in args:
            command += ["--base-url", f"http://127.0.0.1:{dashboard_port(root)}"]
        logs = default_data_root(root) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
        with (logs / "startup.log").open("ab") as output:
            subprocess.Popen(command + args, cwd=root, env=env, shell=False,
                             stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"喵喵守護者+ 啟動未完成：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
