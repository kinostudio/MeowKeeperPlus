"""建置前強制執行已知故障回歸；不安裝、不呼叫外部服務、不輸出測試stderr。"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "configs" / "release-regressions.json"


def load_registry(path: Path = REGISTRY) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not data.get("incidents"):
        raise ValueError("故障回歸台帳格式不正確或沒有項目。")
    identifiers = set()
    for item in data["incidents"]:
        key = item.get("id", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9-]{2,70}", key) or key in identifiers:
            raise ValueError("故障識別碼無效或重複。")
        identifiers.add(key)
        if not item.get("title") or not item.get("tests"):
            raise ValueError("故障必須包含說明及可執行回歸測試。")
        for name in item["tests"]:
            if not isinstance(name, str) or not re.fullmatch(
                r"tests\.test_[a-z0-9_]+\.[A-Za-z0-9_]+\.test_[A-Za-z0-9_]+", name
            ):
                raise ValueError("回歸項目必須是明確的本機unittest方法。")
    return data["incidents"]


def source_digest(root: Path = ROOT) -> str:
    """綁定本次第一方程式／測試／設定，不採用上輪測試結果。"""
    digest = hashlib.sha256()
    for path in sorted((*root.glob("*.cmd"), *root.glob("*.ps1"))):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    for folder in ("src", "tools", "scripts", "tests", "configs"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".ps1", ".cmd", ".js", ".cjs", ".json", ".html", ".css", ".cs"}:
                if "__pycache__" in path.parts:
                    continue
                digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
                digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def run_worker() -> dict:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    entries = load_registry()
    names = sorted({name for item in entries for name in item["tests"]})
    loader = unittest.TestLoader()
    # Error tracebacks may contain paths or test values; publish identifiers only.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        suite = loader.loadTestsFromNames(names)
        result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    failed = sorted({test.id() for test, _ in result.failures + result.errors})
    skipped = sorted(test.id() for test, _ in result.skipped)
    passed = (result.wasSuccessful() and not skipped and not result.expectedFailures
              and not loader.errors and result.testsRun == len(names))
    return {"status": "passed" if passed else "failed", "tests_run": result.testsRun,
            "failed_tests": failed, "skipped_tests": skipped, "incident_ids": [x["id"] for x in entries]}


def verify_gate(*, timeout: float = 180) -> dict:
    before = source_digest()
    registry_hash = hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
            shell=False, check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("故障回歸門檻逾時，已阻止建置；不使用舊結果。") from None
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise ValueError("故障回歸沒有有效結果，已阻止建置；請在本機執行對應測試。") from None
    entries = load_registry()
    expected_tests = len({name for item in entries for name in item["tests"]})
    if (completed.returncode or not isinstance(result, dict) or result.get("status") != "passed"
            or type(result.get("tests_run")) is not int or result["tests_run"] != expected_tests
            or result.get("failed_tests") != [] or result.get("skipped_tests") != []
            or set(result.get("incident_ids", [])) != {item["id"] for item in entries}):
        raise ValueError("已知故障回歸失敗或被略過，已阻止建置；請執行 verify_regression_gate.py --worker 查看安全摘要。")
    if before != source_digest():
        raise ValueError("回歸期間程式發生變更，測試證據已失效，請重跑後再建置。")
    return {**result, "source_sha256": before, "registry_sha256": registry_hash,
            "checked_at_utc": datetime.now(timezone.utc).isoformat()}


def verify_package_evidence(package: Path) -> dict:
    """發布簽章前拒絕缺漏、過期或與包內manifest不一致的回歸證據。"""
    try:
        report = json.loads((package / "release_regression_gate.json").read_text(encoding="utf-8"))
        manifest = json.loads((package / "build_manifest.json").read_text(encoding="utf-8"))
        entries = load_registry()
        expected = {item["id"] for item in entries}
        expected_tests = len({name for item in entries for name in item["tests"]})
        if (not isinstance(report, dict) or report != manifest.get("regression_gate")
                or report.get("status") != "passed" or report.get("failed_tests") != []
                or report.get("skipped_tests") != [] or type(report.get("tests_run")) is not int
                or report["tests_run"] != expected_tests or set(report.get("incident_ids", [])) != expected
                or report.get("registry_sha256") != hashlib.sha256(REGISTRY.read_bytes()).hexdigest()
                or report.get("source_sha256") != source_digest()):
            raise ValueError("invalid")
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError("發布包缺少本版有效故障回歸證據，請重新建置；未產生簽章或ZIP。") from None
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="執行已知故障的強制回歸門檻。")
    parser.add_argument("--worker", action="store_true", help="輸出本機測試安全摘要，不安裝程式。")
    args = parser.parse_args()
    try:
        result = run_worker() if args.worker else verify_gate()
    except (OSError, ValueError, TypeError):
        result = {"status": "failed", "message_zh_tw": "故障回歸門檻未通過；未建立發布包。"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
