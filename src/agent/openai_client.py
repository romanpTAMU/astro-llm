from __future__ import annotations

from typing import Any, Dict

from openai import OpenAI


def get_client() -> OpenAI:
    return OpenAI()


def chat_json(
    client: OpenAI,
    model: str,
    system: str,
    user: str,
    use_web_search: bool = False,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,  # Use lower temperature for more deterministic outputs
    }
    
    # Enable web search if requested (for models that support it)
    if use_web_search:
        kwargs["web_search_options"] = {
            "search_mode": "auto",  # Let the model decide when to search
        }
    
    try:
        # Add timeout to prevent hanging (httpx timeout in seconds)
        resp = client.chat.completions.create(**kwargs, timeout=timeout)
    except Exception as e:
        # If web_search_options not supported, retry without it
        if use_web_search and "web_search_options" in str(e).lower():
            kwargs.pop("web_search_options", None)
            resp = client.chat.completions.create(**kwargs, timeout=timeout)
        else:
            raise
    
    content = resp.choices[0].message.content or "{}"
    return _safe_json_parse(content)


def _safe_json_parse(text: str) -> Dict[str, Any]:
    try:
        import orjson
        return orjson.loads(text)
    except Exception:
        import json
        try:
            return json.loads(text)
        except Exception:
            return {"error": "invalid_json", "raw": text}
