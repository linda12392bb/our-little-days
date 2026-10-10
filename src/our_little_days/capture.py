"""Capture only the selected HWND. Native calls are isolated and time limited."""
import multiprocessing as mp
import os
import sys
import time

from .models import Frame, WindowInfo


class CaptureError(RuntimeError):
    pass


class UnsupportedBackend:
    label = "Windows 擷取不可用"
    is_demo = False

    def list_windows(self):
        return []

    def capture(self, window, cancelled=lambda: False):
        raise CaptureError("指定視窗擷取僅支援 Windows 10／11。")


class DemoBackend:
    """Explicit, synthetic fixture for Linux UI development; no screen access."""
    label = "離線展示 · 非真實視窗"
    is_demo = True

    def __init__(self):
        self.counter = 0

    def list_windows(self):
        return [WindowInfo(1, "展示測試畫面（不會讀取電腦視窗）", 0)]

    def capture(self, window, cancelled=lambda: False):
        self.counter += 1
        width, height = 640, 360
        sky = bytes((180, 151, 121, 255)) * width
        wall = bytes((206, 223, 239, 255)) * width
        floor = bytes((164, 190, 206, 255)) * width
        rows = [sky] * 140 + [wall] * 155 + [floor] * 65
        marker = 120 + self.counter % 150
        rows[marker] = bytes((120, 114, 155, 255)) * width
        return Frame(width, height, b"".join(rows))


def _native_capture(connection, window):
    """Child process can be killed if PrintWindow hangs on an unresponsive app."""
    try:
        from .win32 import capture_window
        connection.send((True, capture_window(window)))
    except Exception as exc:
        connection.send((False, str(exc)))
    finally:
        connection.close()


class WindowsBackend:
    label = "Windows · PrintWindow"
    is_demo = False

    def list_windows(self):
        from .win32 import list_windows
        return list_windows(exclude_pid=os.getpid())

    def capture(self, window, cancelled=lambda: False):
        ctx = mp.get_context("spawn")
        receiver, sender = ctx.Pipe(duplex=False)
        process = ctx.Process(target=_native_capture, args=(sender, window), daemon=True)
        try:
            process.start()
            sender.close()
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                if cancelled():
                    raise CaptureError("擷取已取消。")
                if receiver.poll(0.05):
                    try:
                        ok, value = receiver.recv()
                    except EOFError as exc:
                        raise CaptureError("擷取程序提前結束；請重新選擇視窗。") from exc
                    if not ok:
                        raise CaptureError(value)
                    return value
            raise CaptureError("擷取逾時；目標可能未回應或不支援 PrintWindow。")
        finally:
            if process.pid is not None:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
                process.close()
            receiver.close()
            sender.close()


def make_backend(demo=False):
    if demo:
        return DemoBackend()
    return WindowsBackend() if sys.platform == "win32" else UnsupportedBackend()
