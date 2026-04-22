"""Главное окно десктоп-приложения VK."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

from app.services.comment_service import CommentService
from app.services.token_service import TokenRecord, TokenService
from app.storage.repositories import GroupCommentsPool, PoolsRepository


class MainWindow(QMainWindow):
    """Основная оболочка приложения с временными вкладками."""

    def __init__(self) -> None:
        super().__init__()
        self.token_service = TokenService()
        self.comment_service = CommentService()
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

        refresh_button = QPushButton("Обновить список")
        refresh_button.clicked.connect(self._refresh_tokens_table)
        button_row.addWidget(refresh_button)
        button_row.addStretch(1)

        self.tokens_file_label = QLabel("")
        self.tokens_table = QTableWidget()
        self.tokens_table.setColumnCount(3)
        self.tokens_table.setHorizontalHeaderLabels(["Токен", "Статус", "Комментарий"])
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

    def _refresh_tokens_table(self) -> None:
        if not self.tokens_file_path.exists():
            self.token_records = []
            self.tokens_file_label.setText(f"Файл токенов: {self.tokens_file_path} (не найден)")
            self.tokens_table.setRowCount(0)
            self._refresh_actions_selectors()
            self.statusBar().showMessage("Файл access_tokens.txt не найден")
            return

        self.token_records = self.token_service.parse_txt(self.tokens_file_path)
        self.tokens_file_label.setText(f"Файл токенов: {self.tokens_file_path}")
        self._fill_tokens_table()
        self._refresh_actions_selectors()
        self.statusBar().showMessage(f"Загружено токенов: {len(self.token_records)}")

    def _fill_tokens_table(self) -> None:
        self.tokens_table.setRowCount(len(self.token_records))
        for row_index, record in enumerate(self.token_records):
            self.tokens_table.setItem(row_index, 0, QTableWidgetItem(self._mask_token(record.token)))

            if record.last_error:
                status_text = "Ошибка"
            elif record.is_valid:
                status_text = "Валидный"
            else:
                status_text = "Не проверен"
            self.tokens_table.setItem(row_index, 1, QTableWidgetItem(status_text))
            comment_text = record.last_error or (record.owner_name or "")
            self.tokens_table.setItem(row_index, 2, QTableWidgetItem(comment_text))

    @staticmethod
    def _mask_token(token: str) -> str:
        if len(token) <= 10:
            return "*" * len(token)
        return f"{token[:6]}...{token[-4:]}"

    def _build_pools_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)

        hint = QLabel("Комментарии в колонке указывайте через || (например: Привет||Отличный пост)")
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
        self.pools_table.setColumnCount(2)
        self.pools_table.setHorizontalHeaderLabels(["ID/ссылка группы", "Комментарии (через ||)"])
        self.pools_table.verticalHeader().setVisible(False)
        self.pools_table.horizontalHeader().setStretchLastSection(True)

        layout.addLayout(button_row)
        layout.addWidget(self.pools_table)

        self._load_pools_table()
        return page

    def _add_pool_row(self) -> None:
        row_index = self.pools_table.rowCount()
        self.pools_table.insertRow(row_index)
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
            self.pools_table.setItem(row_index, 0, QTableWidgetItem(pool.group_id))
            self.pools_table.setItem(row_index, 1, QTableWidgetItem(comments_text))

        self._refresh_actions_selectors()
        self.statusBar().showMessage(f"Загружено пулов: {len(pools)}")

    def _save_pools_table(self) -> None:
        pools: list[GroupCommentsPool] = []
        for row_index in range(self.pools_table.rowCount()):
            group_item = self.pools_table.item(row_index, 0)
            comments_item = self.pools_table.item(row_index, 1)

            group_id = group_item.text().strip() if group_item else ""
            comments_raw = comments_item.text().strip() if comments_item else ""
            comments = [item.strip() for item in comments_raw.split("||") if item.strip()]

            if not group_id:
                continue
            pools.append(GroupCommentsPool(group_id=group_id, comments=comments))

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

        token_label = QLabel("Аккаунт (токен):")
        self.actions_token_combo = QComboBox()
        layout.addWidget(token_label)
        layout.addWidget(self.actions_token_combo)

        group_label = QLabel("Группа (из пула):")
        self.actions_group_combo = QComboBox()
        self.actions_group_combo.currentIndexChanged.connect(self._fill_comment_from_selected_group)
        layout.addWidget(group_label)
        layout.addWidget(self.actions_group_combo)

        post_label = QLabel("ID поста:")
        self.post_id_input = QLineEdit()
        self.post_id_input.setPlaceholderText("Например: 123")
        layout.addWidget(post_label)
        layout.addWidget(self.post_id_input)

        comment_label = QLabel("Текст комментария:")
        self.comment_input = QPlainTextEdit()
        self.comment_input.setPlaceholderText("Введите текст комментария")
        self.comment_input.setFixedHeight(120)
        layout.addWidget(comment_label)
        layout.addWidget(self.comment_input)

        button_row = QHBoxLayout()
        comment_button = QPushButton("Оставить комментарий")
        comment_button.clicked.connect(self._send_comment_action)
        button_row.addWidget(comment_button)

        like_button = QPushButton("Поставить лайк")
        like_button.clicked.connect(self._send_like_action)
        button_row.addWidget(like_button)
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
        if not hasattr(self, "actions_token_combo") or not hasattr(self, "actions_group_combo"):
            return

        self.actions_token_combo.clear()
        for record in self.token_records:
            token_name = record.owner_name or "Аккаунт без имени"
            self.actions_token_combo.addItem(f"{token_name} ({self._mask_token(record.token)})", record.token)

        self.actions_group_combo.clear()
        for pool in self.group_pools:
            self.actions_group_combo.addItem(pool.group_id, pool.group_id)

        self._fill_comment_from_selected_group()

    def _fill_comment_from_selected_group(self) -> None:
        if not hasattr(self, "actions_group_combo") or not hasattr(self, "comment_input"):
            return
        group_ref = self.actions_group_combo.currentData()
        if not isinstance(group_ref, str):
            return
        for pool in self.group_pools:
            if pool.group_id == group_ref and pool.comments:
                self.comment_input.setPlainText(pool.comments[0])
                return
        self.comment_input.clear()

    def _resolve_action_context(self) -> tuple[str | None, str | None, int | None]:
        token = self.actions_token_combo.currentData()
        group_ref = self.actions_group_combo.currentData()
        post_id_raw = self.post_id_input.text().strip()

        if not isinstance(token, str) or not token:
            self._set_action_result("выберите аккаунт")
            return None, None, None
        if not isinstance(group_ref, str) or not group_ref:
            self._set_action_result("выберите группу")
            return None, None, None
        if not post_id_raw.isdigit():
            self._set_action_result("укажите корректный ID поста (число)")
            return None, None, None

        return token, group_ref, int(post_id_raw)

    def _send_comment_action(self) -> None:
        token, group_ref, post_id = self._resolve_action_context()
        if token is None or group_ref is None or post_id is None:
            return

        comment_text = self.comment_input.toPlainText().strip()
        if not comment_text:
            self._set_action_result("введите текст комментария")
            return

        ok, owner_id, message = self.comment_service.resolve_owner_id(token, group_ref)
        if not ok or owner_id is None:
            self._set_action_result(f"ошибка owner_id: {message}")
            return

        result = self.comment_service.post_comment(
            account_token=token,
            owner_id=owner_id,
            post_id=post_id,
            text=comment_text,
        )
        self._set_action_result(result.message)
        self.statusBar().showMessage("Комментарий отправлен" if result.success else "Ошибка отправки комментария")

    def _send_like_action(self) -> None:
        token, group_ref, post_id = self._resolve_action_context()
        if token is None or group_ref is None or post_id is None:
            return

        ok, owner_id, message = self.comment_service.resolve_owner_id(token, group_ref)
        if not ok or owner_id is None:
            self._set_action_result(f"ошибка owner_id: {message}")
            return

        result = self.comment_service.add_like(
            account_token=token,
            owner_id=owner_id,
            post_id=post_id,
        )
        self._set_action_result(result.message)
        self.statusBar().showMessage("Лайк поставлен" if result.success else "Ошибка постановки лайка")

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
