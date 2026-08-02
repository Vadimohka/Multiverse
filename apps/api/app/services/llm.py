import json
from abc import ABC, abstractmethod
from typing import Any

import httpx
from app.config import get_settings


class AIProvider(ABC):
    @abstractmethod
    async def complete(self, model: str, messages: list[dict[str, str]], settings: dict[str, Any]) -> dict[str, Any]: ...


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    async def complete(self, model: str, messages: list[dict[str, str]], settings: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": settings.get("temperature", 0), "max_tokens": settings.get("max_tokens", 2000)}
        if settings.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=settings.get("timeout", 60)) as client:
            response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        return {"response": content, "parsed_response": parsed, "usage": data.get("usage", {}), "model": data.get("model", model)}


class MockProvider(AIProvider):
    async def complete(self, model: str, messages: list[dict[str, str]], settings: dict[str, Any]) -> dict[str, Any]:
        return {"response": '{"mock": true}', "parsed_response": {"mock": True}, "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model": "mock"}


def get_provider(name: str) -> AIProvider:
    settings = get_settings()
    if name == "mock":
        return MockProvider()
    if name in {"deepseek", "openai_compatible"}:
        return OpenAICompatibleProvider(settings.deepseek_base_url, settings.deepseek_api_key)
    raise ValueError(f"Unknown AI provider: {name}")
