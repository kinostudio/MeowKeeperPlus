"""相容性別名；正式 Web 啟動入口為 ``tools/run_host.py``。"""

from __future__ import annotations

try:
    from run_host import build_parser, main
except ImportError:  # pragma: no cover - package import fallback
    from tools.run_host import build_parser, main


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
