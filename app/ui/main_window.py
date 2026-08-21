"""Главное окно десктоп-приложения VK."""

from __future__ import annotations

import random
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.automation_service import AutomationService, ScenarioRunResult
from app.services.comment_service import CommentService
from app.services.monitoring_service import MonitoredTask, MonitoringService
from app.runtime_paths import application_directory
from app.services.token_service import TokenRecord, TokenService
from app.storage.repositories import GroupCommentsPool, PoolsRepository

ROLE_LABEL_TO_VALUE = {
    "Лайкер": "liker",
    "Комментатор": "commentator",
    "Ответчик": "replier",
}
ROLE_VALUE_TO_LABEL = {value: label for label, value in ROLE_LABEL_TO_VALUE.items()}
ROLE_FALLBACK_ORDER: dict[str, tuple[str, ...]] = {
    "commentator": ("commentator", "liker", "replier"),
    "replier": ("replier", "commentator", "liker"),
    "liker": ("liker", "commentator", "replier"),
}
POST_STRATEGY_LABEL_TO_VALUE = {
    "Закрепленный (или последний)": "pinned",
    "Только последний": "latest",
}
POST_STRATEGY_VALUE_TO_LABEL = {value: label for label, value in POST_STRATEGY_LABEL_TO_VALUE.items()}


class MainWindow(QMainWindow):
    """Основная оболочка приложения с временными вкладками."""

    def __init__(self) -> None:
        super().__init__()
        self.token_service = TokenService()
        self.comment_service = CommentService()
        self.automation_service = AutomationService(self.comment_service)
        self.monitoring_service = MonitoringService(self.comment_service)
        self._app_dir = application_directory()
        self.pools_repository = PoolsRepository(storage_path=self._app_dir / "group_comment_pools.json")
        self.token_records: list[TokenRecord] = []
        self.group_pools: list[GroupCommentsPool] = []
        self.last_comment_ids_by_group: dict[str, int] = {}
        self.last_log_minute: str | None = None
        self.monitoring_timer = QTimer(self)
        self.monitoring_timer.setInterval(10_000)
        self.monitoring_timer.timeout.connect(self._run_monitoring_tick)
        self.monitoring_countdown_seconds = 30
        self.monitoring_countdown_timer = QTimer(self)
        self.monitoring_countdown_timer.setInterval(1_000)
        self.monitoring_countdown_timer.timeout.connect(self._update_monitoring_countdown)
        self.tokens_file_path = self._app_dir / "access_tokens.txt"
        self.setWindowTitle("VK Помощник")
        self.resize(1100, 760)
        self._setup_layout()
        self._refresh_tokens_table()

    def _setup_layout(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_accounts_tab(), "Аккаунты")
        tabs.addTab(self._build_pools_tab(), "Пулы")
        tabs.addTab(self._build_actions_tab(), "Действия")
        tabs.addTab(self._create_placeholder("Журнал операций и диагностика API"), "Диагностика")

        self.setCentralWidget(tabs)
        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Готово")

    @staticmethod
    def _create_placeholder(text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel(text))
        layout.addStretch(1)
        return page

    def _build_accounts_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        button_row = QHBoxLayout()
        import_button = QPushButton("Импорт токенов")
        import_button.clicked.connect(self._select_tokens_file)
        button_row.addWidget(import_button)

        check_button = QPushButton("Проверить токены")
        check_button.clicked.connect(self._check_tokens)
        button_row.addWidget(check_button)

        check_likes_button = QPushButton("Проверить лайки")
        check_likes_button.clicked.connect(self._check_likes)
        button_row.addWidget(check_likes_button)

        check_comment_reply_button = QPushButton("Проверить комментарии/ответы")
        check_comment_reply_button.clicked.connect(self._check_comment_reply)
        button_row.addWidget(check_comment_reply_button)

        delete_button = QPushButton("Удалить токен")
        delete_button.clicked.connect(self._delete_selected_token)
        button_row.addWidget(delete_button)

        refresh_button = QPushButton("Обновить список")
        refresh_button.clicked.connect(self._refresh_tokens_table)
        button_row.addWidget(refresh_button)
        button_row.addStretch(1)

        self.tokens_file_label = QLabel("")
        self.tokens_table = QTableWidget()
        self.tokens_table.setColumnCount(5)
        self.tokens_table.setHorizontalHeaderLabels(["Токен", "Роль", "Группы", "Статус", "Комментарий"])
        self.tokens_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tokens_table.verticalHeader().setVisible(False)
        self.tokens_table.horizontalHeader().setStretchLastSection(True)

        layout.addLayout(button_row)
        layout.addWidget(self.tokens_file_label)
        layout.addWidget(self.tokens_table)
        return page

    def _select_tokens_file(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите файл с токенами",
            str(self._app_dir),
            "Текстовые файлы (*.txt);;Все файлы (*.*)",
        )
        if not file_path:
            return
        self.tokens_file_path = Path(file_path)
        self._refresh_tokens_table()

    def _check_tokens(self) -> None:
        if not self.token_records:
            self.statusBar().showMessage("Нет токенов для проверки")
            return

        self.statusBar().showMessage("Проверяю токены через VK API...")
        for record in self.token_records:
            self.token_service.validate_record(record)

        self._fill_tokens_table()
        valid_count = sum(1 for record in self.token_records if record.is_valid)
        blocked_count = sum(
            1
            for record in self.token_records
            if not record.is_valid
            and record.last_error
            and ("заблокирован" in record.last_error.lower() or "user is blocked" in record.last_error.lower())
        )
        invalid_count = len(self.token_records) - valid_count
        self.statusBar().showMessage(
            f"Проверка завершена: рабочих {valid_count} из {len(self.token_records)}"
            f" (невалидных {invalid_count}, из них в бане {blocked_count})"
        )

    def _check_likes(self) -> None:
        if not self.token_records:
            self.statusBar().showMessage("Нет токенов для проверки лайков")
            return
        if hasattr(self, "pools_table"):
            self.group_pools = self._collect_pools_from_table()
        if not self.group_pools:
            self.statusBar().showMessage("Добавьте хотя бы одну группу в пулы")
            return

        liker_records = self._find_records_by_role("liker")
        if not liker_records:
            self.statusBar().showMessage("Нет аккаунтов с ролью Лайкер")
            return

        checked_count = 0
        ok_count = 0
        self.statusBar().showMessage("Проверяю likes.add у лайкеров...")
        for record in liker_records:
            pool = self._pick_like_test_pool(record)
            if pool is None:
                record.likes_available = False
                record.last_like_error = "Нет разрешенной группы для теста лайков"
                checked_count += 1
                continue

            ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, pool.group_id)
            if not ok or owner_id is None:
                record.likes_available = False
                record.last_like_error = f"owner_id: {message}"
                checked_count += 1
                continue

            post_result = self.comment_service.find_target_post(
                account_token=record.token,
                owner_id=owner_id,
                prefer_pinned=pool.post_strategy != "latest",
            )
            if not post_result.success:
                record.likes_available = False
                record.last_like_error = post_result.message
                checked_count += 1
                continue
            if not post_result.data or not isinstance(post_result.data.get("post_id"), int):
                record.likes_available = False
                record.last_like_error = "Не удалось получить post_id для теста лайка"
                checked_count += 1
                continue

            like_result = self.comment_service.add_like(
                account_token=record.token,
                owner_id=owner_id,
                post_id=int(post_result.data["post_id"]),
            )
            record.likes_available = like_result.success
            record.last_like_error = None if like_result.success else like_result.message
            checked_count += 1
            if like_result.success:
                ok_count += 1

        self._fill_tokens_table()
        self.statusBar().showMessage(f"Проверка лайков завершена: OK {ok_count} из {checked_count}")

    def _check_comment_reply(self) -> None:
        if not self.token_records:
            self.statusBar().showMessage("Нет токенов для проверки комментариев/ответов")
            return
        if hasattr(self, "pools_table"):
            self.group_pools = self._collect_pools_from_table()
        if not self.group_pools:
            self.statusBar().showMessage("Добавьте хотя бы одну группу в пулы")
            return

        checked_count = 0
        ok_count = 0
        self.statusBar().showMessage("Проверяю wall.createComment у комментаторов/ответчиков...")

        for role, role_label in (("commentator", "Комментатор"), ("replier", "Ответчик")):
            role_records = self._find_records_by_role(role)
            for record in role_records:
                pool = self._pick_like_test_pool(record)
                if pool is None:
                    checked_count += 1
                    continue

                ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, pool.group_id)
                if not ok or owner_id is None:
                    record.last_error = f"{role_label}: owner_id: {message}"
                    checked_count += 1
                    continue

                post_result = self.comment_service.find_target_post(
                    account_token=record.token,
                    owner_id=owner_id,
                    prefer_pinned=pool.post_strategy != "latest",
                )
                if not post_result.success or not post_result.data or not isinstance(post_result.data.get("post_id"), int):
                    record.last_error = f"{role_label}: {post_result.message if not post_result.success else 'Не удалось получить post_id'}"
                    checked_count += 1
                    continue
                post_id = int(post_result.data["post_id"])

                marker_comment = "[CHECK] проверка комментария/ответа, можно удалить"
                comment_result = self.comment_service.post_comment(
                    account_token=record.token,
                    owner_id=owner_id,
                    post_id=post_id,
                    text=marker_comment,
                )
                checked_count += 1
                if not comment_result.success or not comment_result.data or not isinstance(comment_result.data.get("comment_id"), int):
                    record.last_error = f"{role_label}: {comment_result.message}"
                    continue

                temp_comment_id = int(comment_result.data["comment_id"])
                role_ok = True

                if role == "replier":
                    reply_result = self.comment_service.reply_to_comment(
                        account_token=record.token,
                        owner_id=owner_id,
                        post_id=post_id,
                        reply_to_comment=temp_comment_id,
                        text="[CHECK] тестовый ответ, можно удалить",
                    )
                    if not reply_result.success:
                        role_ok = False
                        record.last_error = f"{role_label}: {reply_result.message}"
                    elif reply_result.data and isinstance(reply_result.data.get("comment_id"), int):
                        self.comment_service.delete_comment(
                            account_token=record.token,
                            owner_id=owner_id,
                            comment_id=int(reply_result.data["comment_id"]),
                        )

                self.comment_service.delete_comment(
                    account_token=record.token,
                    owner_id=owner_id,
                    comment_id=temp_comment_id,
                )

                if role_ok:
                    ok_count += 1

        self._fill_tokens_table()
        self.statusBar().showMessage(f"Проверка комментариев/ответов завершена: OK {ok_count} из {checked_count}")

    def _delete_selected_token(self) -> None:
        current_row = self.tokens_table.currentRow()
        if current_row < 0 or current_row >= len(self.token_records):
            self.statusBar().showMessage("Выберите токен для удаления")
            return
        if not self.tokens_file_path.exists():
            self.statusBar().showMessage("Файл токенов не найден")
            return

        del self.token_records[current_row]
        try:
            self._save_tokens_file()
        except OSError as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить файл токенов: {error}")
            return

        self._fill_tokens_table()
        self._refresh_actions_selectors()
        self.statusBar().showMessage("Токен удален")

    def _refresh_tokens_table(self) -> None:
        if not self.tokens_file_path.exists():
            self.token_records = []
            self.tokens_file_label.setText(f"Файл токенов: {self.tokens_file_path} (не найден)")
            self.tokens_table.setRowCount(0)
            self._refresh_actions_selectors()
            self.statusBar().showMessage("Файл access_tokens.txt не найден")
            return

        roles_by_token = {record.token: record.role for record in self.token_records}
        groups_by_token = {record.token: list(record.allowed_groups or []) for record in self.token_records}
        likes_by_token = {record.token: record.likes_available for record in self.token_records}
        like_errors_by_token = {record.token: record.last_like_error for record in self.token_records}
        self.token_records = self.token_service.parse_txt(self.tokens_file_path)
        for record in self.token_records:
            record.role = roles_by_token.get(record.token, "liker")
            record.allowed_groups = groups_by_token.get(record.token, [])
            record.likes_available = likes_by_token.get(record.token)
            record.last_like_error = like_errors_by_token.get(record.token)
        self.tokens_file_label.setText(f"Файл токенов: {self.tokens_file_path}")
        self._fill_tokens_table()
        self._refresh_actions_selectors()
        self.statusBar().showMessage(f"Загружено токенов: {len(self.token_records)}")

    def _fill_tokens_table(self) -> None:
        self.tokens_table.setRowCount(len(self.token_records))
        for row_index, record in enumerate(self.token_records):
            self.tokens_table.setItem(row_index, 0, QTableWidgetItem(self._mask_token(record.token)))

            role_combo = QComboBox()
            for role_label, role_value in ROLE_LABEL_TO_VALUE.items():
                role_combo.addItem(role_label, role_value)
            selected_role = record.role if record.role in ROLE_VALUE_TO_LABEL else "liker"
            role_combo.setCurrentIndex(role_combo.findData(selected_role))
            role_combo.currentIndexChanged.connect(
                lambda _index, current_record=record, combo=role_combo: self._update_token_role(current_record, combo)
            )
            self.tokens_table.setCellWidget(row_index, 1, role_combo)

            groups_button = QPushButton(self._format_allowed_groups(record))
            groups_button.clicked.connect(
                lambda _checked=False, current_record=record: self._select_token_groups(current_record)
            )
            self.tokens_table.setCellWidget(row_index, 2, groups_button)

            if record.is_valid:
                status_text = "Валидный"
            elif record.last_error and (
                "заблокирован" in record.last_error.lower() or "user is blocked" in record.last_error.lower()
            ):
                status_text = "В бане"
            elif record.last_error:
                status_text = "Ошибка"
            else:
                status_text = "Не проверен"
            self.tokens_table.setItem(row_index, 3, QTableWidgetItem(status_text))
            comment_parts: list[str] = []
            if record.last_error:
                comment_parts.append(record.last_error)
            elif record.owner_name:
                comment_parts.append(record.owner_name)
            if record.likes_available is True:
                comment_parts.append("Лайки OK")
            elif record.likes_available is False:
                comment_parts.append(f"Лайки ошибка: {record.last_like_error or 'неизвестно'}")
            comment_text = " | ".join(comment_parts)
            self.tokens_table.setItem(row_index, 4, QTableWidgetItem(comment_text))

    def _update_token_role(self, record: TokenRecord, role_combo: QComboBox) -> None:
        role = role_combo.currentData()
        if not isinstance(role, str):
            return
        record.role = role
        self._refresh_actions_selectors()

    def _select_token_groups(self, record: TokenRecord) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Выберите группы для аккаунта")
        layout = QVBoxLayout(dialog)

        hint = QLabel("Пустой выбор = аккаунт доступен для всех групп")
        layout.addWidget(hint)

        groups_list = QListWidget()
        groups_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        selected_groups = set(record.allowed_groups or [])
        for pool in self.group_pools:
            item = QListWidgetItem(pool.group_id)
            item.setSelected(pool.group_id in selected_groups)
            groups_list.addItem(item)
        layout.addWidget(groups_list)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        record.allowed_groups = [item.text().strip() for item in groups_list.selectedItems() if item.text().strip()]
        self._fill_tokens_table()
        self._refresh_actions_selectors()

    @staticmethod
    def _format_allowed_groups(record: TokenRecord) -> str:
        groups = record.allowed_groups or []
        if not groups:
            return "Все группы"
        if len(groups) <= 2:
            return " || ".join(groups)
        return f"{groups[0]} || {groups[1]} +{len(groups) - 2}"

    @staticmethod
    def _mask_token(token: str) -> str:
        if len(token) <= 10:
            return "*" * len(token)
        return f"{token[:6]}...{token[-4:]}"

    def _save_tokens_file(self) -> None:
        tokens_text = "\n".join(record.token for record in self.token_records)
        if tokens_text:
            tokens_text += "\n"
        self.tokens_file_path.write_text(tokens_text, encoding="utf-8")

    def _build_pools_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel(
            "Комментарии/ответы через ||. Колонка «Пост» — закреп или последний. "
            "«Мониторинг (мин)» — как часто проверять эту группу (не чаще чем раз в 30 сек)."
        )
        layout.addWidget(hint)

        button_row = QHBoxLayout()
        add_row_button = QPushButton("Добавить группу")
        add_row_button.clicked.connect(self._add_pool_row)
        button_row.addWidget(add_row_button)

        delete_row_button = QPushButton("Удалить группу")
        delete_row_button.clicked.connect(self._delete_pool_row)
        button_row.addWidget(delete_row_button)

        load_button = QPushButton("Загрузить пулы")
        load_button.clicked.connect(self._load_pools_table)
        button_row.addWidget(load_button)

        save_button = QPushButton("Сохранить пулы")
        save_button.clicked.connect(self._save_pools_table)
        button_row.addWidget(save_button)
        button_row.addStretch(1)

        self.pools_table = QTableWidget()
        self.pools_table.setColumnCount(5)
        self.pools_table.setHorizontalHeaderLabels(
            [
                "ID/ссылка группы",
                "Комментарии (через ||)",
                "Ответы (через ||)",
                "Пост",
                "Мониторинг (мин)",
            ]
        )
        self.pools_table.verticalHeader().setVisible(False)
        self.pools_table.horizontalHeader().setStretchLastSection(True)
        self.pools_table.itemChanged.connect(self._refresh_actions_selectors)

        layout.addLayout(button_row)
        layout.addWidget(self.pools_table)

        self._load_pools_table()
        return page

    def _add_pool_row(self) -> None:
        row_index = self.pools_table.rowCount()
        self.pools_table.insertRow(row_index)
        self._set_post_strategy_cell(row_index, "pinned")
        self._set_monitoring_interval_cell(row_index, 0.5)
        self._refresh_accounts_group_selectors()
        self.statusBar().showMessage("Строка группы добавлена")

    def _delete_pool_row(self) -> None:
        current_row = self.pools_table.currentRow()
        if current_row < 0:
            self.statusBar().showMessage("Выберите строку для удаления")
            return
        self.pools_table.removeRow(current_row)
        self.group_pools = self._collect_pools_from_table()
        self._refresh_accounts_group_selectors()
        self._refresh_actions_selectors()
        self.statusBar().showMessage("Строка удалена")

    def _load_pools_table(self) -> None:
        pools = self.pools_repository.list_pools()
        self.group_pools = pools
        self.pools_table.setRowCount(len(pools))

        for row_index, pool in enumerate(pools):
            comments_text = "||".join(pool.comments)
            replies_text = "||".join(pool.replies)
            self.pools_table.setItem(row_index, 0, QTableWidgetItem(pool.group_id))
            self.pools_table.setItem(row_index, 1, QTableWidgetItem(comments_text))
            self.pools_table.setItem(row_index, 2, QTableWidgetItem(replies_text))
            self._set_post_strategy_cell(row_index, pool.post_strategy)
            self._set_monitoring_interval_cell(row_index, pool.monitoring_interval_seconds / 60.0)

        self._refresh_accounts_group_selectors()
        self._refresh_actions_selectors()
        self.statusBar().showMessage(f"Загружено пулов: {len(pools)}")

    def _save_pools_table(self) -> None:
        pools = self._collect_pools_from_table()

        try:
            self.pools_repository.save_pools(pools)
        except OSError as error:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить пулы: {error}")
            return

        self.group_pools = pools
        self._refresh_accounts_group_selectors()
        self._refresh_actions_selectors()
        self.statusBar().showMessage(f"Пулы сохранены: {len(pools)}")

    def _build_actions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        self.roles_info_label = QLabel("")
        layout.addWidget(self.roles_info_label)

        group_label = QLabel("Группы (из пула, можно выбрать несколько):")
        self.actions_group_list = QListWidget()
        self.actions_group_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.actions_group_list.itemSelectionChanged.connect(self._on_group_selection_changed)
        layout.addWidget(group_label)
        layout.addWidget(self.actions_group_list)

        self.select_all_groups_checkbox = QCheckBox("Выбрать все группы")
        self.select_all_groups_checkbox.toggled.connect(self._toggle_all_groups_selection)
        layout.addWidget(self.select_all_groups_checkbox)

        self.post_id_input = QLineEdit()
        self.post_id_input.setPlaceholderText("Например: 123")
        self.post_id_input.setVisible(False)

        self.comment_id_input = QLineEdit()
        self.comment_id_input.setPlaceholderText("Например: 456")
        self.comment_id_input.setVisible(False)

        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText("Введите текст комментария")
        self.comment_input.setFixedHeight(90)
        self.comment_input.setVisible(False)

        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("Введите текст ответа")
        self.reply_input.setFixedHeight(90)
        self.reply_input.setVisible(False)

        delay_label = QLabel("Пауза между шагами (сек):")
        self.delay_input = QLineEdit()
        self.delay_input.setPlaceholderText("Например: 5")
        self.delay_input.setText("3")
        layout.addWidget(delay_label)
        layout.addWidget(self.delay_input)

        photo_label = QLabel("Фото к комментарию (превью; YouTube в комменте сам не встраивается):")
        layout.addWidget(photo_label)
        photo_row = QHBoxLayout()
        self.comment_photo_input = QLineEdit()
        self.comment_photo_input.setPlaceholderText("Путь к jpg/png или скачай превью YouTube")
        photo_row.addWidget(self.comment_photo_input)
        pick_photo_button = QPushButton("Выбрать...")
        pick_photo_button.clicked.connect(self._pick_comment_photo)
        photo_row.addWidget(pick_photo_button)
        yt_thumb_button = QPushButton("Превью из YouTube")
        yt_thumb_button.clicked.connect(self._download_youtube_preview_for_comment)
        photo_row.addWidget(yt_thumb_button)
        clear_photo_button = QPushButton("Очистить")
        clear_photo_button.clicked.connect(lambda: self.comment_photo_input.clear())
        photo_row.addWidget(clear_photo_button)
        layout.addLayout(photo_row)

        button_row = QHBoxLayout()
        comment_button = QPushButton("Комментатор: оставить комментарий")
        comment_button.clicked.connect(self._send_comment_by_commentator)
        button_row.addWidget(comment_button)

        like_button = QPushButton("Лайкер: лайкнуть комментарий")
        like_button.clicked.connect(self._send_comment_like_by_liker)
        button_row.addWidget(like_button)

        reply_button = QPushButton("Ответчик: ответить на комментарий")
        reply_button.clicked.connect(self._send_reply_by_replier)
        button_row.addWidget(reply_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        auto_row = QHBoxLayout()
        scenario_button = QPushButton("Авто: комментарий -> ответ -> лайк")
        scenario_button.setStyleSheet("QPushButton { background-color: #2e7d32; color: white; font-weight: 600; }")
        scenario_button.clicked.connect(self._run_auto_scenario)
        auto_row.addWidget(scenario_button)

        self.continue_scenario_button = QPushButton("Продолжить после подтверждения")
        self.continue_scenario_button.setEnabled(False)
        self.continue_scenario_button.clicked.connect(self._continue_auto_scenario)
        auto_row.addWidget(self.continue_scenario_button)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)

        monitoring_row = QHBoxLayout()
        start_monitoring_button = QPushButton("Запустить мониторинг")
        start_monitoring_button.setStyleSheet("QPushButton { background-color: #2e7d32; color: white; font-weight: 600; }")
        start_monitoring_button.clicked.connect(self._start_monitoring)
        monitoring_row.addWidget(start_monitoring_button)

        stop_monitoring_button = QPushButton("Остановить мониторинг")
        stop_monitoring_button.clicked.connect(self._stop_monitoring)
        monitoring_row.addWidget(stop_monitoring_button)

        self.monitoring_status_label = QLabel("Мониторинг: выключен")
        monitoring_row.addWidget(self.monitoring_status_label)
        monitoring_row.addStretch(1)
        layout.addLayout(monitoring_row)

        result_label = QLabel("Результат:")
        self.actions_result_output = QPlainTextEdit()
        self.actions_result_output.setReadOnly(True)
        self.actions_result_output.setFixedHeight(220)
        self.actions_result_output.setPlainText("-")
        layout.addWidget(result_label)
        layout.addWidget(self.actions_result_output)

        copy_button = QPushButton("Копировать ошибку")
        copy_button.clicked.connect(self._copy_action_result)
        layout.addWidget(copy_button)
        layout.addStretch(1)

        self._refresh_actions_selectors()
        return page

    def _refresh_actions_selectors(self) -> None:
        if not hasattr(self, "actions_group_list"):
            return
        if hasattr(self, "pools_table"):
            self.group_pools = self._collect_pools_from_table()

        selected_before = set(self._selected_group_refs())
        self.actions_group_list.clear()
        for pool in self.group_pools:
            item = QListWidgetItem(pool.group_id)
            self.actions_group_list.addItem(item)
            if self.select_all_groups_checkbox.isChecked() or pool.group_id in selected_before:
                item.setSelected(True)

        self._refresh_roles_info()
        self._fill_comment_from_selected_group()

    def _toggle_all_groups_selection(self, checked: bool) -> None:
        if checked:
            self.actions_group_list.selectAll()
        else:
            self.actions_group_list.clearSelection()
        self._fill_comment_from_selected_group()

    def _on_group_selection_changed(self) -> None:
        total_groups = self.actions_group_list.count()
        selected_count = len(self.actions_group_list.selectedItems())
        self.select_all_groups_checkbox.blockSignals(True)
        self.select_all_groups_checkbox.setChecked(total_groups > 0 and selected_count == total_groups)
        self.select_all_groups_checkbox.blockSignals(False)
        self._fill_comment_from_selected_group()

    def _refresh_roles_info(self) -> None:
        if not hasattr(self, "roles_info_label"):
            return
        commentator_name = self._role_account_label("commentator")
        liker_name = self._role_account_label("liker")
        replier_name = self._role_account_label("replier")
        self.roles_info_label.setText(
            f"Роли из вкладки Аккаунты -> Комментатор: {commentator_name}, "
            f"Лайкер: {liker_name}, Ответчик: {replier_name}"
        )

    def _role_account_label(self, role: str) -> str:
        records = self._find_records_by_role(role)
        if not records:
            return "не назначен"
        if len(records) > 1:
            return f"{len(records)} аккаунтов"
        record = records[0]
        token_name = record.owner_name or "Аккаунт без имени"
        return f"{token_name} ({self._mask_token(record.token)})"

    def _find_record_by_role(self, role: str) -> TokenRecord | None:
        records = self._find_records_by_role(role)
        return records[0] if records else None

    def _find_records_by_role(self, role: str) -> list[TokenRecord]:
        return [record for record in self.token_records if record.role == role]

    def _find_records_for_group(self, role: str, group_ref: str) -> list[TokenRecord]:
        normalized_group = self._normalize_group_ref_for_match(group_ref)
        records: list[TokenRecord] = []
        for record in self._find_records_by_role(role):
            allowed_groups = record.allowed_groups or []
            if not allowed_groups:
                records.append(record)
                continue
            normalized_allowed = {self._normalize_group_ref_for_match(item) for item in allowed_groups}
            if normalized_group in normalized_allowed:
                records.append(record)
        return records

    def _find_records_for_group_filtered(
        self,
        role: str,
        group_ref: str,
        disabled_accounts: set[tuple[str, str]],
        require_likes_available: bool = False,
    ) -> list[TokenRecord]:
        return [
            record
            for record in self._find_records_for_group(role, group_ref)
            if (record.role, record.token) not in disabled_accounts
            and (not require_likes_available or record.likes_available is not False)
        ]

    def _pick_record_for_group_with_fallback(
        self,
        role: str,
        group_ref: str,
        disabled_accounts: set[tuple[str, str]] | None = None,
        require_likes_available: bool = False,
    ) -> tuple[TokenRecord | None, str | None]:
        disabled = disabled_accounts or set()
        role_order = ROLE_FALLBACK_ORDER.get(role, (role,))
        for candidate_role in role_order:
            records = self._find_records_for_group_filtered(
                candidate_role,
                group_ref,
                disabled,
                require_likes_available=require_likes_available,
            )
            if not records:
                continue
            shuffled = list(records)
            random.shuffle(shuffled)
            return shuffled[0], candidate_role
        return None, None

    def _collect_records_for_group_with_fallback(
        self,
        role: str,
        group_ref: str,
        exclude_tokens: set[str] | None = None,
        require_likes_available: bool = False,
    ) -> list[tuple[TokenRecord, str]]:
        excluded = exclude_tokens or set()
        result: list[tuple[TokenRecord, str]] = []
        for candidate_role in ROLE_FALLBACK_ORDER.get(role, (role,)):
            records = self._find_records_for_group(candidate_role, group_ref)
            shuffled = list(records)
            random.shuffle(shuffled)
            for record in shuffled:
                if record.token in excluded:
                    continue
                if require_likes_available and record.likes_available is False:
                    continue
                result.append((record, candidate_role))
        return result

    @staticmethod
    def _is_account_error_text(text: str) -> bool:
        lowered = text.lower()
        return any(fragment in lowered for fragment in ("invalid access_token", "authorization failed", "access denied"))

    def _has_like_account_error(self, logs: list[str]) -> bool:
        for line in reversed(logs):
            lowered = line.lower()
            if "likes.add" in lowered and self._is_account_error_text(line):
                return True
            if "сценарий остановлен на лайке" in lowered:
                break
        return False

    @staticmethod
    def _normalize_group_ref_for_match(group_ref: str) -> str:
        value = group_ref.strip().lower()
        for prefix in ("https://vk.com/", "http://vk.com/", "vk.com/"):
            if value.startswith(prefix):
                value = value[len(prefix) :]
                break
        return value.strip().rstrip("/").split("?")[0].split("#")[0]

    def _pick_like_test_pool(self, record: TokenRecord) -> GroupCommentsPool | None:
        allowed_groups = record.allowed_groups or []
        if not allowed_groups:
            return self.group_pools[0] if self.group_pools else None
        normalized_allowed = {self._normalize_group_ref_for_match(item) for item in allowed_groups}
        for pool in self.group_pools:
            if self._normalize_group_ref_for_match(pool.group_id) in normalized_allowed:
                return pool
        return None

    def _fill_comment_from_selected_group(self) -> None:
        if (
            not hasattr(self, "actions_group_list")
            or not hasattr(self, "comment_input")
            or not hasattr(self, "reply_input")
        ):
            return
        selected_groups = self._selected_group_refs()
        if len(selected_groups) != 1:
            return
        group_ref = selected_groups[0]
        for pool in self.group_pools:
            if pool.group_id != group_ref:
                continue
            self.comment_input.setPlainText(pool.comments[0] if pool.comments else "")
            if pool.replies:
                self.reply_input.setPlainText(pool.replies[0])
            else:
                self.reply_input.setPlainText(pool.comments[0] if pool.comments else "")
            return
        self.comment_input.clear()
        self.reply_input.clear()

    def _selected_group_refs(self) -> list[str]:
        if not hasattr(self, "actions_group_list"):
            return []
        selected_groups: list[str] = []
        for item in self.actions_group_list.selectedItems():
            group_ref = item.text().strip()
            if group_ref:
                selected_groups.append(group_ref)
        return selected_groups

    def _collect_pools_from_table(self) -> list[GroupCommentsPool]:
        pools: list[GroupCommentsPool] = []
        for row_index in range(self.pools_table.rowCount()):
            group_item = self.pools_table.item(row_index, 0)
            comments_item = self.pools_table.item(row_index, 1)
            replies_item = self.pools_table.item(row_index, 2)
            strategy_combo = self.pools_table.cellWidget(row_index, 3)
            interval_spin = self.pools_table.cellWidget(row_index, 4)

            group_id = group_item.text().strip() if group_item else ""
            comments_raw = comments_item.text().strip() if comments_item else ""
            replies_raw = replies_item.text().strip() if replies_item else ""
            comments = [item.strip() for item in comments_raw.split("||") if item.strip()]
            replies = [item.strip() for item in replies_raw.split("||") if item.strip()]
            strategy_value = "pinned"
            if isinstance(strategy_combo, QComboBox):
                selected_strategy = strategy_combo.currentData()
                if isinstance(selected_strategy, str) and selected_strategy in {"pinned", "latest"}:
                    strategy_value = selected_strategy
            monitoring_seconds = 30
            if isinstance(interval_spin, QDoubleSpinBox):
                monitoring_seconds = max(30, int(round(interval_spin.value() * 60.0)))
            if group_id:
                pools.append(
                    GroupCommentsPool(
                        group_id=group_id,
                        comments=comments,
                        replies=replies,
                        post_strategy=strategy_value,
                        monitoring_interval_seconds=monitoring_seconds,
                    )
                )
        return pools

    def _set_post_strategy_cell(self, row_index: int, strategy_value: str) -> None:
        strategy_combo = QComboBox()
        for label, value in POST_STRATEGY_LABEL_TO_VALUE.items():
            strategy_combo.addItem(label, value)
        normalized = strategy_value if strategy_value in {"pinned", "latest"} else "pinned"
        strategy_combo.setCurrentIndex(strategy_combo.findData(normalized))
        strategy_combo.currentIndexChanged.connect(self._refresh_actions_selectors)
        self.pools_table.setCellWidget(row_index, 3, strategy_combo)

    def _set_monitoring_interval_cell(self, row_index: int, minutes_value: float) -> None:
        spin = QDoubleSpinBox()
        spin.setRange(0.5, 10_080.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" мин")
        normalized = max(0.5, float(minutes_value))
        spin.setValue(normalized)
        spin.valueChanged.connect(self._refresh_actions_selectors)
        self.pools_table.setCellWidget(row_index, 4, spin)

    def _refresh_accounts_group_selectors(self) -> None:
        if not hasattr(self, "tokens_table"):
            return
        if hasattr(self, "pools_table"):
            self.group_pools = self._collect_pools_from_table()

        for row_index, record in enumerate(self.token_records):
            selector = self.tokens_table.cellWidget(row_index, 2)
            if isinstance(selector, QPushButton):
                selector.setText(self._format_allowed_groups(record))
                continue
            if not isinstance(selector, QListWidget):
                continue
            selected_groups = set(record.allowed_groups or [])
            selector.blockSignals(True)
            selector.clear()
            for pool in self.group_pools:
                item = QListWidgetItem(pool.group_id)
                item.setSelected(pool.group_id in selected_groups)
                selector.addItem(item)
            selector.blockSignals(False)

    def _selected_comment_photo_path(self) -> str | None:
        if not hasattr(self, "comment_photo_input"):
            return None
        value = self.comment_photo_input.text().strip()
        if not value:
            return None
        path = Path(value)
        if not path.exists() or not path.is_file():
            return None
        return str(path)

    def _pick_comment_photo(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Выберите фото для комментария",
            "",
            "Images (*.jpg *.jpeg *.png *.webp);;All files (*.*)",
        )
        if file_path:
            self.comment_photo_input.setText(file_path)
            self.statusBar().showMessage(f"Фото к комментарию: {Path(file_path).name}")

    def _download_youtube_preview_for_comment(self) -> None:
        candidates: list[str] = []
        if hasattr(self, "comment_input"):
            candidates.append(self.comment_input.toPlainText())
        if hasattr(self, "reply_input"):
            candidates.append(self.reply_input.toPlainText())
        for group_ref in self._selected_group_refs():
            pool = next((item for item in self.group_pools if item.group_id == group_ref), None)
            if pool and pool.comments:
                candidates.extend(pool.comments)
            if pool and pool.replies:
                candidates.extend(pool.replies)

        source_text = next(
            (text for text in candidates if CommentService.extract_youtube_video_id(text)),
            "",
        )
        if not source_text:
            QMessageBox.information(
                self,
                "YouTube превью",
                "Не нашёл ссылку YouTube в тексте комментария/пуле.\n"
                "Вставь ссылку в комментарий пула или в поле текста, затем нажми снова.",
            )
            return

        result = self.comment_service.download_youtube_thumbnail(source_text)
        if not result.success or not result.data or not isinstance(result.data.get("path"), str):
            QMessageBox.warning(self, "YouTube превью", result.message)
            return

        self.comment_photo_input.setText(result.data["path"])
        self.statusBar().showMessage(result.message)
        self._set_action_result(result.message)

    def _resolve_base_context(self, require_post_id: bool = True) -> tuple[str | None, int | None]:
        selected_groups = self._selected_group_refs()
        group_ref = selected_groups[0] if selected_groups else None
        post_id_raw = self.post_id_input.text().strip() if self.post_id_input.isVisible() else ""

        if not isinstance(group_ref, str) or not group_ref:
            self._set_action_result("выберите группу")
            return None, None
        if not post_id_raw:
            if require_post_id:
                self._set_action_result("укажите корректный ID поста (число)")
                return None, None
            return group_ref, None
        if not post_id_raw.isdigit():
            self._set_action_result("укажите корректный ID поста (число)")
            return None, None

        return group_ref, int(post_id_raw)

    def _send_comment_by_commentator(self) -> None:
        selected_groups = self._selected_group_refs()
        _group_ref, post_id = self._resolve_base_context(require_post_id=False)
        per_group_post_id = post_id if len(selected_groups) == 1 else None
        if not self._find_records_by_role("commentator"):
            self._set_action_result("назначьте роль Комментатор на вкладке Аккаунты")
            return
        if not selected_groups:
            return

        global_comment_text = self.comment_input.toPlainText().strip()
        result_messages: list[str] = []
        last_comment_id: int | None = None
        last_post_id: int | None = None
        for group_ref in selected_groups:
            record, used_role = self._pick_record_for_group_with_fallback("commentator", group_ref)
            if record is None or used_role is None:
                result_messages.append(f"[{group_ref}] пропуск: нет доступного аккаунта для комментирования")
                continue
            if used_role != "commentator":
                result_messages.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(used_role, used_role)} -> Комментатор"
                )
            pool = next((item for item in self.group_pools if item.group_id == group_ref), None)
            comment_text = pool.comments[0] if pool and pool.comments else global_comment_text
            if not comment_text:
                result_messages.append(f"[{group_ref}] пропуск: пустой комментарий")
                continue

            ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
            if not ok or owner_id is None:
                result_messages.append(f"[{group_ref}] ошибка owner_id: {message}")
                continue

            selected_post_id = per_group_post_id
            result_messages.append(f"=== Группа {group_ref} ===")
            if selected_post_id is None:
                prefer_pinned = True if not pool else pool.post_strategy != "latest"
                post_result = self.comment_service.find_target_post(
                    account_token=record.token,
                    owner_id=owner_id,
                    prefer_pinned=prefer_pinned,
                )
                result_messages.append(post_result.message)
                if not post_result.success:
                    continue
                if not post_result.data or not isinstance(post_result.data.get("post_id"), int):
                    result_messages.append("Не удалось получить post_id")
                    continue
                selected_post_id = int(post_result.data["post_id"])
            last_post_id = selected_post_id

            result = self.comment_service.post_comment(
                account_token=record.token,
                owner_id=owner_id,
                post_id=selected_post_id,
                text=comment_text,
                photo_path=self._selected_comment_photo_path(),
            )
            if result.success and result.data and isinstance(result.data.get("comment_id"), int):
                last_comment_id = int(result.data["comment_id"])
                self.last_comment_ids_by_group[group_ref] = last_comment_id
            result_messages.append(result.message)

        if len(selected_groups) == 1:
            group_ref = selected_groups[0]
            saved_comment_id = self.last_comment_ids_by_group.get(group_ref)
            if last_post_id is not None:
                self.post_id_input.setText(str(last_post_id))
            if saved_comment_id is not None:
                self.comment_id_input.setText(str(saved_comment_id))
        elif last_comment_id is not None:
            self.comment_id_input.clear()

        self._set_action_result("\n".join(result_messages) if result_messages else "Нет комментариев для отправки")
        self.statusBar().showMessage("Комментарии обработаны")

    def _send_comment_like_by_liker(self) -> None:
        selected_groups = self._selected_group_refs()
        _group_ref, post_id = self._resolve_base_context(require_post_id=False)
        per_group_post_id = post_id if len(selected_groups) == 1 else None
        if not self._find_records_by_role("liker"):
            self._set_action_result("назначьте роль Лайкер на вкладке Аккаунты")
            return
        if not selected_groups:
            return

        comment_id_raw = self.comment_id_input.text().strip()
        if not self.comment_id_input.isVisible():
            comment_id_raw = ""
        field_comment_id = int(comment_id_raw) if comment_id_raw.isdigit() and len(selected_groups) == 1 else None
        result_messages: list[str] = []
        for group_ref in selected_groups:
            record, used_role = self._pick_record_for_group_with_fallback(
                "liker",
                group_ref,
                require_likes_available=True,
            )
            if record is None or used_role is None:
                result_messages.append(f"[{group_ref}] пропуск: нет доступного аккаунта для лайка")
                continue
            if used_role != "liker":
                result_messages.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(used_role, used_role)} -> Лайкер"
                )
            comment_id = field_comment_id or self.last_comment_ids_by_group.get(group_ref)
            if comment_id is None:
                result_messages.append(f"[{group_ref}] пропуск: нет ID комментария")
                continue

            ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
            if not ok or owner_id is None:
                result_messages.append(f"[{group_ref}] ошибка owner_id: {message}")
                continue

            result_messages.append(f"=== Группа {group_ref} ===")
            result = self.comment_service.add_like_to_comment(
                account_token=record.token,
                owner_id=owner_id,
                comment_id=comment_id,
                post_id=per_group_post_id,
            )
            result_messages.append(result.message)

        self._set_action_result("\n".join(result_messages) if result_messages else "Нет комментариев для лайка")
        self.statusBar().showMessage("Лайки обработаны")

    def _send_reply_by_replier(self) -> None:
        selected_groups = self._selected_group_refs()
        _group_ref, post_id = self._resolve_base_context(require_post_id=False)
        per_group_post_id = post_id if len(selected_groups) == 1 else None
        if not self._find_records_by_role("replier"):
            self._set_action_result("назначьте роль Ответчик на вкладке Аккаунты")
            return
        if not selected_groups:
            return

        comment_id_raw = self.comment_id_input.text().strip()
        if not self.comment_id_input.isVisible():
            comment_id_raw = ""
        field_comment_id = int(comment_id_raw) if comment_id_raw.isdigit() and len(selected_groups) == 1 else None
        global_reply_text = self.reply_input.toPlainText().strip()
        result_messages: list[str] = []
        last_post_id: int | None = None
        for group_ref in selected_groups:
            record, used_role = self._pick_record_for_group_with_fallback("replier", group_ref)
            if record is None or used_role is None:
                result_messages.append(f"[{group_ref}] пропуск: нет доступного аккаунта для ответа")
                continue
            if used_role != "replier":
                result_messages.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(used_role, used_role)} -> Ответчик"
                )
            pool = next((item for item in self.group_pools if item.group_id == group_ref), None)
            comment_id = field_comment_id or self.last_comment_ids_by_group.get(group_ref)
            if comment_id is None:
                result_messages.append(f"[{group_ref}] пропуск: нет ID комментария")
                continue

            reply_text = pool.replies[0] if pool and pool.replies else (
                pool.comments[0] if pool and pool.comments else global_reply_text
            )
            if not reply_text:
                result_messages.append(f"[{group_ref}] пропуск: пустой ответ")
                continue

            ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
            if not ok or owner_id is None:
                result_messages.append(f"[{group_ref}] ошибка owner_id: {message}")
                continue

            selected_post_id = per_group_post_id
            result_messages.append(f"=== Группа {group_ref} ===")
            if selected_post_id is None:
                prefer_pinned = True if not pool else pool.post_strategy != "latest"
                post_result = self.comment_service.find_target_post(
                    account_token=record.token,
                    owner_id=owner_id,
                    prefer_pinned=prefer_pinned,
                )
                result_messages.append(post_result.message)
                if not post_result.success:
                    continue
                if not post_result.data or not isinstance(post_result.data.get("post_id"), int):
                    result_messages.append("Не удалось получить post_id")
                    continue
                selected_post_id = int(post_result.data["post_id"])
            last_post_id = selected_post_id

            result = self.comment_service.reply_to_comment(
                account_token=record.token,
                owner_id=owner_id,
                post_id=selected_post_id,
                reply_to_comment=comment_id,
                text=reply_text,
            )
            result_messages.append(result.message)

        if len(selected_groups) == 1 and last_post_id is not None:
            self.post_id_input.setText(str(last_post_id))

        self._set_action_result("\n".join(result_messages) if result_messages else "Нет ответов для отправки")
        self.statusBar().showMessage("Ответы обработаны")

    def _run_auto_scenario(self) -> None:
        selected_groups = self._selected_group_refs()
        _group_ref, post_id = self._resolve_base_context(require_post_id=False)
        per_group_post_id = post_id if len(selected_groups) == 1 else None

        if not self.token_records:
            self._set_action_result("добавьте аккаунты на вкладке Аккаунты")
            return
        if not selected_groups:
            return

        delay_raw = self.delay_input.text().strip().replace(",", ".")
        try:
            delay_seconds = float(delay_raw)
            if delay_seconds < 0:
                raise ValueError("negative delay")
        except ValueError:
            self._set_action_result("укажите корректную паузу (например: 1.5)")
            return

        global_comment_text = self.comment_input.toPlainText().strip()
        global_reply_text = self.reply_input.toPlainText().strip()
        logs: list[str] = []
        last_result: ScenarioRunResult | None = None

        self.statusBar().showMessage(f"Запуск автосценария по группам: {len(selected_groups)}")
        for group_ref in selected_groups:
            pool = next((item for item in self.group_pools if item.group_id == group_ref), None)
            comment_text = pool.comments[0] if pool and pool.comments else global_comment_text
            require_reply = bool(pool.replies) if pool else bool(global_reply_text.strip())
            reply_text = pool.replies[0] if pool and pool.replies else ("" if pool else global_reply_text)
            prefer_pinned = True if not pool else pool.post_strategy != "latest"

            if not comment_text:
                logs.append(f"[{group_ref}] пропуск: пустой комментарий")
                continue
            if require_reply and not reply_text.strip():
                logs.append(f"[{group_ref}] пропуск: нужен ответ, но текст ответа пуст")
                continue

            commentator_record, commentator_used_role = self._pick_record_for_group_with_fallback(
                "commentator",
                group_ref,
            )
            liker_record, liker_used_role = self._pick_record_for_group_with_fallback(
                "liker",
                group_ref,
                require_likes_available=True,
            )
            replier_record = commentator_record
            replier_used_role = "commentator"
            if require_reply:
                replier_record, replier_used_role = self._pick_record_for_group_with_fallback(
                    "replier",
                    group_ref,
                )
            if commentator_record is None:
                logs.append(f"[{group_ref}] пропуск: нет доступного аккаунта для комментирования")
                continue
            if liker_record is None:
                logs.append(f"[{group_ref}] пропуск: нет доступного аккаунта для лайка")
                continue
            if require_reply and replier_record is None:
                logs.append(f"[{group_ref}] пропуск: нет доступного аккаунта для ответа")
                continue

            logs.append(f"=== Группа {group_ref} ===")
            if commentator_used_role and commentator_used_role != "commentator":
                logs.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(commentator_used_role, commentator_used_role)} -> Комментатор"
                )
            if require_reply and replier_used_role and replier_used_role != "replier":
                logs.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(replier_used_role, replier_used_role)} -> Ответчик"
                )
            if liker_used_role and liker_used_role != "liker":
                logs.append(
                    f"[{group_ref}] подмена роли: {ROLE_VALUE_TO_LABEL.get(liker_used_role, liker_used_role)} -> Лайкер"
                )
            result = self.automation_service.run_comment_like_reply_scenario(
                commentator_token=commentator_record.token,
                liker_token=liker_record.token,
                replier_token=replier_record.token,
                group_ref=group_ref,
                post_id=per_group_post_id,
                comment_text=comment_text,
                reply_text=reply_text,
                prefer_pinned=prefer_pinned,
                delay_seconds=delay_seconds,
                reply_required=require_reply,
                photo_path=self._selected_comment_photo_path(),
            )
            if (
                require_reply
                and not result.success
                and not result.requires_confirmation
                and result.comment_id is not None
                and result.post_id is not None
                and result.owner_id is not None
                and result.reply_id is None
                and result.logs
                and self._is_account_error_text(result.logs[-1])
            ):
                used_tokens = {commentator_record.token, replier_record.token}
                recovery_done = False
                for candidate_record, candidate_role in self._collect_records_for_group_with_fallback(
                    "replier",
                    group_ref,
                    exclude_tokens=used_tokens,
                ):
                    if candidate_role != "replier":
                        result.logs.append(
                            f"[{group_ref}] подмена роли (recovery): {ROLE_VALUE_TO_LABEL.get(candidate_role, candidate_role)} -> Ответчик"
                        )
                    reply_retry = self.comment_service.reply_to_comment(
                        account_token=candidate_record.token,
                        owner_id=result.owner_id,
                        post_id=result.post_id,
                        reply_to_comment=result.comment_id,
                        text=reply_text,
                    )
                    result.logs.append(reply_retry.message)
                    used_tokens.add(candidate_record.token)
                    if not reply_retry.success:
                        continue
                    if reply_retry.data and isinstance(reply_retry.data.get("comment_id"), int):
                        result.reply_id = int(reply_retry.data["comment_id"])
                    replier_record = candidate_record
                    recovery_done = True
                    break
                if recovery_done:
                    like_retry_success = False
                    used_like_tokens = {liker_record.token, commentator_record.token}
                    if require_reply:
                        used_like_tokens.add(replier_record.token)
                    for candidate_record, candidate_role in self._collect_records_for_group_with_fallback(
                        "liker",
                        group_ref,
                        exclude_tokens=used_like_tokens,
                        require_likes_available=True,
                    ):
                        if candidate_role != "liker":
                            result.logs.append(
                                f"[{group_ref}] подмена роли (recovery): {ROLE_VALUE_TO_LABEL.get(candidate_role, candidate_role)} -> Лайкер"
                            )
                        like_retry = self.comment_service.add_like_to_comment(
                            account_token=candidate_record.token,
                            owner_id=result.owner_id,
                            comment_id=result.comment_id,
                            post_id=result.post_id,
                        )
                        result.logs.append(like_retry.message)
                        used_like_tokens.add(candidate_record.token)
                        if like_retry.success:
                            liker_record = candidate_record
                            like_retry_success = True
                            break
                    result.success = like_retry_success
                    result.message = "Сценарий выполнен" if like_retry_success else "Сценарий выполнен частично (без лайка)"
                    if like_retry_success:
                        result.logs.append("Сценарий завершен успешно")
            if (
                not result.success
                and not result.requires_confirmation
                and result.comment_id is not None
                and result.owner_id is not None
                and self._has_like_account_error(result.logs)
            ):
                like_retry_success = False
                used_like_tokens = {liker_record.token, commentator_record.token}
                if require_reply:
                    used_like_tokens.add(replier_record.token)
                for candidate_record, candidate_role in self._collect_records_for_group_with_fallback(
                    "liker",
                    group_ref,
                    exclude_tokens=used_like_tokens,
                    require_likes_available=True,
                ):
                    if candidate_role != "liker":
                        result.logs.append(
                            f"[{group_ref}] подмена роли (recovery): {ROLE_VALUE_TO_LABEL.get(candidate_role, candidate_role)} -> Лайкер"
                        )
                    like_retry = self.comment_service.add_like_to_comment(
                        account_token=candidate_record.token,
                        owner_id=result.owner_id,
                        comment_id=result.comment_id,
                        post_id=result.post_id,
                    )
                    result.logs.append(like_retry.message)
                    used_like_tokens.add(candidate_record.token)
                    if like_retry.success:
                        liker_record = candidate_record
                        like_retry_success = True
                        break
                if like_retry_success:
                    result.success = True
                    result.message = "Сценарий выполнен"
                    result.logs.append("Сценарий завершен успешно")
            logs.extend(result.logs)
            last_result = result
            self._register_monitoring_task(
                group_ref=group_ref,
                result=result,
                comment_text=comment_text,
                reply_text=reply_text,
                require_reply=require_reply,
                prefer_pinned=prefer_pinned,
                commentator_token=commentator_record.token,
                replier_token=replier_record.token,
                liker_token=liker_record.token,
            )
            if result.requires_confirmation:
                break

        if last_result is None:
            self._set_action_result("\n".join(logs) if logs else "Нет групп для запуска")
            self.statusBar().showMessage("Автосценарий не запущен")
            self.continue_scenario_button.setEnabled(False)
            return

        merged_result = ScenarioRunResult(
            success=last_result.success and not last_result.requires_confirmation,
            message=last_result.message,
            logs=logs,
            requires_confirmation=last_result.requires_confirmation,
            redirect_uri=last_result.redirect_uri,
            comment_id=last_result.comment_id,
            post_id=last_result.post_id if len(selected_groups) == 1 else None,
        )
        self._apply_scenario_result(merged_result)

    def _continue_auto_scenario(self) -> None:
        result = self.automation_service.continue_pending_scenario()
        self._apply_scenario_result(result)

    def _register_monitoring_task(
        self,
        group_ref: str,
        result: ScenarioRunResult,
        comment_text: str,
        reply_text: str,
        require_reply: bool,
        prefer_pinned: bool,
        commentator_token: str,
        replier_token: str,
        liker_token: str | None,
    ) -> None:
        if result.comment_id is None or result.post_id is None or result.owner_id is None:
            return
        pool = next((p for p in self.group_pools if p.group_id == group_ref), None)
        interval_sec = pool.monitoring_interval_seconds if pool else 30
        # Новые посты: стратегия "только последний" ИЛИ закреп не нашёлся и взяли крайний.
        track_new_posts = (not prefer_pinned) or any(
            "Закрепленный пост не найден" in line for line in result.logs
        )
        self.monitoring_service.register_task(
            MonitoredTask(
                group_ref=group_ref,
                owner_id=result.owner_id,
                post_id=result.post_id,
                comment_text=comment_text,
                reply_text=reply_text,
                require_reply=require_reply,
                prefer_pinned=prefer_pinned,
                commentator_token=commentator_token,
                replier_token=replier_token,
                liker_token=liker_token,
                comment_id=result.comment_id,
                reply_id=result.reply_id if require_reply else None,
                interval_seconds=interval_sec,
                track_new_posts=track_new_posts,
                photo_path=self._selected_comment_photo_path(),
            )
        )

    def _start_monitoring(self) -> None:
        if not self.monitoring_service.has_tasks():
            self._set_action_result("Нет задач мониторинга. Сначала запустите авто-сценарий.")
            self.statusBar().showMessage("Мониторинг не запущен: нет задач")
            return
        self.monitoring_countdown_seconds = self.monitoring_service.seconds_until_next_check()
        self.monitoring_timer.start()
        self.monitoring_countdown_timer.start()
        self._refresh_monitoring_status_label()
        self._set_action_result("Мониторинг запущен")
        self.statusBar().showMessage("Мониторинг запущен")

    def _stop_monitoring(self) -> None:
        self.monitoring_timer.stop()
        self.monitoring_countdown_timer.stop()
        self.monitoring_status_label.setText("Мониторинг: выключен")
        self._set_action_result("Мониторинг остановлен")
        self.statusBar().showMessage("Мониторинг остановлен")

    def _update_monitoring_countdown(self) -> None:
        if not self.monitoring_timer.isActive():
            return
        self.monitoring_countdown_seconds = self.monitoring_service.seconds_until_next_check()
        self._refresh_monitoring_status_label()

    def _refresh_monitoring_status_label(self) -> None:
        self.monitoring_status_label.setText(
            f"Мониторинг: включен, ближайшая проверка через ~{self.monitoring_countdown_seconds} сек"
        )

    def _run_monitoring_tick(self) -> None:
        if not self.monitoring_service.has_tasks():
            self._stop_monitoring()
            return

        self.monitoring_countdown_seconds = self.monitoring_service.seconds_until_next_check()
        self._refresh_monitoring_status_label()
        result = self.monitoring_service.run_tick(self._select_monitoring_accounts)
        self.monitoring_countdown_seconds = self.monitoring_service.seconds_until_next_check()
        self._refresh_monitoring_status_label()
        if result.logs:
            self._set_action_result("\n".join(result.logs))
        if result.exhausted_messages:
            QMessageBox.warning(
                self,
                "Мониторинг",
                "Недостаточно аккаунтов для восстановления:\n" + "\n".join(result.exhausted_messages),
            )

    def _select_monitoring_accounts(
        self,
        role: str,
        group_ref: str,
        disabled_accounts: set[tuple[str, str]],
    ) -> list[TokenRecord]:
        records: list[TokenRecord] = []
        for candidate_role in ROLE_FALLBACK_ORDER.get(role, (role,)):
            records.extend(
                self._find_records_for_group_filtered(
                    candidate_role,
                    group_ref,
                    disabled_accounts,
                    require_likes_available=(role == "liker"),
                )
            )
        return records

    def _apply_scenario_result(self, result: ScenarioRunResult) -> None:
        self._set_action_result("\n".join(result.logs))
        if result.comment_id is not None:
            self.comment_id_input.setText(str(result.comment_id))
        if result.post_id is not None:
            self.post_id_input.setText(str(result.post_id))
        self.continue_scenario_button.setEnabled(result.requires_confirmation and self.automation_service.has_pending_scenario())
        if result.requires_confirmation and result.redirect_uri:
            self.statusBar().showMessage("Нужно подтверждение в браузере, затем нажмите 'Продолжить после подтверждения'")
        else:
            self.statusBar().showMessage("Автосценарий выполнен" if result.success else "Автосценарий завершился с ошибкой")

    def _set_action_result(self, text: str) -> None:
        normalized_text = text.strip()
        if not normalized_text:
            return
        now = datetime.now()
        timestamp = now.strftime("%H:%M:%S")
        current_minute = now.strftime("%H:%M")
        lines = [line for line in normalized_text.splitlines() if line.strip()]
        formatted = "\n".join(f"[{timestamp}] {line}" for line in lines)
        current_text = self.actions_result_output.toPlainText().strip()
        if not current_text or current_text == "-":
            self.actions_result_output.setPlainText(formatted)
        else:
            if self.last_log_minute != current_minute:
                self.actions_result_output.appendPlainText(f"\n----- Новые события {current_minute} -----")
            self.actions_result_output.appendPlainText(formatted)
        self.last_log_minute = current_minute

    def _copy_action_result(self) -> None:
        text = self.actions_result_output.toPlainText().strip()
        if not text or text == "-":
            self.statusBar().showMessage("Нет текста для копирования")
            return
        self.actions_result_output.selectAll()
        self.actions_result_output.copy()
        self.statusBar().showMessage("Текст результата скопирован")
