"""Launch the installed MeowKeeper+ Host while serving live Web UI templates/static from Workspace."""

from __future__ import annotations

import functools
import importlib.util
import sys
from pathlib import Path

INSTALLED_APP = Path(r"C:\ProgramData\MeowKeeperPlus\App")
WORKSPACE_ROOT = Path(r"C:\Users\user\Desktop\MeowKeeperPlus-Workspace")
WORKSPACE_SRC = WORKSPACE_ROOT / "src"
WORKSPACE_WEB = WORKSPACE_SRC / "meowkeeper" / "app" / "web"

sys.path[:] = [
    str(WORKSPACE_SRC),
    str(INSTALLED_APP / "src"),
    str(INSTALLED_APP),
    *[p for p in sys.path if "MeowKeeperPlus-Workspace" not in p],
]

import os
os.environ.setdefault("MEOWKEEPER_XIAOMI_MISS_PTZ_ENABLED", "1")

import meowkeeper.runtime as _rt

_rt.project_root = lambda: INSTALLED_APP

import meowkeeper.app.web.api as web_api
import meowkeeper.app.web.main as web_main

_orig_create_app = web_api.create_app


@functools.wraps(_orig_create_app)
def _patched_create_app(*args, **kwargs):
    kwargs.setdefault("template_directory", WORKSPACE_WEB / "templates")
    kwargs.setdefault("static_directory", WORKSPACE_WEB / "static")
    return _orig_create_app(*args, **kwargs)


web_api.create_app = _patched_create_app
web_main._create_api_app = _patched_create_app

run_host_script = WORKSPACE_ROOT / "tools" / "run_host.py"
if not run_host_script.is_file():
    run_host_script = INSTALLED_APP / "tools" / "run_host.py"
spec = importlib.util.spec_from_file_location("run_host", run_host_script)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

if __name__ == "__main__":
    raise SystemExit(module.main())
