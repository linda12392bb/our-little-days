"""Win32 enumeration and PrintWindow. Never falls back to desktop capture."""
import ctypes as C
from ctypes import wintypes as W
import sys

from .capture import CaptureError
from .models import Frame, WindowInfo

if sys.platform != "win32":
    raise ImportError("win32 模組僅支援 Windows")

user32 = C.WinDLL("user32", use_last_error=True)
gdi32 = C.WinDLL("gdi32", use_last_error=True)
EnumProc = C.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


def _bind(lib, name, args, result):
    func = getattr(lib, name)
    func.argtypes = args
    func.restype = result
    return func


enum_windows = _bind(user32, "EnumWindows", [EnumProc, W.LPARAM], W.BOOL)
is_window = _bind(user32, "IsWindow", [W.HWND], W.BOOL)
is_visible = _bind(user32, "IsWindowVisible", [W.HWND], W.BOOL)
is_iconic = _bind(user32, "IsIconic", [W.HWND], W.BOOL)
title_length = _bind(user32, "GetWindowTextLengthW", [W.HWND], C.c_int)
get_title = _bind(user32, "GetWindowTextW", [W.HWND, W.LPWSTR, C.c_int], C.c_int)
get_pid = _bind(user32, "GetWindowThreadProcessId", [W.HWND, C.POINTER(W.DWORD)], W.DWORD)
get_rect = _bind(user32, "GetWindowRect", [W.HWND, C.POINTER(W.RECT)], W.BOOL)
get_dc = _bind(user32, "GetDC", [W.HWND], W.HDC)
release_dc = _bind(user32, "ReleaseDC", [W.HWND, W.HDC], C.c_int)
print_window = _bind(user32, "PrintWindow", [W.HWND, W.HDC, W.UINT], W.BOOL)
create_dc = _bind(gdi32, "CreateCompatibleDC", [W.HDC], W.HDC)
create_bitmap = _bind(gdi32, "CreateCompatibleBitmap", [W.HDC, C.c_int, C.c_int], W.HBITMAP)
select_object = _bind(gdi32, "SelectObject", [W.HDC, W.HANDLE], W.HANDLE)
delete_object = _bind(gdi32, "DeleteObject", [W.HANDLE], W.BOOL)
delete_dc = _bind(gdi32, "DeleteDC", [W.HDC], W.BOOL)


class BitmapHeader(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                ("image_size", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("used", W.DWORD), ("important", W.DWORD)]


class BitmapInfo(C.Structure):
    _fields_ = [("header", BitmapHeader), ("colors", W.DWORD * 1)]


get_bits = _bind(gdi32, "GetDIBits", [W.HDC, W.HBITMAP, W.UINT, W.UINT, W.LPVOID, C.POINTER(BitmapInfo), W.UINT], C.c_int)


def enable_dpi_awareness():
    # In the child, Qt isn't initialized. Use physical window dimensions.
    if hasattr(user32, "SetProcessDpiAwarenessContext"):
        func = _bind(user32, "SetProcessDpiAwarenessContext", [W.HANDLE], W.BOOL)
        func(C.c_void_p(-4))


def list_windows(exclude_pid):
    result = []

    @EnumProc
    def visit(hwnd, _):
        if is_visible(hwnd):
            length = title_length(hwnd)
            if length:
                title = C.create_unicode_buffer(length + 1)
                get_title(hwnd, title, length + 1)
                pid = W.DWORD()
                get_pid(hwnd, C.byref(pid))
                if pid.value != exclude_pid and title.value.strip():
                    result.append(WindowInfo(int(hwnd), title.value, pid.value))
        return True

    if not enum_windows(visit, 0):
        raise CaptureError("無法列出 Windows 視窗。")
    return sorted(result, key=lambda window: window.title.casefold())


def capture_window(window):
    enable_dpi_awareness()
    hwnd = window.handle
    pid = W.DWORD()
    get_pid(hwnd, C.byref(pid))
    if not is_window(hwnd) or pid.value != window.pid:
        raise CaptureError("目標視窗已關閉或已更換；請重新選擇。")
    if is_iconic(hwnd):
        raise CaptureError("目標視窗已最小化，請還原後再開始。")
    rect = W.RECT()
    if not get_rect(hwnd, C.byref(rect)):
        raise CaptureError("無法讀取目標視窗尺寸。")
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0 or width * height > 16_777_216:
        raise CaptureError("目標視窗尺寸無效或超過原型的 1600 萬像素限制。")
    source = get_dc(hwnd)
    if not source:
        raise CaptureError("無法取得指定視窗的繪圖資源。")
    memory, bitmap, previous = None, None, None
    try:
        memory = create_dc(source)
        bitmap = create_bitmap(source, width, height)
        if not memory or not bitmap:
            raise CaptureError("無法建立擷取緩衝區。")
        previous = select_object(memory, bitmap)
        if not previous or previous == C.c_void_p(-1).value:
            previous = None
            raise CaptureError("無法選取擷取緩衝區。")
        if not print_window(hwnd, memory, 2):  # PW_RENDERFULLCONTENT
            raise CaptureError("目標不支援擷取；可能受保護或使用特殊渲染。")
        select_object(memory, previous)
        previous = None
        info = BitmapInfo()
        info.header = BitmapHeader(C.sizeof(BitmapHeader), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        buffer = (C.c_ubyte * (width * height * 4))()
        if get_bits(memory, bitmap, 0, height, buffer, C.byref(info), 0) != height:
            raise CaptureError("無法讀取擷取像素。")
        # Sample RGB only. Black frames are ambiguous: never label them as confirmed DRM.
        stride = max(4, ((width * height // 1024) * 4))
        if not any(buffer[offset + channel] > 3 for offset in range(0, len(buffer), stride) for channel in range(3)):
            raise CaptureError("取得全黑或近黑畫面；可能是暗場、受保護內容或渲染不相容。")
        return Frame(width, height, bytes(buffer))
    finally:
        if previous and memory:
            select_object(memory, previous)
        if bitmap:
            delete_object(bitmap)
        if memory:
            delete_dc(memory)
        release_dc(hwnd, source)
