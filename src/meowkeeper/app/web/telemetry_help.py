"""全站共用白話詞典；純本機資料，不含實機數值。"""

import json
from pathlib import Path


def load_help_terms() -> dict[str, dict[str, str]]:
    document = json.loads(Path(__file__).with_name("telemetry_help_terms.json").read_text(encoding="utf-8"))
    if document.get("schema_version") != 1 or document.get("locale") != "zh-TW":
        raise ValueError("介面說明詞典版本不相容。")
    terms = document.get("terms")
    fields = {"label", "technical_alias", "what", "why_or_next_step", "important_note"}
    if not isinstance(terms, dict) or not terms:
        raise ValueError("介面說明詞典不存在。")
    for key, entry in terms.items():
        if not isinstance(key, str) or not isinstance(entry, dict) or set(entry) != fields:
            raise ValueError("介面說明詞典格式不正確。")
        if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
            raise ValueError("介面說明詞典缺少文字。")
    return terms
