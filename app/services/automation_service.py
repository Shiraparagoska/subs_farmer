"""Сервис автоматического сценария комментария, ответа и лайка."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.services.comment_service import ActionResult, CommentService


@dataclass(slots=True)
class ScenarioRunResult:
    """Итог выполнения автоматического сценария."""

    success: bool
    message: str
    logs: list[str]
    requires_confirmation: bool = False
    redirect_uri: str | None = None
    comment_id: int | None = None
    reply_id: int | None = None
    post_id: int | None = None
    owner_id: int | None = None


@dataclass(slots=True)
class ScenarioContext:
    """Контекст автосценария для паузы и продолжения."""

    commentator_token: str
    liker_token: str
    replier_token: str
    group_ref: str
    post_id: int
    comment_text: str
    reply_text: str
    reply_required: bool
    delay_seconds: float
    owner_id: int
    comment_id: int | None = None
    reply_id: int | None = None
    next_step: str = "comment"
    photo_path: str | None = None


class AutomationService:
    """Выполняет последовательность действий комментарий->ответ->лайк."""

    def __init__(self, comment_service: CommentService | None = None) -> None:
        self.comment_service = comment_service or CommentService()
        self._pending_context: ScenarioContext | None = None

    def run_comment_like_reply_scenario(
        self,
        commentator_token: str,
        liker_token: str,
        replier_token: str,
        group_ref: str,
        post_id: int | None,
        comment_text: str,
        reply_text: str,
        prefer_pinned: bool = True,
        delay_seconds: float = 3,
        reply_required: bool = True,
        photo_path: str | None = None,
    ) -> ScenarioRunResult:
        normalized_delay = max(0.0, float(delay_seconds))

        owner_result = self._resolve_owner_id(commentator_token, group_ref)
        if not owner_result.success or owner_result.data is None:
            return ScenarioRunResult(success=False, message="Ошибка owner_id", logs=[owner_result.message])
        owner_id = int(owner_result.data["owner_id"])

        resolved_post_id = post_id
        if resolved_post_id is None:
            post_result = self.comment_service.find_target_post(
                account_token=commentator_token,
                owner_id=owner_id,
                prefer_pinned=prefer_pinned,
            )
            if not post_result.success:
                return ScenarioRunResult(
                    success=False,
                    message="Ошибка поиска поста",
                    logs=[owner_result.message, post_result.message],
                )
            if not post_result.data or not isinstance(post_result.data.get("post_id"), int):
                return ScenarioRunResult(
                    success=False,
                    message="Ошибка поиска поста",
                    logs=[owner_result.message, "Не удалось получить post_id"],
                )
            resolved_post_id = int(post_result.data["post_id"])
            initial_logs = [owner_result.message, post_result.message]
        else:
            initial_logs = [owner_result.message, f"Используется указанный post_id={resolved_post_id}"]

        if photo_path:
            initial_logs.append(f"К комментарию будет прикреплено фото: {photo_path}")

        context = ScenarioContext(
            commentator_token=commentator_token,
            liker_token=liker_token,
            replier_token=replier_token,
            group_ref=group_ref,
            post_id=resolved_post_id,
            comment_text=comment_text,
            reply_text=reply_text,
            reply_required=reply_required,
            delay_seconds=normalized_delay,
            owner_id=owner_id,
            photo_path=photo_path,
        )
        return self._execute_from_context(context, initial_logs=initial_logs)

    def continue_pending_scenario(self) -> ScenarioRunResult:
        context = self._pending_context
        if context is None:
            return ScenarioRunResult(success=False, message="Нет сценария для продолжения", logs=["Нет сохраненного контекста"])
        return self._execute_from_context(context, initial_logs=["Продолжаю сценарий с сохраненного шага"])

    def has_pending_scenario(self) -> bool:
        return self._pending_context is not None

    @staticmethod
    def _is_validation_required(result: ActionResult) -> bool:
        if result.success or not result.data:
            return False
        return result.data.get("error_code") == 17

    @staticmethod
    def _extract_redirect_uri(result: ActionResult) -> str | None:
        if not result.data:
            return None
        value = result.data.get("redirect_uri")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _execute_from_context(self, context: ScenarioContext, initial_logs: list[str]) -> ScenarioRunResult:
        logs = list(initial_logs)

        if context.next_step == "comment":
            comment_result = self.comment_service.post_comment(
                account_token=context.commentator_token,
                owner_id=context.owner_id,
                post_id=context.post_id,
                text=context.comment_text,
                photo_path=context.photo_path,
            )
            logs.append(comment_result.message)
            if not comment_result.success:
                if self._is_validation_required(comment_result):
                    self._pending_context = context
                    return ScenarioRunResult(
                        success=False,
                        message="Требуется подтверждение комментатора",
                        logs=logs,
                        requires_confirmation=True,
                        redirect_uri=self._extract_redirect_uri(comment_result),
                        comment_id=context.comment_id,
                        reply_id=context.reply_id,
                        post_id=context.post_id,
                        owner_id=context.owner_id,
                    )
                self._pending_context = None
                return ScenarioRunResult(
                    success=False,
                    message="Сценарий остановлен на комментарии",
                    logs=logs,
                    comment_id=context.comment_id,
                    reply_id=context.reply_id,
                    post_id=context.post_id,
                    owner_id=context.owner_id,
                )

            context.comment_id = self._extract_comment_id(comment_result)
            if context.comment_id is None:
                logs.append("Не удалось получить ID комментария из результата")
                self._pending_context = None
                return ScenarioRunResult(success=False, message="Нет comment_id для следующих шагов", logs=logs)
            context.next_step = "reply" if context.reply_required else "like"
            if not context.reply_required:
                logs.append("Ответ не требуется — шаг ответа пропущен")
            if context.delay_seconds > 0:
                time.sleep(context.delay_seconds)

        if context.next_step == "reply":
            if context.comment_id is None:
                self._pending_context = None
                return ScenarioRunResult(success=False, message="Нет comment_id для ответа", logs=logs)
            reply_result = self.comment_service.reply_to_comment(
                account_token=context.replier_token,
                owner_id=context.owner_id,
                post_id=context.post_id,
                reply_to_comment=context.comment_id,
                text=context.reply_text,
            )
            logs.append(reply_result.message)
            if reply_result.success and reply_result.data and isinstance(reply_result.data.get("comment_id"), int):
                context.reply_id = int(reply_result.data["comment_id"])
            if not reply_result.success:
                if self._is_validation_required(reply_result):
                    self._pending_context = context
                    return ScenarioRunResult(
                        success=False,
                        message="Требуется подтверждение ответчика",
                        logs=logs,
                        requires_confirmation=True,
                        redirect_uri=self._extract_redirect_uri(reply_result),
                        comment_id=context.comment_id,
                        reply_id=context.reply_id,
                        post_id=context.post_id,
                        owner_id=context.owner_id,
                    )
                self._pending_context = None
                return ScenarioRunResult(
                    success=False,
                    message="Сценарий остановлен на ответе",
                    logs=logs,
                    comment_id=context.comment_id,
                    reply_id=context.reply_id,
                    post_id=context.post_id,
                    owner_id=context.owner_id,
                )
            context.next_step = "like"
            if context.delay_seconds > 0:
                time.sleep(context.delay_seconds)

        if context.next_step == "like":
            if context.comment_id is None:
                self._pending_context = None
                return ScenarioRunResult(success=False, message="Нет comment_id для лайка", logs=logs)
            like_result = self.comment_service.add_like_to_comment(
                account_token=context.liker_token,
                owner_id=context.owner_id,
                comment_id=context.comment_id,
                post_id=context.post_id,
            )
            logs.append(like_result.message)
            if not like_result.success:
                if self._is_validation_required(like_result):
                    self._pending_context = context
                    return ScenarioRunResult(
                        success=False,
                        message="Требуется подтверждение лайкера",
                        logs=logs,
                        requires_confirmation=True,
                        redirect_uri=self._extract_redirect_uri(like_result),
                        comment_id=context.comment_id,
                        reply_id=context.reply_id,
                        post_id=context.post_id,
                        owner_id=context.owner_id,
                    )
                self._pending_context = None
                logs.append(
                    "Лайк не поставлен, но комментарий и ответ уже отправлены"
                    if context.reply_required
                    else "Лайк не поставлен, но комментарий уже отправлен"
                )
                return ScenarioRunResult(
                    success=False,
                    message="Сценарий остановлен на лайке",
                    logs=logs,
                    comment_id=context.comment_id,
                    reply_id=context.reply_id,
                    post_id=context.post_id,
                    owner_id=context.owner_id,
                )

        logs.append("Сценарий завершен успешно")
        self._pending_context = None
        return ScenarioRunResult(
            success=True,
            message="Сценарий выполнен",
            logs=logs,
            comment_id=context.comment_id,
            reply_id=context.reply_id,
            post_id=context.post_id,
            owner_id=context.owner_id,
        )

    def _resolve_owner_id(self, token: str, group_ref: str) -> ActionResult:
        ok, owner_id, message = self.comment_service.resolve_owner_id(token, group_ref)
        if not ok or owner_id is None:
            return ActionResult(success=False, message=f"ошибка owner_id: {message}")
        return ActionResult(success=True, message=f"owner_id определен: {owner_id}", data={"owner_id": owner_id})

    @staticmethod
    def _extract_comment_id(result: ActionResult) -> int | None:
        if not result.data:
            return None
        raw_value = result.data.get("comment_id")
        if isinstance(raw_value, int):
            return raw_value
        return None
