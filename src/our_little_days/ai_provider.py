"""OpenAI-only HTTPS transport and Windows per-user DPAPI credential storage."""
import ctypes
from ctypes import wintypes
import json
import multiprocessing
from pathlib import Path
import ssl
import sys
import time
import urllib.error
import urllib.request

from .ai_cost import AIError


class _Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


class WindowsKeyStore:
    def __init__(self, directory):
        self.path = Path(directory) / 'openai-key.dpapi'

    def _crypt(self, value, protect):
        if sys.platform != 'win32':
            raise AIError('API Key 安全保存只在 Windows 提供；本環境不會使用明文替代。')
        raw = ctypes.create_string_buffer(value)
        incoming = _Blob(len(value), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
        output = _Blob()
        dll = ctypes.WinDLL('crypt32', use_last_error=True)
        function = dll.CryptProtectData if protect else dll.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
        function.restype = wintypes.BOOL
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(output)):
            raise AIError('Windows 無法加密／解密 API Key；不會保存明文。')
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel.LocalFree(output.data)

    def save(self, value):
        if not value.strip() or len(value) > 1024 or any(c.isspace() for c in value):
            raise AIError('API Key 格式不正確。')
        encrypted = self._crypt(value.encode(), True)
        temporary = self.path.with_suffix('.new')
        temporary.write_bytes(encrypted)
        temporary.replace(self.path)

    def read(self):
        if not self.path.exists():
            raise AIError('尚未設定本機 API Key。')
        try:
            return self._crypt(self.path.read_bytes(), False).decode()
        except (OSError, UnicodeError):
            raise AIError('本機 API Key 無法讀取；請在設定中重新輸入。') from None

    def exists(self):
        return self.path.is_file()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise AIError('API 重新導向已拒絕；金鑰不會轉送其他網址。')


def _http_child(connection, key, payload):
    # No SDK automatic retries; no request/response/key data are logged to disk.
    try:
        request = urllib.request.Request('https://api.openai.com/v1/chat/completions',
          data=json.dumps(payload, ensure_ascii=False).encode(), method='POST',
          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        with opener.open(request, timeout=45) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise AIError('API 回應過大。')
        result = json.loads(raw)
        connection.send(('ok', result))
    except urllib.error.HTTPError as exc:
        hints = {401: '金鑰驗證失敗', 403: '模型或帳號存取遭拒', 429: '額度不足或請求限制'}
        connection.send(('error', f'API HTTP {exc.code}：{hints.get(exc.code, "請檢查 API 服務狀態")}。不會自動重試。'))
    except Exception:
        # Exception strings or provider error bodies can contain sensitive input.
        connection.send(('error', 'API 連線、憑證或回應失敗。不會自動重試；可能費用保留在用量紀錄。'))
    finally:
        connection.close()


def request_api(key, payload, cancelled, *, _transport=None):
    context = multiprocessing.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_transport or _http_child, args=(child, key, payload), daemon=True)
    process.start()
    child.close()
    deadline = time.monotonic() + 55
    try:
        while True:
            if cancelled():
                raise AIError('AI 請求已取消；若已送至供應商仍可能計費。')
            if parent.poll(0.05):
                try:
                    state, value = parent.recv()
                except EOFError:
                    raise AIError('API 子程序異常中斷；已保留預留費用。') from None
                if state != 'ok':
                    raise AIError(value)
                return value
            if time.monotonic() >= deadline or not process.is_alive():
                raise AIError('API 逾時或中斷；已保留預留費用，不自動重試。')
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join()
        parent.close()
