"""Интерфейсы слоя хранения и локальные заглушки репозиториев."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class GroupCommentsPool:
    """Связь между id группы и шаблонами комментариев и ответов."""

    group_id: str
    comments: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    post_strategy: str = "pinned"


class PoolsRepository:
    """Отвечает за локальное чтение и запись пулов групп/комментариев."""

    def __init__(self, storage_path: Path | None = None) -> None:
        self.storage_path = storage_path or (Path.cwd() / "group_comment_pools.json")

    def list_pools(self) -> list[GroupCommentsPool]:
        if not self.storage_path.exists():
            return []

        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        raw_items = payload.get("pools", [])
        result: list[GroupCommentsPool] = []

        for item in raw_items:
            group_id = str(item.get("group_id", "")).strip()
            comments = [str(comment).strip() for comment in item.get("comments", []) if str(comment).strip()]
            replies = [str(reply).strip() for reply in item.get("replies", []) if str(reply).strip()]
            raw_strategy = str(item.get("post_strategy", "pinned")).strip().lower()
            post_strategy = raw_strategy if raw_strategy in {"pinned", "latest"} else "pinned"
            if group_id:
                result.append(
                    GroupCommentsPool(
                        group_id=group_id,
                        comments=comments,
                        replies=replies,
                        post_strategy=post_strategy,
                    )
                )

        return result

    def save_pools(self, pools: list[GroupCommentsPool]) -> None:
        data = {
            "pools": [
                {
                    "group_id": pool.group_id,
                    "comments": pool.comments,
                    "replies": pool.replies,
                    "post_strategy": pool.post_strategy,
                }
                for pool in pools
            ]
        }
        self.storage_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
