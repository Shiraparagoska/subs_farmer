"""Runtime-мониторинг опубликованных комментариев и ответов."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from app.services.comment_service import ActionResult, CommentService
from app.services.token_service import TokenRecord

AccountSelector = Callable[[str, str, set[tuple[str, str]]], list[TokenRecord]]


@dataclass(slots=True)
class MonitoredTask:
    """Задача мониторинга одной группы."""

    group_ref: str
    owner_id: int
    post_id: int
    comment_text: str
    reply_text: str
    prefer_pinned: bool
    commentator_token: str
    replier_token: str
    liker_token: str | None = None
    comment_id: int | None = None
    reply_id: int | None = None


@dataclass(slots=True)
class MonitoringTickResult:
    """Результат одного прохода мониторинга."""

    logs: list[str]
    exhausted_messages: list[str]


class MonitoringService:
    """Проверяет живые комментарии и восстанавливает удаленные."""

    def __init__(self, comment_service: CommentService | None = None) -> None:
        self.comment_service = comment_service or CommentService()
        self.tasks_by_group: dict[str, MonitoredTask] = {}
        self.disabled_accounts: set[tuple[str, str]] = set()

    def register_task(self, task: MonitoredTask) -> None:
        self.tasks_by_group[task.group_ref] = task

    def clear(self) -> None:
        self.tasks_by_group.clear()
        self.disabled_accounts.clear()

    def has_tasks(self) -> bool:
        return bool(self.tasks_by_group)

    def run_tick(self, account_selector: AccountSelector) -> MonitoringTickResult:
        logs: list[str] = []
        exhausted_messages: list[str] = []

        for task in list(self.tasks_by_group.values()):
            logs.append(f"=== Мониторинг {task.group_ref} ===")
            check_token = self._pick_check_token(task, account_selector)
            if check_token is None:
                message = f"[{task.group_ref}] нет доступного аккаунта для проверки"
                logs.append(message)
                exhausted_messages.append(message)
                continue

            if task.comment_id is None:
                self._restore_comment_tree(task, account_selector, logs, exhausted_messages)
                continue

            comment_check = self.comment_service.comment_exists(
                account_token=check_token,
                owner_id=task.owner_id,
                post_id=task.post_id,
                comment_id=task.comment_id,
            )
            if not comment_check.success:
                logs.append(comment_check.message)
                self._disable_if_account_error("commentator", check_token, comment_check)
                continue
            if not self._exists(comment_check):
                logs.append(f"[{task.group_ref}] основной комментарий удален, восстанавливаю")
                self._restore_comment_tree(task, account_selector, logs, exhausted_messages)
                continue

            if task.reply_id is None:
                self._restore_reply(task, account_selector, logs, exhausted_messages)
                continue

            reply_check = self.comment_service.reply_exists(
                account_token=check_token,
                owner_id=task.owner_id,
                parent_comment_id=task.comment_id,
                reply_id=task.reply_id,
            )
            if not reply_check.success:
                logs.append(reply_check.message)
                self._disable_if_account_error("replier", check_token, reply_check)
                continue
            if not self._exists(reply_check):
                logs.append(f"[{task.group_ref}] ответ удален, восстанавливаю")
                self._restore_reply(task, account_selector, logs, exhausted_messages)
                continue

            logs.append(f"[{task.group_ref}] комментарий и ответ на месте")

        return MonitoringTickResult(logs=logs, exhausted_messages=exhausted_messages)

    def _restore_comment_tree(
        self,
        task: MonitoredTask,
        account_selector: AccountSelector,
        logs: list[str],
        exhausted_messages: list[str],
    ) -> None:
        old_comment_id = task.comment_id
        old_reply_id = task.reply_id
        fallback_delete_token = self._pick_check_token(task, account_selector)
        self._delete_old_comment(task, task.replier_token or fallback_delete_token, old_reply_id, logs)
        self._delete_old_comment(task, task.commentator_token or fallback_delete_token, old_comment_id, logs)
        task.comment_id = None
        task.reply_id = None
        if not self._restore_comment(task, account_selector, logs, exhausted_messages):
            return
        self._restore_reply(task, account_selector, logs, exhausted_messages)
        self._try_like(task, account_selector, logs)

    def _delete_old_comment(
        self,
        task: MonitoredTask,
        account_token: str | None,
        comment_id: int | None,
        logs: list[str],
    ) -> None:
        if comment_id is None or account_token is None:
            return
        delete_result = self.comment_service.delete_comment(
            account_token=account_token,
            owner_id=task.owner_id,
            comment_id=comment_id,
        )
        logs.append(delete_result.message)

    def _restore_comment(
        self,
        task: MonitoredTask,
        account_selector: AccountSelector,
        logs: list[str],
        exhausted_messages: list[str],
    ) -> bool:
        records = account_selector("commentator", task.group_ref, self.disabled_accounts)
        if not records:
            message = f"[{task.group_ref}] нет доступного комментатора для восстановления"
            logs.append(message)
            exhausted_messages.append(message)
            return False

        for record in self._shuffled(records):
            result = self.comment_service.post_comment(
                account_token=record.token,
                owner_id=task.owner_id,
                post_id=task.post_id,
                text=task.comment_text,
            )
            logs.append(result.message)
            if result.success and result.data and isinstance(result.data.get("comment_id"), int):
                task.comment_id = int(result.data["comment_id"])
                task.commentator_token = record.token
                return True
            self._disable_if_account_error("commentator", record.token, result)

        message = f"[{task.group_ref}] комментаторы закончились"
        logs.append(message)
        exhausted_messages.append(message)
        return False

    def _restore_reply(
        self,
        task: MonitoredTask,
        account_selector: AccountSelector,
        logs: list[str],
        exhausted_messages: list[str],
    ) -> bool:
        if task.comment_id is None:
            return False

        records = account_selector("replier", task.group_ref, self.disabled_accounts)
        if not records:
            message = f"[{task.group_ref}] нет доступного ответчика для восстановления"
            logs.append(message)
            exhausted_messages.append(message)
            return False

        for record in self._shuffled(records):
            result = self.comment_service.reply_to_comment(
                account_token=record.token,
                owner_id=task.owner_id,
                post_id=task.post_id,
                reply_to_comment=task.comment_id,
                text=task.reply_text,
            )
            logs.append(result.message)
            if result.success and result.data and isinstance(result.data.get("comment_id"), int):
                task.reply_id = int(result.data["comment_id"])
                task.replier_token = record.token
                return True
            self._disable_if_account_error("replier", record.token, result)

        message = f"[{task.group_ref}] ответчики закончились"
        logs.append(message)
        exhausted_messages.append(message)
        return False

    def _try_like(self, task: MonitoredTask, account_selector: AccountSelector, logs: list[str]) -> None:
        if task.comment_id is None:
            return

        records = account_selector("liker", task.group_ref, self.disabled_accounts)
        if not records:
            logs.append(f"[{task.group_ref}] нет доступного лайкера")
            return

        for record in self._shuffled(records):
            result = self.comment_service.add_like_to_comment(
                account_token=record.token,
                owner_id=task.owner_id,
                comment_id=task.comment_id,
            )
            logs.append(result.message)
            if result.success:
                task.liker_token = record.token
                return
            self._disable_if_account_error("liker", record.token, result)

    def _pick_check_token(self, task: MonitoredTask, account_selector: AccountSelector) -> str | None:
        for role, token in (
            ("commentator", task.commentator_token),
            ("replier", task.replier_token),
            ("liker", task.liker_token),
        ):
            if token and (role, token) not in self.disabled_accounts:
                return token

        for role in ("commentator", "replier", "liker"):
            records = account_selector(role, task.group_ref, self.disabled_accounts)
            if records:
                return records[0].token
        return None

    def _disable_if_account_error(self, role: str, token: str, result: ActionResult) -> None:
        if result.success:
            return
        if self._is_account_error(result):
            self.disabled_accounts.add((role, token))

    @staticmethod
    def _is_account_error(result: ActionResult) -> bool:
        if not result.data:
            message = result.message.lower()
            return any(fragment in message for fragment in ("invalid access_token", "authorization", "access denied"))
        code = result.data.get("error_code")
        return code in {5, 15, 17, 27, 28, 30}

    @staticmethod
    def _exists(result: ActionResult) -> bool:
        return bool(result.data and result.data.get("exists") is True)

    @staticmethod
    def _shuffled(records: list[TokenRecord]) -> list[TokenRecord]:
        result = list(records)
        random.shuffle(result)
        return result
