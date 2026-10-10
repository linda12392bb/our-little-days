from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QFileDialog

from our_little_days.capture import DemoBackend
from our_little_days.memory_ui import MemoryDialog
from our_little_days.storage import SQLiteStore
from our_little_days.ui import MainWindow


def test_window_close_reopen_restores_history_without_duplicate_welcome(app, store):
    first = MainWindow(DemoBackend(), store.load_characters(), store)
    first.show()
    first.chat_input.setPlainText("重新開啟後仍在")
    first.send_message()
    first.character_combo.setCurrentIndex(11)
    first.add_test_message()
    original = store.messages()
    first.close()
    app.processEvents()
    path = store.path
    store.close()
    reopened = SQLiteStore(path)
    second = MainWindow(DemoBackend(), reopened.load_characters(), reopened)
    try:
        assert len(second.cards) == len(original) == 4
        assert reopened.messages() == original
        assert second.roster_labels["shogo"].text() == "○ 已回覆"
        assert second.frame_image is None
        assert not second.timer.isActive()
    finally:
        second.close()
        app.processEvents()
        reopened.close()


def test_cancel_clear_keeps_history_relationships_and_memories(app, store, monkeypatch):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    store.add_memory("永遠保留的測試記憶")
    count = len(window.cards)
    before = store.load_characters()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    try:
        window.clear_chat()
        assert len(window.cards) == len(store.messages()) == count
        window.pause_monitoring()
        window.stop_monitoring()
        assert store.memories()[0]["content"] == "永遠保留的測試記憶"
        assert store.load_characters() == before
    finally:
        window.close()


def test_storage_failure_does_not_erase_user_input(app, store, monkeypatch):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    initial = len(window.cards)
    def fail(**kwargs):
        raise OSError("磁碟不可寫入")
    monkeypatch.setattr(store, "append_message", fail)
    try:
        window.chat_input.setPlainText("請保留這段文字")
        window.send_message()
        assert window.chat_input.toPlainText() == "請保留這段文字"
        assert len(window.cards) == initial
        assert "訊息未儲存" in window.error_label.text()
    finally:
        window.close()


def test_memory_dialog_saves_manual_entries_with_worldline_scope(app, store):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    dialog = MemoryDialog(store, window)
    try:
        dialog.event_input.setPlainText("已確認的手動事件")
        dialog.save_event()
        dialog.character_combo.setCurrentIndex(dialog.character_combo.findData("shogo"))
        dialog.memory_input.setPlainText("已確認的手動記憶")
        dialog.save_memory()
        assert store.events()[0]["summary"] == "已確認的手動事件"
        assert store.memories()[0]["character_id"] == "shogo"
        dialog.world_combo.setCurrentIndex(dialog.world_combo.findData("glasshouse"))
        assert "還沒有長期記憶" in dialog.memory_list.toPlainText()
        dialog.memory_input.setPlainText("AU 手動記憶")
        dialog.save_memory()
        assert len(store.memories()) == len(store.memories("glasshouse")) == 1
    finally:
        dialog.close()
        window.close()


def test_confirmed_restore_reloads_visible_history_and_stops_capture(app, store, tmp_path, monkeypatch):
    store.append_message(speaker_id="user", speaker_name="月鹿", text="備份內的訊息")
    snapshot = store.backup(tmp_path / "restore-ui.sqlite3")
    store.append_message(speaker_id="user", speaker_name="月鹿", text="備份之後的訊息")
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(snapshot), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(MemoryDialog, "exec", lambda dialog: dialog.restore())
    try:
        window.window_combo.setCurrentIndex(1)
        window.start_monitoring()
        window.manage_memory()
        assert len(window.cards) == 1
        assert store.messages()[0]["text"] == "備份內的訊息"
        assert not window.timer.isActive()
        assert window.frame_image is None
    finally:
        window.close()
        app.processEvents()
