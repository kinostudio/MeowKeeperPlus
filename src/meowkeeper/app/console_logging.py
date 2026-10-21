"""可讀、低噪音且不印出 URL／查詢秘密的 Dashboard 存取日誌。"""

import copy
import logging


def _access(record: logging.LogRecord) -> tuple[str, str, int] | None:
    if not isinstance(record.args, tuple) or len(record.args) != 5:
        return None
    _client, method, target, _version, code = record.args
    if not isinstance(target, str) or not isinstance(code, int):
        return None
    return str(method), target.split("?", 1)[0], code


class QuietPreviewFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        data = _access(record)
        if data is None:
            return True
        _method, path, code = data
        return not (code < 400 and path in {
            "/healthz", "/api/v1/health", "/api/v1/status", "/api/v1/preview/jpeg",
            "/api/v1/preview/status", "/api/v1/analytics/dashboard",
        })


class ChineseAccessFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = _access(record)
        if data is None:
            return "[資訊] Dashboard 存取紀錄：非預期格式（內容已隱藏）。"
        method, path, code = data
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}:
            method = "其他"
        # Never emit arbitrary paths, IDs, query strings, client addresses or headers.
        area = ("登入／帳號" if path.startswith(("/login", "/setup", "/api/v1/auth")) else
                "寵物資料" if path.startswith(("/settings/pets", "/api/v1/pets")) else
                "事件／回報" if path.startswith(("/events", "/api/v1/events")) else
                "即時畫面" if path.startswith(("/live", "/api/v1/preview", "/api/v1/live")) else
                "攝影機設定" if path.startswith(("/cameras", "/api/v1/cameras")) else "儀表板")
        result = ("成功" if code < 400 else "尚未登入或授權已到期" if code == 401 else
                  "權限／安全驗證未通過" if code == 403 else "資源不存在" if code == 404 else
                  "操作衝突，請稍後再試" if code == 409 else "輸入資料不合格" if code == 422 else
                  "服務忙碌或不可用" if code >= 500 else "請求未完成")
        return f"[{self.formatTime(record, '%H:%M:%S')}] [{'警告' if code >= 400 else '資訊'}] {area}｜{method}｜{result}（HTTP {code}）"


def dashboard_log_config(base: dict) -> dict:
    config = copy.deepcopy(base)
    config["formatters"]["access"] = {"()": ChineseAccessFormatter}
    config.setdefault("filters", {})["quiet_preview"] = {"()": QuietPreviewFilter}
    config["handlers"]["access"]["filters"] = ["quiet_preview"]
    return config
