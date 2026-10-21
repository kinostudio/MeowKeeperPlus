"""明確的協定能力登錄；不暗中替換供應商、不默默外送第二家。"""

from urllib.parse import urlsplit, urlunsplit

from .gemini import GeminiVision
from .openai_compatible import OpenAICompatibleVision
from .transport import ProviderError, request_json

PROVIDERS = {"openai_compatible":OpenAICompatibleVision, "gemini":GeminiVision}


def capabilities(provider_id):
    return {"image":provider_id in PROVIDERS, "video":False, "token_usage":provider_id in PROVIDERS,
            "input_mode":"C_PLUS_STORYBOARD", "health_check":"model_metadata_only"}


def health_check(provider_id, settings, credential, *, timeout_seconds=2):
    if credential is not None and (not isinstance(credential,str) or "\r" in credential or "\n" in credential):
        raise ValueError("服務Key格式不正確。")
    PROVIDERS[provider_id](settings, lambda:credential)  # 先驗證協定與目的地。
    headers = {}
    if provider_id == "gemini":
        parsed = urlsplit(settings.endpoint)
        api_prefix = parsed.path.split("/models/", 1)[0]
        endpoint = urlunsplit((parsed.scheme, parsed.netloc, api_prefix + "/models", "", ""))
        if credential:
            headers["x-goog-api-key"] = credential
    else:
        if not settings.endpoint.endswith("/chat/completions"):
            return {"status":"unsupported_check", "message_zh_tw":"此自訂介面無可確認的模型資訊路徑；未發送探測，不代表圖片分析一定失敗。"}
        endpoint = settings.endpoint.removesuffix("/chat/completions")+"/models"
        if credential:
            headers["Authorization"] = "Bearer "+credential
    try:
        reply = request_json(endpoint, headers, {}, timeout_seconds=timeout_seconds, method="GET")
        rows = reply.get("models" if provider_id == "gemini" else "data", [])
        if not isinstance(rows,list):
            raise ProviderError("bad_response",http_status=getattr(reply,"http_status",None))
        model_ids = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if provider_id == "gemini":
                name, methods = row.get("name"), row.get("supportedGenerationMethods")
                if (isinstance(name, str) and isinstance(methods, list) and "generateContent" in methods
                        and name.startswith("models/")):
                    model_id = name.removeprefix("models/")
                else:
                    continue
            else:
                model_id = row.get("id")
            if (isinstance(model_id, str) and 1 <= len(model_id) <= 120
                    and all(char.isascii() and (char.isalnum() or char in "_.:/-") for char in model_id)):
                model_ids.append(model_id)
            if len(model_ids) >= 250:
                break
        available = settings.model_id in model_ids
        return {"status":"metadata_available" if available else "model_not_confirmed", "http_status":getattr(reply,"http_status",None),
                "models":model_ids,
                "message_zh_tw":"已讀取服務商模型清單；清單不代表每個模型都支援圖片，也不是分析品質或影片通知成功的證明。"}
    except ProviderError as error:
        return {"status":error.category, "http_status":error.http_status,
                "message_zh_tw":"目前無法確認模型資訊；請檢查服務、Key或網路。本機找貓與影片通知不受影響。"}
