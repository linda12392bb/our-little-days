"""Exercise the actual Qt UI offline and save current-run evidence.

Run from repository root with QT_QPA_PLATFORM=offscreen on headless Linux.
Screenshots are of the synthetic demonstration UI, never a user's real window.
"""
import json
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from our_little_days.capture import DemoBackend
from our_little_days.models import MonitorState, load_characters, load_apartment_settings, taipei_now
from our_little_days.storage import SQLiteStore
from our_little_days.ui import MainWindow


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(20)
    if not predicate():
        raise RuntimeError("UI smoke check timed out")


def main():
    destination = Path("artifacts")
    destination.mkdir(exist_ok=True)
    report_file = destination / "smoke-report.json"
    # A failed run must not leave a previous successful report looking current.
    report_file.unlink(missing_ok=True)
    app = QApplication.instance() or QApplication([])
    temporary = TemporaryDirectory(prefix="our-little-days-smoke-")
    store = SQLiteStore(Path(temporary.name) / "apartment.sqlite3")
    store.seed(load_characters(), load_apartment_settings())
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    checks = []
    try:
        window.show()
        window.theme_combo.setCurrentIndex(1)
        window.window_combo.setCurrentIndex(1)
        QTest.mouseClick(window.start_button, Qt.MouseButton.LeftButton)
        wait_for(lambda: window.frame_image is not None)
        wait_for(lambda: window.worker is None)
        checks.append("demo selected-window capture returned 640x360 pixels")
        for index in (0, 2, 11):
            window.character_combo.setCurrentIndex(index)
            window.add_test_message()
        window.chat_input.setPlainText("今天先讓公寓亮起來。你好、こんにちは、hello ☾")
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        if len(window.cards) != 6:
            raise RuntimeError("Expected system, three test, and user bubbles")
        checks.append("Chinese, English, Japanese+translation, user bubbles rendered")
        QTest.qWait(80)
        if not window.grab().save(str(destination / "phase1-day.png")):
            raise RuntimeError("Could not write day UI screenshot")
        window.theme_combo.setCurrentIndex(2)
        QTest.qWait(50)
        if not window.grab().save(str(destination / "phase1-night.png")):
            raise RuntimeError("Could not write night UI screenshot")
        checks.append("actual Qt UI rendered both themes")
        window.pause_monitoring()
        if window.frame_image is not None or window.state != MonitorState.PAUSED:
            raise RuntimeError("Pause did not clear capture preview")
        window.start_monitoring()
        wait_for(lambda: window.frame_image is not None)
        window.stop_monitoring()
        if window.timer.isActive() or window.frame_image is not None:
            raise RuntimeError("Stop did not disable capture")
        checks.append("start/pause/resume/stop capture lifecycle")
        window.topmost.setChecked(True)
        if not window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
            raise RuntimeError("Topmost Qt flag missing")
        window.topmost.setChecked(False)
        window.resize(980, 700)
        window.move(30, 30)
        window.showMinimized()
        if not window.isMinimized():
            raise RuntimeError("Minimize state missing")
        window.showNormal()
        QTest.qWait(30)
        if not window.grab().save(str(destination / "phase1-compact.png")):
            raise RuntimeError("Could not write compact UI screenshot")
        checks.append("Qt resize/move/minimize/restore/topmost properties")
        report = {
            "time_taipei": taipei_now().isoformat(),
            "platform": sys.platform,
            "qt_platform": app.platformName(),
            "backend": "synthetic demo (not actual Windows capture)",
            "status": "passed",
            "checks": checks,
            "unverified": ["Win32 enumeration/PrintWindow", "native OS dragging and stacking", "Windows executable build", "live model API and native Windows Phase 2/3 autonomous commentary"],
        }
        report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        window.close()
        app.processEvents()
        store.close()
        temporary.cleanup()


if __name__ == "__main__":
    main()
