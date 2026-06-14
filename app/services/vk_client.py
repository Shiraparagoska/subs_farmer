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

    version: str = "5.131"
    timeout_seconds: float = 15.0


class VkClient:
    """Тонкая обертка над запросами к VK API."""

    def __init__(self, config: VkApiConfig | None = None) -> None:
        self.config = config or VkApiConfig()

    def call_method(
        self,
        method: str,
        token: str,
        params: dict[str, Any] | None = None,
        request_method: str = "GET",
    ) -> dict[str, Any]:
        """Вызывает метод VK API и возвращает сырой JSON-ответ."""
        request_params: dict[str, Any] = dict(params or {})
        request_params["access_token"] = token
        request_params["v"] = self.config.version
        query = urlencode(request_params)
        url = f"https://api.vk.com/method/{method}"
        normalized_request_method = request_method.upper()
        parsed = self._perform_request(url=url, query=query, request_method=normalized_request_method)
        error_data = parsed.get("error")
        if (
            normalized_request_method == "POST"
            and isinstance(error_data, dict)
            and error_data.get("error_code") == 3
        ):
            return self._perform_request(url=url, query=query, request_method="GET")
        return parsed

    def _perform_request(self, url: str, query: str, request_method: str) -> dict[str, Any]:
        if request_method == "POST":
            request = Request(
                url,
                data=query.encode("utf-8"),
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        else:
            request = Request(f"{url}?{query}", method="GET")
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
