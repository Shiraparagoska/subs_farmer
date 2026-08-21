"""Контракты сервиса операций с комментариями и лайками."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.services.vk_client import VkClient


@dataclass(slots=True)
class ActionResult:
    """Результат действия: комментарий или лайк."""

    success: bool
    message: str
    data: dict[str, Any] | None = None


class CommentService:
    """Координирует ручные действия комментариев и лайков."""

    _YOUTUBE_ID_RE = re.compile(
        r"(?:youtube\.com/(?:watch\?.*?v=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{6,})",
        re.IGNORECASE,
    )

    def __init__(self, vk_client: VkClient | None = None) -> None:
        self.vk_client = vk_client or VkClient()

    def post_comment(
        self,
        account_token: str,
        owner_id: int,
        post_id: int,
        text: str,
        photo_path: str | Path | None = None,
    ) -> ActionResult:
        """Оставляет комментарий под постом сообщества/пользователя."""
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "post_id": post_id,
            "message": text,
        }
        if photo_path:
            attachment_result = self.upload_wall_photo(account_token, Path(photo_path))
            if not attachment_result.success:
                return attachment_result
            attachment = (attachment_result.data or {}).get("attachment")
            if not isinstance(attachment, str) or not attachment:
                return ActionResult(success=False, message="Не удалось получить attachment фото")
            params["attachments"] = attachment

        try:
            response = self.vk_client.call_method(
                method="wall.createComment",
                token=account_token,
                params=params,
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="wall.createComment"),
                data=self._extract_error_data(error_data),
            )

        comment_id = response.get("response", {}).get("comment_id")
        if comment_id is None:
            return ActionResult(success=False, message="VK API не вернул comment_id")

        suffix = " +фото" if photo_path else ""
        return ActionResult(
            success=True,
            message=f"Комментарий отправлен{suffix}, id={comment_id}",
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
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="likes.add"),
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

    def add_like_to_comment(
        self,
        account_token: str,
        owner_id: int,
        comment_id: int,
        post_id: int | None = None,
    ) -> ActionResult:
        """Ставит лайк на комментарий."""
        primary = self._likes_add_comment(account_token, owner_id, comment_id)
        if primary.success:
            return primary

        if post_id is None:
            return primary
        fallback_owner_id = self._resolve_comment_author_id(
            account_token=account_token,
            wall_owner_id=owner_id,
            post_id=post_id,
            comment_id=comment_id,
        )
        if fallback_owner_id is None or fallback_owner_id == owner_id:
            return primary

        retry = self._likes_add_comment(account_token, fallback_owner_id, comment_id)
        if retry.success:
            return ActionResult(
                success=True,
                message=f"{retry.message} (owner_id fallback: {owner_id} -> {fallback_owner_id})",
                data=retry.data,
            )
        return ActionResult(
            success=False,
            message=f"{primary.message} | fallback owner_id={fallback_owner_id}: {retry.message}",
            data=retry.data or primary.data,
        )

    def _likes_add_comment(self, account_token: str, owner_id: int, comment_id: int) -> ActionResult:
        try:
            response = self.vk_client.call_method(
                method="likes.add",
                token=account_token,
                params={
                    "type": "comment",
                    "owner_id": owner_id,
                    "item_id": comment_id,
                },
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            base_result = ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="likes.add"),
                data=self._extract_error_data(error_data),
            )
            if base_result.data and base_result.data.get("error_code") == 3:
                execute_result = self._likes_add_comment_via_execute(account_token, owner_id, comment_id)
                if execute_result.success:
                    return ActionResult(
                        success=True,
                        message=f"{execute_result.message} (fallback execute)",
                        data=execute_result.data,
                    )
                return ActionResult(
                    success=False,
                    message=f"{base_result.message} | execute fallback: {execute_result.message}",
                    data=execute_result.data or base_result.data,
                )
            return base_result
        likes_count = response.get("response", {}).get("likes")
        if likes_count is None:
            return ActionResult(success=False, message="VK API не вернул число лайков комментария")
        return ActionResult(
            success=True,
            message=f"Лайк комментария поставлен, всего лайков: {likes_count}",
            data={"likes": likes_count},
        )

    def _likes_add_comment_via_execute(self, account_token: str, owner_id: int, comment_id: int) -> ActionResult:
        code = (
            "var r = API.likes.add({"
            f"\"type\":\"comment\",\"owner_id\":{owner_id},\"item_id\":{comment_id}"
            "});"
            "return r;"
        )
        try:
            response = self.vk_client.call_method(
                method="execute",
                token=account_token,
                params={"code": code},
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))
        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="execute"),
                data=self._extract_error_data(error_data),
            )
        likes_count = response.get("response", {}).get("likes")
        if likes_count is None:
            return ActionResult(success=False, message="VK API execute не вернул число лайков")
        return ActionResult(
            success=True,
            message=f"Лайк комментария поставлен, всего лайков: {likes_count}",
            data={"likes": likes_count},
        )

    def _resolve_comment_author_id(
        self,
        account_token: str,
        wall_owner_id: int,
        post_id: int,
        comment_id: int,
    ) -> int | None:
        try:
            response = self.vk_client.call_method(
                method="wall.getComments",
                token=account_token,
                params={
                    "owner_id": wall_owner_id,
                    "post_id": post_id,
                    "start_comment_id": comment_id,
                    "count": 10,
                    "sort": "asc",
                },
            )
        except RuntimeError:
            return None

        error_data = response.get("error")
        if error_data:
            return None
        items = response.get("response", {}).get("items", [])
        for item in items:
            if not isinstance(item, dict) or item.get("id") != comment_id:
                continue
            from_id = item.get("from_id")
            if isinstance(from_id, int):
                return from_id
        return None

    def reply_to_comment(
        self,
        account_token: str,
        owner_id: int,
        post_id: int,
        reply_to_comment: int,
        text: str,
        photo_path: str | Path | None = None,
    ) -> ActionResult:
        """Отправляет ответ на комментарий под постом."""
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "post_id": post_id,
            "reply_to_comment": reply_to_comment,
            "message": text,
        }
        if photo_path:
            attachment_result = self.upload_wall_photo(account_token, Path(photo_path))
            if not attachment_result.success:
                return attachment_result
            attachment = (attachment_result.data or {}).get("attachment")
            if not isinstance(attachment, str) or not attachment:
                return ActionResult(success=False, message="Не удалось получить attachment фото")
            params["attachments"] = attachment

        try:
            response = self.vk_client.call_method(
                method="wall.createComment",
                token=account_token,
                params=params,
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="wall.createComment"),
                data=self._extract_error_data(error_data),
            )

        comment_id = response.get("response", {}).get("comment_id")
        if comment_id is None:
            return ActionResult(success=False, message="VK API не вернул id ответа")
        suffix = " +фото" if photo_path else ""
        return ActionResult(
            success=True,
            message=f"Ответ отправлен{suffix}, id={comment_id}",
            data={"comment_id": comment_id},
        )


    def delete_comment(self, account_token: str, owner_id: int, comment_id: int) -> ActionResult:
        """Удаляет комментарий/ответ со стены, если у аккаунта есть права."""
        try:
            response = self.vk_client.call_method(
                method="wall.deleteComment",
                token=account_token,
                params={
                    "owner_id": owner_id,
                    "comment_id": comment_id,
                },
                request_method="POST",
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="wall.deleteComment"),
                data=self._extract_error_data(error_data),
            )

        deleted = response.get("response") == 1
        if not deleted:
            return ActionResult(success=False, message=f"VK API не подтвердил удаление comment_id={comment_id}")
        return ActionResult(success=True, message=f"Старый комментарий удален, id={comment_id}")

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
            return False, None, self._format_vk_error(error_data, method="utils.resolveScreenName")

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
                message=self._format_vk_error(error_data, method="wall.get"),
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

    def list_wall_posts(
        self,
        account_token: str,
        owner_id: int,
        limit: int = 10,
    ) -> ActionResult:
        """Возвращает последние посты стены (id и признак закрепа)."""
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
                message=self._format_vk_error(error_data, method="wall.get"),
                data=self._extract_error_data(error_data),
            )

        payload = response.get("response", {})
        items = payload.get("items", [])
        if not isinstance(items, list):
            return ActionResult(success=False, message="VK API не вернул список постов")

        posts: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            post_id = item.get("id")
            if not isinstance(post_id, int):
                continue
            posts.append(
                {
                    "id": post_id,
                    "is_pinned": item.get("is_pinned") == 1,
                }
            )
        return ActionResult(success=True, message=f"Получено постов: {len(posts)}", data={"posts": posts})

    def comment_exists(self, account_token: str, owner_id: int, post_id: int, comment_id: int) -> ActionResult:
        """Проверяет, существует ли комментарий к посту."""
        return self._comment_exists(
            account_token=account_token,
            owner_id=owner_id,
            target_comment_id=comment_id,
            params={
                "owner_id": owner_id,
                "post_id": post_id,
                "start_comment_id": comment_id,
                "count": 10,
                "sort": "asc",
            },
            label=f"Комментарий id={comment_id}",
        )

    def reply_exists(self, account_token: str, owner_id: int, parent_comment_id: int, reply_id: int) -> ActionResult:
        """Проверяет, существует ли ответ на комментарий."""
        return self._comment_exists(
            account_token=account_token,
            owner_id=owner_id,
            target_comment_id=reply_id,
            params={
                "owner_id": owner_id,
                "comment_id": parent_comment_id,
                "start_comment_id": reply_id,
                "count": 10,
                "sort": "asc",
            },
            label=f"Ответ id={reply_id}",
        )

    def upload_wall_photo(self, account_token: str, photo_path: Path) -> ActionResult:
        """Загружает фото на стену пользователя и возвращает attachment вида photo{owner}_{id}."""
        if not photo_path.exists() or not photo_path.is_file():
            return ActionResult(success=False, message=f"Файл фото не найден: {photo_path}")

        try:
            server_response = self.vk_client.call_method(
                method="photos.getWallUploadServer",
                token=account_token,
                params={},
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = server_response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="photos.getWallUploadServer"),
                data=self._extract_error_data(error_data),
            )

        upload_url = (server_response.get("response") or {}).get("upload_url")
        if not isinstance(upload_url, str) or not upload_url.strip():
            return ActionResult(success=False, message="VK не вернул upload_url для фото")

        try:
            upload_payload = self.vk_client.upload_file(upload_url, "photo", photo_path)
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        if isinstance(upload_payload.get("error"), str):
            return ActionResult(success=False, message=f"Upload-сервер: {upload_payload['error']}")

        server = upload_payload.get("server")
        photo = upload_payload.get("photo")
        photo_hash = upload_payload.get("hash")

        # Иногда VK отдаёт объект/массив — saveWallPhoto ждёт строку JSON.
        if isinstance(photo, (dict, list)):
            photo = json.dumps(photo, ensure_ascii=False, separators=(",", ":"))
        if isinstance(photo, str):
            photo = photo.strip()
        if server is None or photo_hash is None or not photo or photo in {"[]", "null", "None"}:
            return ActionResult(
                success=False,
                message=(
                    "Upload-сервер вернул пустое photo "
                    f"(server={server!r}, photo={photo!r}, hash={photo_hash!r}). "
                    "Попробуй jpg вместо png или другое имя файла."
                ),
            )

        try:
            save_response = self.vk_client.call_method_raw_params(
                method="photos.saveWallPhoto",
                token=account_token,
                params={
                    "server": server,
                    "photo": photo,
                    "hash": photo_hash,
                },
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        save_error = save_response.get("error")
        if save_error:
            return ActionResult(
                success=False,
                message=self._format_vk_error(save_error, method="photos.saveWallPhoto"),
                data=self._extract_error_data(save_error),
            )

        saved_items = save_response.get("response")
        if not isinstance(saved_items, list) or not saved_items:
            return ActionResult(success=False, message="photos.saveWallPhoto вернул пустой ответ")

        saved = saved_items[0]
        owner_id = saved.get("owner_id")
        photo_id = saved.get("id")
        if not isinstance(owner_id, int) or not isinstance(photo_id, int):
            return ActionResult(success=False, message="photos.saveWallPhoto не вернул id фото")

        attachment = f"photo{owner_id}_{photo_id}"
        return ActionResult(
            success=True,
            message=f"Фото загружено: {attachment}",
            data={"attachment": attachment, "owner_id": owner_id, "photo_id": photo_id},
        )

    def download_youtube_thumbnail(self, text_or_url: str, target_path: Path | None = None) -> ActionResult:
        """Скачивает превью YouTube по ссылке из текста. Возвращает путь к jpg."""
        video_id = self.extract_youtube_video_id(text_or_url)
        if not video_id:
            return ActionResult(success=False, message="В тексте нет ссылки YouTube")

        candidates = (
            f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
        )
        last_error = "Не удалось скачать превью YouTube"
        image_bytes: bytes | None = None
        for url in candidates:
            try:
                raw = self.vk_client.download_bytes(url)
            except RuntimeError as error:
                last_error = str(error)
                continue
            # Заглушка YouTube ~1KB; нормальное превью заметно больше.
            if len(raw) < 5000:
                last_error = "Превью YouTube недоступно (заглушка)"
                continue
            image_bytes = raw
            break

        if image_bytes is None:
            return ActionResult(success=False, message=last_error)

        output = target_path or Path(tempfile.gettempdir()) / f"vk_yt_{video_id}.jpg"
        try:
            output.write_bytes(image_bytes)
        except OSError as error:
            return ActionResult(success=False, message=f"Не удалось сохранить превью: {error}")

        return ActionResult(
            success=True,
            message=f"Превью YouTube сохранено: {output.name}",
            data={"path": str(output), "video_id": video_id},
        )

    @classmethod
    def extract_youtube_video_id(cls, text_or_url: str) -> str | None:
        value = (text_or_url or "").strip()
        if not value:
            return None
        match = cls._YOUTUBE_ID_RE.search(value)
        if match:
            return match.group(1)
        parsed = urlparse(value)
        if "youtube.com" in parsed.netloc.lower():
            query_id = parse_qs(parsed.query).get("v", [None])[0]
            if isinstance(query_id, str) and query_id.strip():
                return query_id.strip()
        return None

    def _comment_exists(
        self,
        account_token: str,
        owner_id: int,
        target_comment_id: int,
        params: dict[str, Any],
        label: str,
    ) -> ActionResult:
        try:
            response = self.vk_client.call_method(
                method="wall.getComments",
                token=account_token,
                params=params,
            )
        except RuntimeError as error:
            return ActionResult(success=False, message=str(error))

        error_data = response.get("error")
        if error_data:
            return ActionResult(
                success=False,
                message=self._format_vk_error(error_data, method="wall.getComments"),
                data=self._extract_error_data(error_data),
            )

        items = response.get("response", {}).get("items", [])
        for item in items:
            if not isinstance(item, dict) or item.get("id") != target_comment_id:
                continue
            if self._is_deleted_comment(item):
                return ActionResult(success=True, message=f"{label} удален", data={"exists": False})
            return ActionResult(success=True, message=f"{label} найден", data={"exists": True})
        return ActionResult(success=True, message=f"{label} не найден", data={"exists": False})

    @staticmethod
    def _is_deleted_comment(item: dict[str, Any]) -> bool:
        deleted_value = item.get("deleted")
        if deleted_value in (1, True, "1", "true", "True"):
            return True
        if item.get("text") == "" and item.get("from_id") is None:
            return True
        return False

    @staticmethod
    def _format_vk_error(error_data: dict, method: str | None = None) -> str:
        code = error_data.get("error_code", "n/a")
        message = error_data.get("error_msg", "Неизвестная ошибка VK API")
        redirect_uri = error_data.get("redirect_uri")
        method_prefix = f"{method}: " if method else ""
        if isinstance(redirect_uri, str) and redirect_uri.strip():
            return f"{method_prefix}VK API {code}: {message}\nОткройте в браузере: {redirect_uri}"
        return f"{method_prefix}VK API {code}: {message}"

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
