"""Главное окно десктоп-приложения VK."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
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
from app.services.token_service import TokenRecord, TokenService
from app.storage.repositories import GroupCommentsPool, PoolsRepository

ROLE_LABEL_TO_VALUE = {
    "Лайкер": "liker",
    "Комментатор": "commentator",
    "Ответчик": "replier",
}
ROLE_VALUE_TO_LABEL = {value: label for label, value in ROLE_LABEL_TO_VALUE.items()}
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
        self.pools_repository = PoolsRepository()
        self.token_records: list[TokenRecord] = []
        self.group_pools: list[GroupCommentsPool] = []
        self.tokens_file_path = Path.cwd() / "access_tokens.txt"
        self.setWindowTitle("VK Помощник")
        self.resize(1100, 760)
        self._setup_layout()
        self._refresh_tokens_table()

    def _setup_layout(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(self._build_accounts_tab(), "Аккаунты")
        tabs.addTab(self._build_pools_tab(), "Пулы")
        tabs.addTab(self._create_placeholder("Лента мониторинга комментариев"), "Мониторинг")
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

        delete_button = QPushButton("Удалить токен")
        delete_button.clicked.connect(self._delete_selected_token)
        button_row.addWidget(delete_button)

        refresh_button = QPushButton("Обновить список")
        refresh_button.clicked.connect(self._refresh_tokens_table)
        button_row.addWidget(refresh_button)
        button_row.addStretch(1)

        self.tokens_file_label = QLabel("")
        self.tokens_table = QTableWidget()
        self.tokens_table.setColumnCount(4)
        self.tokens_table.setHorizontalHeaderLabels(["Токен", "Роль", "Статус", "Комментарий"])
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
            str(Path.cwd()),
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
        self.statusBar().showMessage(
            f"Проверка завершена: валидных {valid_count} из {len(self.token_records)}"
        )

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
        self.token_records = self.token_service.parse_txt(self.tokens_file_path)
        for record in self.token_records:
            record.role = roles_by_token.get(record.token, "liker")
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

            if record.last_error:
                status_text = "Ошибка"
            elif record.is_valid:
                status_text = "Валидный"
            else:
                status_text = "Не проверен"
            self.tokens_table.setItem(row_index, 2, QTableWidgetItem(status_text))
            comment_text = record.last_error or (record.owner_name or "")
            self.tokens_table.setItem(row_index, 3, QTableWidgetItem(comment_text))

    def _update_token_role(self, record: TokenRecord, role_combo: QComboBox) -> None:
        role = role_combo.currentData()
        if not isinstance(role, str):
            return
        record.role = role
        self._refresh_actions_selectors()

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
            "Комментарии/ответы через ||. Для колонки 'Пост' используйте выпадающий список."
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
        self.pools_table.setColumnCount(4)
        self.pools_table.setHorizontalHeaderLabels(
            ["ID/ссылка группы", "Комментарии (через ||)", "Ответы (через ||)", "Пост"]
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
        self.statusBar().showMessage("Строка группы добавлена")

    def _delete_pool_row(self) -> None:
        current_row = self.pools_table.currentRow()
        if current_row < 0:
            self.statusBar().showMessage("Выберите строку для удаления")
            return
        self.pools_table.removeRow(current_row)
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
        self.actions_group_list.itemSelectionChanged.connect(self._fill_comment_from_selected_group)
        layout.addWidget(group_label)
        layout.addWidget(self.actions_group_list)

        post_label = QLabel("ID поста:")
        self.post_id_input = QLineEdit()
        self.post_id_input.setPlaceholderText("Например: 123")
        layout.addWidget(post_label)
        layout.addWidget(self.post_id_input)

        comment_id_label = QLabel("ID комментария (для лайка/ответа):")
        self.comment_id_input = QLineEdit()
        self.comment_id_input.setPlaceholderText("Например: 456")
        layout.addWidget(comment_id_label)
        layout.addWidget(self.comment_id_input)

        comment_label = QLabel("Текст комментария:")
        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText("Введите текст комментария")
        self.comment_input.setFixedHeight(90)
        layout.addWidget(comment_label)
        layout.addWidget(self.comment_input)

        reply_label = QLabel("Текст ответа:")
        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("Введите текст ответа")
        self.reply_input.setFixedHeight(90)
        layout.addWidget(reply_label)
        layout.addWidget(self.reply_input)

        delay_label = QLabel("Пауза между шагами (сек):")
        self.delay_input = QLineEdit()
        self.delay_input.setPlaceholderText("Например: 1.5")
        self.delay_input.setText("1.5")
        layout.addWidget(delay_label)
        layout.addWidget(self.delay_input)

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

        scenario_button = QPushButton("Авто: комментарий -> лайк -> ответ")
        scenario_button.clicked.connect(self._run_auto_scenario)
        button_row.addWidget(scenario_button)

        self.continue_scenario_button = QPushButton("Продолжить после подтверждения")
        self.continue_scenario_button.setEnabled(False)
        self.continue_scenario_button.clicked.connect(self._continue_auto_scenario)
        button_row.addWidget(self.continue_scenario_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        result_label = QLabel("Результат:")
        self.actions_result_output = QPlainTextEdit()
        self.actions_result_output.setReadOnly(True)
        self.actions_result_output.setFixedHeight(90)
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
            if pool.group_id in selected_before:
                item.setSelected(True)

        self._refresh_roles_info()
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
        record = self._find_record_by_role(role)
        if record is None:
            return "не назначен"
        token_name = record.owner_name or "Аккаунт без имени"
        return f"{token_name} ({self._mask_token(record.token)})"

    def _find_record_by_role(self, role: str) -> TokenRecord | None:
        for record in self.token_records:
            if record.role == role:
                return record
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
            if group_id:
                pools.append(
                    GroupCommentsPool(
                        group_id=group_id,
                        comments=comments,
                        replies=replies,
                        post_strategy=strategy_value,
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

    def _resolve_base_context(self, require_post_id: bool = True) -> tuple[str | None, int | None]:
        selected_groups = self._selected_group_refs()
        group_ref = selected_groups[0] if selected_groups else None
        post_id_raw = self.post_id_input.text().strip()

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
        record = self._find_record_by_role("commentator")
        group_ref, post_id = self._resolve_base_context()
        if record is None:
            self._set_action_result("назначьте роль Комментатор на вкладке Аккаунты")
            return
        if group_ref is None or post_id is None:
            return

        comment_text = self.comment_input.toPlainText().strip()
        if not comment_text:
            self._set_action_result("введите текст комментария")
            return

        ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
        if not ok or owner_id is None:
            self._set_action_result(f"ошибка owner_id: {message}")
            return

        result = self.comment_service.post_comment(
            account_token=record.token,
            owner_id=owner_id,
            post_id=post_id,
            text=comment_text,
        )
        self._set_action_result(result.message)
        self.statusBar().showMessage("Комментарий отправлен" if result.success else "Ошибка отправки комментария")

    def _send_comment_like_by_liker(self) -> None:
        record = self._find_record_by_role("liker")
        group_ref, _post_id = self._resolve_base_context()
        if record is None:
            self._set_action_result("назначьте роль Лайкер на вкладке Аккаунты")
            return
        if group_ref is None:
            return

        comment_id_raw = self.comment_id_input.text().strip()
        if not comment_id_raw.isdigit():
            self._set_action_result("укажите корректный ID комментария (число)")
            return
        comment_id = int(comment_id_raw)

        ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
        if not ok or owner_id is None:
            self._set_action_result(f"ошибка owner_id: {message}")
            return

        result = self.comment_service.add_like_to_comment(
            account_token=record.token,
            owner_id=owner_id,
            comment_id=comment_id,
        )
        self._set_action_result(result.message)
        self.statusBar().showMessage("Лайк комментария поставлен" if result.success else "Ошибка постановки лайка")

    def _send_reply_by_replier(self) -> None:
        record = self._find_record_by_role("replier")
        group_ref, post_id = self._resolve_base_context()
        if record is None:
            self._set_action_result("назначьте роль Ответчик на вкладке Аккаунты")
            return
        if group_ref is None or post_id is None:
            return

        comment_id_raw = self.comment_id_input.text().strip()
        if not comment_id_raw.isdigit():
            self._set_action_result("укажите корректный ID комментария (число)")
            return
        comment_id = int(comment_id_raw)

        reply_text = self.reply_input.toPlainText().strip()
        if not reply_text:
            self._set_action_result("введите текст ответа")
            return

        ok, owner_id, message = self.comment_service.resolve_owner_id(record.token, group_ref)
        if not ok or owner_id is None:
            self._set_action_result(f"ошибка owner_id: {message}")
            return

        result = self.comment_service.reply_to_comment(
            account_token=record.token,
            owner_id=owner_id,
            post_id=post_id,
            reply_to_comment=comment_id,
            text=reply_text,
        )
        self._set_action_result(result.message)
        self.statusBar().showMessage("Ответ отправлен" if result.success else "Ошибка отправки ответа")

    def _run_auto_scenario(self) -> None:
        commentator_record = self._find_record_by_role("commentator")
        liker_record = self._find_record_by_role("liker")
        replier_record = self._find_record_by_role("replier")
        selected_groups = self._selected_group_refs()
        _group_ref, post_id = self._resolve_base_context(require_post_id=False)

        if commentator_record is None:
            self._set_action_result("назначьте роль Комментатор на вкладке Аккаунты")
            return
        if liker_record is None:
            self._set_action_result("назначьте роль Лайкер на вкладке Аккаунты")
            return
        if replier_record is None:
            self._set_action_result("назначьте роль Ответчик на вкладке Аккаунты")
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
            reply_text = (
                pool.replies[0]
                if pool and pool.replies
                else (pool.comments[0] if pool and pool.comments else global_reply_text)
            )
            prefer_pinned = True if not pool else pool.post_strategy != "latest"

            if not comment_text:
                logs.append(f"[{group_ref}] пропуск: пустой комментарий")
                continue
            if not reply_text:
                logs.append(f"[{group_ref}] пропуск: пустой ответ")
                continue

            logs.append(f"=== Группа {group_ref} ===")
            result = self.automation_service.run_comment_like_reply_scenario(
                commentator_token=commentator_record.token,
                liker_token=liker_record.token,
                replier_token=replier_record.token,
                group_ref=group_ref,
                post_id=post_id,
                comment_text=comment_text,
                reply_text=reply_text,
                prefer_pinned=prefer_pinned,
                delay_seconds=delay_seconds,
            )
            logs.extend(result.logs)
            last_result = result
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
            post_id=last_result.post_id,
        )
        self._apply_scenario_result(merged_result)

    def _continue_auto_scenario(self) -> None:
        result = self.automation_service.continue_pending_scenario()
        self._apply_scenario_result(result)

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
        self.actions_result_output.setPlainText(text)

    def _copy_action_result(self) -> None:
        text = self.actions_result_output.toPlainText().strip()
        if not text or text == "-":
            self.statusBar().showMessage("Нет текста для копирования")
            return
        self.actions_result_output.selectAll()
        self.actions_result_output.copy()
        self.statusBar().showMessage("Текст результата скопирован")
