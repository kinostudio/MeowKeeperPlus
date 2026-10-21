"""Extract only provider-reported token counts; never retain raw response metadata."""


def _count(value):
    return value if type(value) is int and 0 <= value <= 100_000_000 else None


def openai_token_usage(response):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return _count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens"))


def gemini_token_usage(response):
    usage = response.get("usageMetadata") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return None, None
    return _count(usage.get("promptTokenCount")), _count(usage.get("candidatesTokenCount"))
