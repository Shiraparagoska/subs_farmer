"""Контракты сервиса операций с комментариями и лайками."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.vk_client import VkClient


@dataclass(slots=True)
class ActionResult:
    """Результат действия: комментарий или лайк."""

    success: bool
    message: str
    data: dict[str, Any] | None = None


class CommentService:
    """Координирует ручные действия комментариев и лайков."""

    def __init__(self, vk_client: VkClient | None = None) -> None:
        self.vk_client = vk_client or VkClient()

    def post_comment(self, account_token: str, owner_id: int, post_id: int, text: str) -> ActionResult:
        """Оставляет комментарий под постом сообщества/пользователя."""
        try:
            response = self.vk_client.call_method(
                method="wall.createComment",
                token=account_token,
                params={
                    "owner_id": owner_id,
                    "post_id": post_id,
                    "message": text,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data),
                data=self._extract_error_data(error_data),
            )

        comment_id = response.get("response", {}).get("comment_id")
        if comment_id is None:
            return ActionResult(success=False, message="VK API не вернул comment_id")

        return ActionResult(
            success=True,
            message=f"Комментарий отправлен, id={comment_id}",
            data={"comment_id": comment_id},
        )

    def add_like(self, account_token: str, owner_id: int, post_id: int) -> ActionResult:
        """Ставит лайк на пост."""
        try:
            response = self.vk_client.call_method(
                method="likes.add",
                token=account_token,
                params={
                    "type": "post",
                    "owner_id": owner_id,
                    "item_id": post_id,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data),
                data=self._extract_error_data(error_data),
            )

        likes_count = response.get("response", {}).get("likes")
        if likes_count is None:
            return ActionResult(success=False, message="VK API не вернул число лайков")

        return ActionResult(
            success=True,
            message=f"Лайк поставлен, всего лайков: {likes_count}",
            data={"likes": likes_count},
        )

    def add_like_to_comment(self, account_token: str, owner_id: int, comment_id: int) -> ActionResult:
        """Ставит лайк на комментарий."""
        try:
            response = self.vk_client.call_method(
                method="likes.add",
                token=account_token,
                params={
                    "type": "comment",
                    "owner_id": owner_id,
                    "item_id": comment_id,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data),
                data=self._extract_error_data(error_data),
            )

        likes_count = response.get("response", {}).get("likes")
        if likes_count is None:
            return ActionResult(success=False, message="VK API не вернул число лайков комментария")

        return ActionResult(
            success=True,
            message=f"Лайк комментария поставлен, всего лайков: {likes_count}",
            data={"likes": likes_count},
        )

    def reply_to_comment(
        self,
        account_token: str,
        owner_id: int,
        post_id: int,
        reply_to_comment: int,
        text: str,
    ) -> ActionResult:
        """Отправляет ответ на комментарий под постом."""
        try:
            response = self.vk_client.call_method(
                method="wall.createComment",
                token=account_token,
                params={
                    "owner_id": owner_id,
                    "post_id": post_id,
                    "reply_to_comment": reply_to_comment,
                    "message": text,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data),
                data=self._extract_error_data(error_data),
            )

        comment_id = response.get("response", {}).get("comment_id")
        if comment_id is None:
            return ActionResult(success=False, message="VK API не вернул id ответа")
        return ActionResult(
            success=True,
            message=f"Ответ отправлен, id={comment_id}",
            data={"comment_id": comment_id},
        )

    def resolve_owner_id(self, account_token: str, group_ref: str) -> tuple[bool, int | None, str]:
        """Преобразует ссылку/ID группы в owner_id для wall/likes."""
        value = group_ref.strip()
        if not value:
            return False, None, "Не указана группа"

        normalized = value
        for prefix in ("https://vk.com/", "http://vk.com/", "vk.com/"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        normalized = normalized.strip().split("?")[0].split("#")[0]

        if normalized.startswith("club") and normalized[4:].isdigit():
            return True, -int(normalized[4:]), "OK"
        if normalized.startswith("public") and normalized[6:].isdigit():
            return True, -int(normalized[6:]), "OK"
        if normalized.lstrip("-").isdigit():
            numeric = int(normalized)
            return True, numeric if numeric < 0 else -numeric, "OK"

        try:
            response = self.vk_client.call_method(
                method="utils.resolveScreenName",
                token=account_token,
                params={"screen_name": normalized},
            )
        except RuntimeError as error:
            return False, None, str(error)

        error_data = response.get("error")
        if error_data:
            return False, None, self._format_vk_error(error_data)

        resolved = response.get("response")
        if not resolved:
            return False, None, "Группа не найдена по ссылке"

        item_type = resolved.get("type")
        object_id = resolved.get("object_id")
        if item_type != "group" or not isinstance(object_id, int):
            return False, None, "Ссылка не на группу VK"

        return True, -object_id, "OK"

    def find_target_post(
        self,
        account_token: str,
        owner_id: int,
        prefer_pinned: bool = True,
        limit: int = 10,
    ) -> ActionResult:
        """Ищет пост для сценария: закрепленный, иначе последний."""
        request_limit = max(1, min(limit, 50))
        try:
            response = self.vk_client.call_method(
                method="wall.get",
                token=account_token,
                params={
                    "owner_id": owner_id,
                    "count": request_limit,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data),
                data=self._extract_error_data(error_data),
            )

        payload = response.get("response", {})
        items = payload.get("items", [])
        if not isinstance(items, list) or not items:
            return ActionResult(success=False, message="В группе нет доступных постов")

        chosen_post: dict[str, Any] | None = None
        source = "latest"
        if prefer_pinned:
            for item in items:
                if isinstance(item, dict) and item.get("is_pinned") == 1:
                    chosen_post = item
                    source = "pinned"
                    break

        if chosen_post is None:
            for item in items:
                if isinstance(item, dict):
                    chosen_post = item
                    break

        if not chosen_post:
            return ActionResult(success=False, message="VK API не вернул корректные данные постов")

        post_id = chosen_post.get("id")
        if not isinstance(post_id, int):
            return ActionResult(success=False, message="VK API не вернул id поста")

        if source == "pinned":
            message = f"Найден закрепленный пост, id={post_id}"
        else:
            message = f"Закрепленный пост не найден, взят последний пост, id={post_id}"
        return ActionResult(success=True, message=message, data={"post_id": post_id, "source": source})

    @staticmethod
    def _format_vk_error(error_data: dict) -> str:
        code = error_data.get("error_code", "n/a")
        message = error_data.get("error_msg", "Неизвестная ошибка VK API")
        redirect_uri = error_data.get("redirect_uri")
        if isinstance(redirect_uri, str) and redirect_uri.strip():
            return f"VK API {code}: {message}\nОткройте в браузере: {redirect_uri}"
        return f"VK API {code}: {message}"

    @staticmethod
    def _extract_error_data(error_data: dict) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        code = error_data.get("error_code")
        message = error_data.get("error_msg")
        redirect_uri = error_data.get("redirect_uri")
        if isinstance(code, int):
            payload["error_code"] = code
        if isinstance(message, str):
            payload["error_msg"] = message
        if isinstance(redirect_uri, str) and redirect_uri.strip():
            payload["redirect_uri"] = redirect_uri.strip()
        return payload
