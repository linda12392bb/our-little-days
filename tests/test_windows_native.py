"""Runs only on an interactive Windows desktop; Linux must report this skipped."""
import subprocess
import sys
import uuid

import pytest
from PySide6.QtTest import QTest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="需 Windows 10/11 互動式桌面，Linux 無法驗證 Win32 擷取")


def test_actual_window_enumeration_capture_close(app):
    from our_little_days.capture import CaptureError, WindowsBackend
    title = "OUR LITTLE DAYS native test " + uuid.uuid4().hex
    # Different process so normal own-app filtering remains active.
    script = """
import sys
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import QTimer
app=QApplication([])
w=QWidget(); w.setWindowTitle(sys.argv[1]); w.setStyleSheet('background: #ADCFDB;')
w.resize(400,240); w.show()
QTimer.singleShot(30000, app.quit)
app.exec()
"""
    child = subprocess.Popen([sys.executable, "-c", script, title])
    try:
        backend = WindowsBackend()
        windows = []
        for _ in range(100):
            windows = [w for w in backend.list_windows() if w.title == title]
            if windows:
                break
            QTest.qWait(50)
        assert windows, "測試視窗未被列出；確認 Windows 工作階段有互動式桌面"
        frame = backend.capture(windows[0])
        assert frame.width >= 400 and frame.height >= 240
        assert len(frame.pixels) == frame.width * frame.height * 4
        center = ((frame.height // 2) * frame.width + frame.width // 2) * 4
        assert tuple(frame.pixels[center:center + 3]) == (219, 207, 173)
        child.terminate()
        child.wait(timeout=5)
        with pytest.raises(CaptureError, match="關閉|更換"):
            backend.capture(windows[0])
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
