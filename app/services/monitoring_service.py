"""Runtime-мониторинг опубликованных комментариев и ответов."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from app.services.comment_service import ActionResult, CommentService
from app.services.token_service import TokenRecord

AccountSelector = Callable[[str, str, set[tuple[str, str]]], list[TokenRecord]]


@dataclass(slots=True)
class MonitoredTask:
    """Задача мониторинга одного поста в группе."""

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
    require_reply: bool = True
    interval_seconds: int = 30
    last_run_monotonic: float | None = None
    # Если True — на тике ищем более новые посты и тоже комментируем их.
    track_new_posts: bool = False
    photo_path: str | None = None


@dataclass(slots=True)
class MonitoringTickResult:
    """Результат одного прохода мониторинга."""

    logs: list[str]
    exhausted_messages: list[str]


class MonitoringService:
    """Проверяет живые комментарии и восстанавливает удаленные."""

    def __init__(self, comment_service: CommentService | None = None) -> None:
        self.comment_service = comment_service or CommentService()
        # Ключ: group_ref + post_id — одна группа может вести несколько постов.
        self.tasks_by_key: dict[tuple[str, int], MonitoredTask] = {}
        # Глобально мёртвые аккаунты (бан VK / битый токен): (role, token)
        self.disabled_accounts: set[tuple[str, str]] = set()
        # Бан только в конкретной группе: (role, token, group_ref)
        self.disabled_in_group: set[tuple[str, str, str]] = set()
        # Группы, куда больше не ходим (все акки отвалились)
        self.paused_groups: set[str] = set()
        self._last_discovery_by_group: dict[str, float] = {}

    def register_task(self, task: MonitoredTask) -> None:
        self.tasks_by_key[(task.group_ref, task.post_id)] = task
        # Новая задача снимает паузу только если явно перерегистрировали после рестарта сценария.
        self.paused_groups.discard(task.group_ref)

    def clear(self) -> None:
        self.tasks_by_key.clear()
        self.disabled_accounts.clear()
        self.disabled_in_group.clear()
        self.paused_groups.clear()
        self._last_discovery_by_group.clear()

    def has_tasks(self) -> bool:
        return bool(self.tasks_by_key)

    def seconds_until_next_check(self) -> int:
        """Секунд до ближайшей проверки любой задачи (0 — можно проверять сразу)."""
        now = time.monotonic()
        best: float | None = None
        for task in self.tasks_by_key.values():
            interval = max(30, int(task.interval_seconds))
            if task.last_run_monotonic is None:
                return 0
            elapsed = now - task.last_run_monotonic
            remaining = max(0.0, float(interval) - elapsed)
            if best is None or remaining < best:
                best = remaining
        return int(best) if best is not None else 0

    def run_tick(self, account_selector: AccountSelector) -> MonitoringTickResult:
        logs: list[str] = []
        exhausted_messages: list[str] = []
        now = time.monotonic()

        # Сначала ищем новые посты по группам, где включён track_new_posts.
        for group_ref in sorted({task.group_ref for task in self.tasks_by_key.values()}):
            if group_ref in self.paused_groups:
                continue
            group_tasks = [task for task in self.tasks_by_key.values() if task.group_ref == group_ref]
            if not any(task.track_new_posts for task in group_tasks):
                continue
            template = group_tasks[0]
            interval = max(30, int(template.interval_seconds))
            last_discovery = self._last_discovery_by_group.get(group_ref)
            if last_discovery is not None and now - last_discovery < interval:
                continue
            self._discover_new_posts(template, account_selector, logs, exhausted_messages)
            self._last_discovery_by_group[group_ref] = time.monotonic()

        for task in list(self.tasks_by_key.values()):
            if task.group_ref in self.paused_groups:
                continue
            interval = max(30, int(task.interval_seconds))
            if task.last_run_monotonic is not None:
                if now - task.last_run_monotonic < interval:
                    continue

            logs.append(f"=== Мониторинг {task.group_ref} (post_id={task.post_id}) ===")
            try:
                check_token = self._pick_check_token(task, account_selector)
                if check_token is None:
                    self._pause_group(
                        task.group_ref,
                        f"[{task.group_ref}] нет доступного аккаунта для проверки — группа на паузе",
                        logs,
                        exhausted_messages,
                    )
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
                    self._disable_if_account_error(None, check_token, task.group_ref, comment_check)
                    if self._classify_error(comment_check) == "group" and self._pick_check_token(task, account_selector) is None:
                        self._pause_group(
                            task.group_ref,
                            f"[{task.group_ref}] все аккаунты без доступа к группе — группа на паузе",
                            logs,
                            exhausted_messages,
                        )
                    continue
                if not self._exists(comment_check):
                    logs.append(f"[{task.group_ref}] основной комментарий удален, восстанавливаю")
                    self._restore_comment_tree(task, account_selector, logs, exhausted_messages)
                    continue

                if not task.require_reply:
                    logs.append(f"[{task.group_ref}] комментарий на месте (ответ не используется)")
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
                    self._disable_if_account_error(None, check_token, task.group_ref, reply_check)
                    continue
                if not self._exists(reply_check):
                    logs.append(f"[{task.group_ref}] ответ удален, восстанавливаю")
                    self._restore_reply(task, account_selector, logs, exhausted_messages)
                    continue

                logs.append(f"[{task.group_ref}] комментарий и ответ на месте (post_id={task.post_id})")
            finally:
                task.last_run_monotonic = time.monotonic()

        return MonitoringTickResult(logs=logs, exhausted_messages=exhausted_messages)

    def _discover_new_posts(
        self,
        template: MonitoredTask,
        account_selector: AccountSelector,
        logs: list[str],
        exhausted_messages: list[str],
    ) -> None:
        check_token = self._pick_check_token(template, account_selector)
        if check_token is None:
            self._pause_group(
                template.group_ref,
                f"[{template.group_ref}] нет доступного аккаунта для поиска новых постов — группа на паузе",
                logs,
                exhausted_messages,
            )
            return

        wall_result = self.comment_service.list_wall_posts(
            account_token=check_token,
            owner_id=template.owner_id,
            limit=10,
        )
        if not wall_result.success:
            logs.append(f"[{template.group_ref}] новые посты: {wall_result.message}")
            self._disable_if_account_error(None, check_token, template.group_ref, wall_result)
            if self._classify_error(wall_result) == "group" and self._pick_check_token(template, account_selector) is None:
                self._pause_group(
                    template.group_ref,
                    f"[{template.group_ref}] все аккаунты без доступа к группе — группа на паузе",
                    logs,
                    exhausted_messages,
                )
            return

        posts = wall_result.data.get("posts", []) if wall_result.data else []
        if not isinstance(posts, list) or not posts:
            return

        # Если стратегия «закреп» и закреп есть — новые посты не гоняем.
        if template.prefer_pinned and any(
            isinstance(item, dict) and item.get("is_pinned") is True for item in posts
        ):
            return

        known_ids = {
            task.post_id
            for task in self.tasks_by_key.values()
            if task.group_ref == template.group_ref
        }
        new_post_ids = [
            int(item["id"])
            for item in posts
            if isinstance(item, dict) and isinstance(item.get("id"), int) and int(item["id"]) not in known_ids
        ]
        if not new_post_ids:
            return

        # Сначала самые новые (wall.get обычно отдаёт сверху вниз).
        for post_id in new_post_ids:
            logs.append(f"[{template.group_ref}] найден новый пост id={post_id}, оставляю комментарий")
            new_task = MonitoredTask(
                group_ref=template.group_ref,
                owner_id=template.owner_id,
                post_id=post_id,
                comment_text=template.comment_text,
                reply_text=template.reply_text,
                prefer_pinned=template.prefer_pinned,
                commentator_token=template.commentator_token,
                replier_token=template.replier_token,
                liker_token=template.liker_token,
                require_reply=template.require_reply,
                interval_seconds=template.interval_seconds,
                track_new_posts=template.track_new_posts,
                photo_path=template.photo_path,
                last_run_monotonic=None,
            )
            self.register_task(new_task)
            self._restore_comment_tree(new_task, account_selector, logs, exhausted_messages)
            new_task.last_run_monotonic = time.monotonic()

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
        if task.require_reply:
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
        records = self._available_records(account_selector, "commentator", task.group_ref)
        if not records:
            self._pause_group(
                task.group_ref,
                f"[{task.group_ref}] нет доступного комментатора — группа на паузе",
                logs,
                exhausted_messages,
            )
            return False

        for record in self._shuffled(records):
            result = self.comment_service.post_comment(
                account_token=record.token,
                owner_id=task.owner_id,
                post_id=task.post_id,
                text=task.comment_text,
                photo_path=task.photo_path,
            )
            logs.append(result.message)
            if result.success and result.data and isinstance(result.data.get("comment_id"), int):
                task.comment_id = int(result.data["comment_id"])
                task.commentator_token = record.token
                return True
            self._disable_if_account_error(record.role, record.token, task.group_ref, result)

        self._pause_group(
            task.group_ref,
            f"[{task.group_ref}] все комментаторы без доступа/забанены — группа на паузе",
            logs,
            exhausted_messages,
        )
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

        records = self._available_records(account_selector, "replier", task.group_ref)
        if not records:
            self._pause_group(
                task.group_ref,
                f"[{task.group_ref}] нет доступного ответчика — группа на паузе",
                logs,
                exhausted_messages,
            )
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
            self._disable_if_account_error(record.role, record.token, task.group_ref, result)

        self._pause_group(
            task.group_ref,
            f"[{task.group_ref}] все ответчики без доступа/забанены — группа на паузе",
            logs,
            exhausted_messages,
        )
        return False

    def _try_like(self, task: MonitoredTask, account_selector: AccountSelector, logs: list[str]) -> None:
        if task.comment_id is None:
            return

        records = self._available_records(account_selector, "liker", task.group_ref)
        if not records:
            logs.append(f"[{task.group_ref}] нет доступного лайкера")
            return

        for record in self._shuffled(records):
            result = self.comment_service.add_like_to_comment(
                account_token=record.token,
                owner_id=task.owner_id,
                comment_id=task.comment_id,
                post_id=task.post_id,
            )
            logs.append(result.message)
            if result.success:
                task.liker_token = record.token
                return
            self._disable_if_account_error(record.role, record.token, task.group_ref, result)

    def _pick_check_token(self, task: MonitoredTask, account_selector: AccountSelector) -> str | None:
        for role, token in (
            ("commentator", task.commentator_token),
            ("replier", task.replier_token),
            ("liker", task.liker_token),
        ):
            if token and not self._is_token_unavailable(role, token, task.group_ref):
                return token

        for role in ("commentator", "replier", "liker"):
            records = self._available_records(account_selector, role, task.group_ref)
            if records:
                return records[0].token
        return None

    def _available_records(
        self,
        account_selector: AccountSelector,
        role: str,
        group_ref: str,
    ) -> list[TokenRecord]:
        records = account_selector(role, group_ref, self.disabled_accounts)
        return [
            record
            for record in records
            if (record.role, record.token, group_ref) not in self.disabled_in_group
        ]

    def _pause_group(
        self,
        group_ref: str,
        message: str,
        logs: list[str],
        exhausted_messages: list[str],
    ) -> None:
        if group_ref in self.paused_groups:
            return
        self.paused_groups.add(group_ref)
        logs.append(message)
        exhausted_messages.append(message)

    def _disable_if_account_error(
        self,
        role: str | None,
        token: str,
        group_ref: str,
        result: ActionResult,
    ) -> None:
        if result.success:
            return
        kind = self._classify_error(result)
        if kind is None:
            return
        if kind == "global":
            if role is None:
                for role_name in ("commentator", "replier", "liker"):
                    self.disabled_accounts.add((role_name, token))
                return
            self.disabled_accounts.add((role, token))
            return

        # kind == "group": бан/нет доступа только в этой группе
        if role is None:
            for role_name in ("commentator", "replier", "liker"):
                self.disabled_in_group.add((role_name, token, group_ref))
            return
        self.disabled_in_group.add((role, token, group_ref))

    @staticmethod
    def _classify_error(result: ActionResult) -> str | None:
        """Возвращает 'global', 'group' или None."""
        message = result.message.lower()
        global_markers = (
            "invalid access_token",
            "authorization failed",
            "user is blocked",
            "user was deleted",
            "заблокирован vk",
        )
        if any(marker in message for marker in global_markers):
            return "global"

        code = result.data.get("error_code") if result.data else None
        if code in {5, 27, 28}:
            return "global"
        if code in {17, 30}:
            return "global"

        group_markers = (
            "access denied",
            "no access",
            "access to group",
            "access to post",
            "banned",
            "blocked from",
            "user was blocked",
            "community",
            "wall is disabled",
            "comments are disabled",
        )
        if code in {15, 203, 212, 213, 214} or any(marker in message for marker in group_markers):
            return "group"
        return None

    @staticmethod
    def _exists(result: ActionResult) -> bool:
        return bool(result.data and result.data.get("exists") is True)

    def _is_token_unavailable(self, role: str, token: str, group_ref: str) -> bool:
        if any(saved_token == token for _role, saved_token in self.disabled_accounts):
            return True
        return (role, token, group_ref) in self.disabled_in_group

    @staticmethod
    def _shuffled(records: list[TokenRecord]) -> list[TokenRecord]:
        result = list(records)
        random.shuffle(result)
        return result
