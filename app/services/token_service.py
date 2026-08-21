"""Сервис управления токенами аккаунтов."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs

from app.services.vk_client import VkClient


@dataclass(slots=True)
class TokenRecord:
    """Одна запись токена аккаунта."""

    token: str
    is_valid: bool = False
    last_error: str | None = None
    owner_name: str | None = None
    role: str = "liker"
    allowed_groups: list[str] | None = None
    likes_available: bool | None = None
    last_like_error: str | None = None


class TokenService:
    """Парсит и хранит записи токенов."""

    def __init__(self, vk_client: VkClient | None = None) -> None:
        self.vk_client = vk_client or VkClient()

    def parse_txt(self, path: Path) -> list[TokenRecord]:
        """Читает UTF-8 TXT файл: один токен на строку."""
        records: list[TokenRecord] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            token = self._extract_token(line)
            if token:
                records.append(TokenRecord(token=token))
        return records

    def validate_record(self, record: TokenRecord) -> TokenRecord:
        """Проверяет токен через VK API и обновляет запись.

        users.get часто отвечает даже для забаненных аккаунтов, поэтому
        дополнительно дергаем wall.get — тот же вызов, что падает в сценарии.
        """
        try:
            response = self.vk_client.call_method(
                method="users.get",
                token=record.token,
                params={"fields": "id,first_name,last_name,deactivated"},
            )
        except RuntimeError as error:
            record.is_valid = False
            record.last_error = str(error)
            record.owner_name = None
            return record

        if "error" in response:
            error_data = response["error"]
            record.is_valid = False
            record.last_error = self._format_vk_error(error_data, method="users.get")
            record.owner_name = None
            return record

        users = response.get("response", [])
        if not users:
            record.is_valid = False
            record.last_error = "Пустой ответ users.get"
            record.owner_name = None
            return record

        user = users[0]
        first_name = user.get("first_name", "")
        last_name = user.get("last_name", "")
        full_name = f"{first_name} {last_name}".strip()
        record.owner_name = full_name or None

        deactivated = user.get("deactivated")
        if isinstance(deactivated, str) and deactivated.strip():
            record.is_valid = False
            record.last_error = f"Аккаунт деактивирован VK ({deactivated})"
            return record

        probe_error = self._probe_account_not_blocked(record.token)
        if probe_error is not None:
            record.is_valid = False
            record.last_error = probe_error
            return record

        record.is_valid = True
        record.last_error = None
        return record

    def _probe_account_not_blocked(self, token: str) -> str | None:
        """Возвращает текст ошибки, если аккаунт заблокирован / нерабочий."""
        try:
            response = self.vk_client.call_method(
                method="wall.get",
                token=token,
                params={"count": 1},
            )
        except RuntimeError as error:
            return str(error)

        if "error" not in response:
            return None

        error_data = response["error"]
        message = str(error_data.get("error_msg", "")).lower()
        if "user is blocked" in message or "user was deleted" in message:
            return "Аккаунт заблокирован VK (user is blocked)"
        return self._format_vk_error(error_data, method="wall.get")

    @staticmethod
    def _extract_token(raw_line: str) -> str:
        """Извлекает чистый access_token из строки файла."""
        token = raw_line.strip().lstrip("\ufeff").strip()
        if not token:
            return ""

        if (token.startswith('"') and token.endswith('"')) or (token.startswith("'") and token.endswith("'")):
            token = token[1:-1].strip()

        # OAuth redirect: ...#access_token=vk1.a....&expires_in=...
        hash_marker = "#access_token="
        if hash_marker in token:
            token = token.split(hash_marker, maxsplit=1)[1]

        if "access_token=" in token:
            parsed = parse_qs(token, keep_blank_values=True)
            access_token_values = parsed.get("access_token")
            if access_token_values:
                return access_token_values[0].strip()
            # На случай кривой строки без нормального query
            after = token.split("access_token=", maxsplit=1)[1]
            return after.split("&", maxsplit=1)[0].strip()

        if "&" in token:
            return token.split("&", maxsplit=1)[0].strip()

        # Иногда в дампах: id:vk1.a.... или login:pass:vk1.a....
        if "vk1." in token and ":" in token and not token.startswith("vk1."):
            vk_index = token.find("vk1.")
            if vk_index >= 0:
                return token[vk_index:].split("&", maxsplit=1)[0].strip()

        return token

    @staticmethod
    def _format_vk_error(error_data: dict, method: str | None = None) -> str:
        code = error_data.get("error_code", "n/a")
        message = error_data.get("error_msg", "Неизвестная ошибка VK API")
        redirect_uri = error_data.get("redirect_uri")
        method_prefix = f"{method}: " if method else ""
        if isinstance(redirect_uri, str) and redirect_uri.strip():
            return f"{method_prefix}VK API {code}: {message}\nОткройте в браузере: {redirect_uri}"
        return f"{method_prefix}VK API {code}: {message}"
