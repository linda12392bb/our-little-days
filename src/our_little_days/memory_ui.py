"""Local memory management and confirmed restore; no model requests."""
from PySide6.QtWidgets import QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget


class MemoryDialog(QDialog):
    def __init__(self, store, parent):
        super().__init__(parent)
        self.store = store
        self.restored = False
        self.setWindowTitle("公寓的記憶 · 本機永久保存")
        self.resize(720, 630)
        layout = QVBoxLayout(self)
        path = QLabel(f"資料庫：{store.path}")
        path.setWordWrap(True)
        layout.addWidget(path)
        self.world_combo = QComboBox()
        for row in store.connection.execute("SELECT id,label FROM worldlines ORDER BY id"):
            self.world_combo.addItem(row["label"], row["id"])
        self.world_combo.setCurrentIndex(self.world_combo.findData(parent.worldline))
        self.world_combo.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.world_combo)
        layout.addWidget(QLabel("各世界線分開保存。AI 畫面事件為未確認觀察；長期記憶仍由你手動確認保存。"))
        tabs = QTabWidget()
        layout.addWidget(tabs, 1)
        self.event_list = QPlainTextEdit()
        self.event_list.setReadOnly(True)
        self.event_input = QPlainTextEdit()
        self.event_input.setPlaceholderText("記下重要事件（只存文字，不存截圖）")
        self.event_input.setMaximumHeight(90)
        events = QWidget()
        event_layout = QVBoxLayout(events)
        event_layout.addWidget(self.event_list, 1)
        event_layout.addWidget(self.event_input)
        event_add = QPushButton("保存重要事件")
        event_add.clicked.connect(self.save_event)
        event_layout.addWidget(event_add)
        tabs.addTab(events, "重要事件")
        self.memory_list = QPlainTextEdit()
        self.memory_list.setReadOnly(True)
        self.memory_input = QPlainTextEdit()
        self.memory_input.setPlaceholderText("記下長期記憶；與聊天紀錄分開儲存")
        self.memory_input.setMaximumHeight(90)
        memories = QWidget()
        memory_layout = QVBoxLayout(memories)
        memory_layout.addWidget(self.memory_list, 1)
        self.character_combo = QComboBox()
        self.character_combo.addItem("公寓共同記憶", None)
        for character in store.load_characters():
            self.character_combo.addItem(f"{character.icon} {character.name}", character.id)
        memory_layout.addWidget(self.character_combo)
        memory_layout.addWidget(self.memory_input)
        memory_add = QPushButton("保存長期記憶")
        memory_add.clicked.connect(self.save_memory)
        memory_layout.addWidget(memory_add)
        tabs.addTab(memories, "長期記憶")
        buttons = QHBoxLayout()
        backup = QPushButton("備份整間公寓…")
        backup.clicked.connect(self.backup)
        restore = QPushButton("還原備份…")
        restore.clicked.connect(self.restore)
        close = QPushButton("關閉")
        close.clicked.connect(self.accept)
        for widget in (backup, restore, close):
            buttons.addWidget(widget)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self, *_):
        if not hasattr(self, "memory_list"):
            return
        world = self.world_combo.currentData()
        self.event_list.setPlainText("\n\n".join(f"#{row['id']} · {row['created_at']}\n{row['summary']}" for row in self.store.events(world)) or "這條世界線還沒有已記錄的重要事件。")
        self.memory_list.setPlainText("\n\n".join(f"#{row['id']} · {row['character_id'] or '公寓共同'} · {row['updated_at']}\n{row['content']}" for row in self.store.memories(world)) or "這條世界線還沒有長期記憶。")

    def save_event(self):
        try:
            self.store.add_event(self.event_input.toPlainText(), self.world_combo.currentData())
            self.event_input.clear()
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "事件未儲存", str(exc))

    def save_memory(self):
        try:
            self.store.add_memory(self.memory_input.toPlainText(), self.world_combo.currentData(), self.character_combo.currentData())
            self.memory_input.clear()
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, "記憶未儲存", str(exc))

    def backup(self):
        destination, _ = QFileDialog.getSaveFileName(self, "備份整間公寓（請選擇新檔名）", "公寓備份.sqlite3", "SQLite backup (*.sqlite3)")
        if not destination:
            return
        try:
            result = self.store.backup(destination)
            QMessageBox.information(self, "備份完成", f"角色、關係、聊天、事件與記憶均已備份：\n{result}")
        except Exception as exc:
            QMessageBox.warning(self, "備份失敗", str(exc))

    def restore(self):
        source, _ = QFileDialog.getOpenFileName(self, "選擇公寓備份", "", "SQLite backup (*.sqlite3)")
        if not source:
            return
        try:
            self.store.validate_backup(source)
            answer = QMessageBox.question(self, "確認還原整間公寓？", "這會以備份替換目前角色、關係、聊天、事件與記憶。\n程式會先備份目前資料到資料目錄的 backups 子資料夾。\n是否繼續？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            if self.parent().voice:
                self.parent().voice.stop()
            self.parent().stop_monitoring()
            safety = self.store.restore(source, confirmed=True)
            self.restored = True
            QMessageBox.information(self, "還原完成", f"資料已還原。還原前資料另存於：\n{safety}")
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "還原失敗，請保留資料檔", str(exc))
