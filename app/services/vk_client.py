"""Абстракции клиента VK API."""

from __future__ import annotations

import json
import mimetypes
import ssl
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import dataclass
from typing import Any

import certifi


@dataclass(slots=True)
class VkApiConfig:
    """Параметры выполнения запросов к VK API."""

    version: str = "5.131"
    timeout_seconds: float = 15.0


class VkClient:
    """Тонкая обертка над запросами к VK API."""

    def __init__(self, config: VkApiConfig | None = None) -> None:
        self.config = config or VkApiConfig()
        # Windows/Python без локального CA: urllib сам certifi не подхватывает.
        self._ssl_context = ssl.create_default_context(cafile=certifi.where())

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

    def upload_file(self, upload_url: str, field_name: str, file_path: Path) -> dict[str, Any]:
        """Загружает файл на upload-сервер VK (multipart/form-data)."""
        boundary = f"----VkHelperBoundary{uuid.uuid4().hex}"
        # Кириллица/пробелы в filename часто дают пустой photo от upload-сервера VK.
        suffix = file_path.suffix.lower() or ".jpg"
        if suffix not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
            suffix = ".jpg"
        safe_name = f"upload{suffix}"
        content_type = mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
        file_bytes = file_path.read_bytes()
        if not file_bytes:
            raise RuntimeError("Файл фото пустой")

        body = bytearray()
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{safe_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("ascii")
        )
        body.extend(file_bytes)
        body.extend(f"\r\n--{boundary}--\r\n".encode("ascii"))

        request = Request(
            upload_url,
            data=bytes(body),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urlopen(
                request,
                timeout=max(30.0, self.config.timeout_seconds),
                context=self._ssl_context,
            ) as response:
                raw_data = response.read().decode("utf-8")
        except HTTPError as error:
            raise RuntimeError(f"HTTP ошибка загрузки файла: {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Сетевая ошибка загрузки файла: {error.reason}") from error
        try:
            parsed: dict[str, Any] = json.loads(raw_data)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"Upload-сервер вернул некорректный JSON: {raw_data[:200]}") from error
        return parsed

    def call_method_raw_params(
        self,
        method: str,
        token: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """POST с аккуратной кодировкой (нужно для photo JSON в saveWallPhoto)."""
        request_params: dict[str, Any] = dict(params)
        request_params["access_token"] = token
        request_params["v"] = self.config.version
        # doseq не нужен; safe='' чтобы не портить JSON photo.
        query = urlencode(request_params, doseq=True, safe="")
        url = f"https://api.vk.com/method/{method}"
        return self._perform_request(url=url, query=query, request_method="POST")

    def download_bytes(self, url: str) -> bytes:
        """Скачивает бинарные данные по HTTPS."""
        request = Request(url, method="GET")
        try:
            with urlopen(
                request,
                timeout=max(30.0, self.config.timeout_seconds),
                context=self._ssl_context,
            ) as response:
                return response.read()
        except HTTPError as error:
            raise RuntimeError(f"HTTP ошибка скачивания: {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"Сетевая ошибка скачивания: {error.reason}") from error

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
            with urlopen(
                request,
                timeout=self.config.timeout_seconds,
                context=self._ssl_context,
            ) as response:
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
