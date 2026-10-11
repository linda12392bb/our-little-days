import time
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

from our_little_days.capture import CaptureError, DemoBackend, UnsupportedBackend
from our_little_days.models import Frame, MonitorState, WindowInfo, load_characters
from our_little_days.ui import MainWindow


def wait_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), "Timed out waiting for expected UI behavior"


@pytest.fixture
def window(app, store):
    instance = MainWindow(DemoBackend(), store.load_characters(), store)
    instance.show()
    QTest.qWait(30)
    yield instance
    instance.close()
    app.processEvents()


def choose_and_start(window):
    window.window_combo.setCurrentIndex(1)
    QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    wait_until(lambda: window.frame_image is not None)
    wait_until(lambda: window.worker is None)


def test_capture_lifecycle_and_selection_lock(window):
    assert not window.start_button.isEnabled()
    assert window.frame_image is None
    choose_and_start(window)
    assert window.state == MonitorState.RUNNING
    assert not window.window_combo.isEnabled()
    assert not window.refresh_button.isEnabled()
    target = window.target
    window.refresh_windows()
    assert window.target is target
    assert window.frame_image.width() == 640
    QTest.mouseClick(window.pause_button, Qt.MouseButton.LeftButton)
    assert window.state == MonitorState.PAUSED
    assert window.frame_image is None
    assert not window.timer.isActive()
    count = window.backend.counter
    window.request_capture()
    assert window.backend.counter == count
    assert window.start_button.text() == "▷ 恢復陪伴"
    QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
    wait_until(lambda: window.frame_image is not None)
    QTest.mouseClick(window.stop_button, Qt.MouseButton.LeftButton)
    assert window.state == MonitorState.STOPPED
    assert window.window_combo.isEnabled()
    assert window.frame_image is None
    assert not window.timer.isActive()


def test_late_frame_does_not_reappear_after_pause(window):
    choose_and_start(window)
    previous_generation = window.generation
    window.pause_monitoring()
    frame = Frame(1, 1, bytes((10, 20, 30, 255)))
    window.receive_frame(frame, previous_generation)
    assert window.frame_image is None
    assert window.state == MonitorState.PAUSED
    window.start_monitoring()
    window.receive_frame(frame, previous_generation)
    assert window.frame_image is None


def test_sensitive_pause_cancels_inflight_capture(window):
    class Slow(DemoBackend):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.cancelled = threading.Event()

        def capture(self, target, cancelled):
            self.started.set()
            while not cancelled():
                time.sleep(0.01)
            self.cancelled.set()
            return super().capture(target)

    window.backend = Slow()
    window.window_combo.setCurrentIndex(1)
    window.start_monitoring()
    wait_until(window.backend.started.is_set)
    window.pause_monitoring()
    wait_until(window.backend.cancelled.is_set)
    wait_until(lambda: window.worker is None)
    assert window.frame_image is None


@pytest.mark.parametrize("failure", ["目標視窗已關閉", "取得全黑畫面", "擷取逾時"])
def test_capture_error_stops_without_automatic_retry(window, failure):
    class Broken(DemoBackend):
        def capture(self, target, cancelled):
            self.counter += 1
            raise CaptureError(failure)

    window.backend = Broken()
    window.window_combo.setCurrentIndex(1)
    window.start_monitoring()
    wait_until(lambda: window.error_label.isVisible())
    wait_until(lambda: window.worker is None)
    assert window.state == MonitorState.STOPPED
    assert not window.timer.isActive()
    assert failure in window.error_label.text()
    assert "不會改擷取桌面" in window.error_label.text()
    assert window.backend.counter == 1
    assert window.frame_image is None


def test_invalid_frame_is_reported_not_rendered(window):
    window.window_combo.setCurrentIndex(1)
    window.state = MonitorState.RUNNING
    window.receive_frame(Frame(640, 360, b"bad"), window.generation)
    assert window.state == MonitorState.STOPPED
    assert "資料尺寸" in window.error_label.text()


def test_multilingual_bubbles_and_real_roster_states(window, monkeypatch):
    for index, character in enumerate(window.characters):
        window.character_combo.setCurrentIndex(index)
        window.add_test_message()
        card = window.cards[-1]
        assert card.character is character
        assert window.roster_labels[character.id].text() == "· 未出場"  # Display samples are not real replies.
        assert hasattr(card, "translation_label") == (character.language != "zh-TW")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.clear_chat()
    assert not window.cards
    assert all(row.text() == "· 未出場" for row in window.roster_labels.values())


def test_input_is_plain_text_no_fake_api_response(window):
    initial = len(window.cards)
    text = "<script>こんにちは</script> hello 月鹿"
    window.chat_input.setPlainText(text)
    QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
    assert len(window.cards) == initial + 1
    labels = window.cards[-1].findChildren(QLabel)
    content = next(item for item in labels if item.objectName() == "messageText")
    assert content.text() == text
    assert content.textFormat() == Qt.TextFormat.PlainText
    assert window.chat_input.toPlainText() == ""
    assert all(row.text() == "· 未出場" for row in window.roster_labels.values())


def test_empty_and_oversized_input(window):
    initial = len(window.cards)
    window.send_message()
    assert len(window.cards) == initial
    window.chat_input.setPlainText("字" * 4001)
    window.send_message()
    assert len(window.cards) == initial
    assert "4000" in window.error_label.text()
    assert window.chat_input.toPlainText()


def test_enter_shift_enter_and_ime_composition(window):
    initial = len(window.cards)
    editor = window.chat_input
    editor.setPlainText("月鹿")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    QTest.keyClick(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    assert len(window.cards) == initial
    assert editor.toPlainText() == "月鹿\n"
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert len(window.cards) == initial + 1
    editor.setPlainText("組字中")
    QApplication.sendEvent(editor, QInputMethodEvent("月", []))
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert len(window.cards) == initial + 1
    QApplication.sendEvent(editor, QInputMethodEvent("", []))
    QTest.keyClick(editor, Qt.Key.Key_Return)
    assert len(window.cards) == initial + 2


def test_reader_stays_in_history_new_message_does_not_force_scroll(window):
    for _ in range(24):
        window.add_test_message()
    QTest.qWait(100)
    bar = window.scroll.verticalScrollBar()
    assert bar.maximum() > 0
    window.scroll_to_bottom()
    bar.setValue(0)
    window.add_test_message()
    QTest.qWait(100)
    assert bar.value() == 0
    assert window.jump_button.isVisible()
    window.scroll_to_bottom()
    window.add_test_message()
    QTest.qWait(100)
    assert bar.value() == bar.maximum()


def test_window_resize_topmost_minimize_restore_theme(window):
    window.resize(980, 700)
    QTest.qWait(30)
    assert window.width() == 980
    assert window.height() == 700
    window.topmost.setChecked(True)
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.topmost.setChecked(False)
    assert not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.showMinimized()
    assert window.isMinimized()
    window.showNormal()
    assert not window.isMinimized()
    window.add_test_message()
    window.theme_combo.setCurrentIndex(2)
    assert window.night
    assert "#8D6225" in window.cards[-1].styleSheet()
    assert "#FFFFFF" in window.cards[-1].styleSheet()
    window.theme_combo.setCurrentIndex(1)
    assert not window.night
    assert "#F9D48B" in window.cards[-1].styleSheet()


def test_non_windows_is_explicit_not_demo_by_default(app, store):
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        assert window.window_combo.count() == 1
        assert not window.start_button.isEnabled()
        assert "不是 Windows" in window.error_label.text()
        assert "尚未串接" in window.api_status.text()
    finally:
        window.close()
