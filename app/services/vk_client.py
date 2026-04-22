"""Абстракции клиента VK API."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class VkApiConfig:
    """Параметры выполнения запросов к VK API."""

    version: str = "5.199"
    timeout_seconds: float = 15.0


class VkClient:
    """Тонкая обертка над запросами к VK API."""

    def __init__(self, config: VkApiConfig | None = None) -> None:
        self.config = config or VkApiConfig()

    def call_method(self, method: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Вызывает метод VK API и возвращает сырой JSON-ответ."""
        request_params: dict[str, Any] = dict(params or {})
        request_params["access_token"] = token
        request_params["v"] = self.config.version
        query = urlencode(request_params)
        url = f"https://api.vk.com/method/{method}?{query}"
        request = Request(url, method="GET")

        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                raw_data = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"HTTP ошибка VK API: {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Сетевая ошибка VK API: {error.reason}") from error

        try:
            parsed: dict[str, Any] = json.loads(raw_data)
        except json.JSONDecodeError as error:
            raise RuntimeError("VK API вернул некорректный JSON") from error

        return parsed
