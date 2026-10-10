"""PyInstaller entry point; absolute imports also work outside the package."""
import multiprocessing
import sys


def run():
    if sys.argv[1:2] == ["--package-check"]:
        if len(sys.argv) != 3:
            return 2
        from our_little_days.package_check import write_report
        return write_report(sys.argv[2])
    try:
        from our_little_days.app import main
        return main()
    except Exception:
        import ctypes
        import os
        from pathlib import Path
        import traceback
        from datetime import datetime
        directory = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "OurLittleDays" / "logs"
        details = traceback.format_exc()
        try:
            directory.mkdir(parents=True, exist_ok=True)
            log = directory / f"startup-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
            log.write_text(details, encoding="utf-8")
            message = f"公寓無法啟動，請保留錯誤紀錄：\n{log}\n\n已儲存資料不會被重置。"
        except OSError:
            message = "公寓無法啟動，且無法寫入錯誤紀錄。\n請確認資料夾可寫入並保留已有資料。\n\n" + details[-1500:]
        if sys.platform == "win32":
            ctypes.windll.user32.MessageBoxW(None, message, "公寓啟動失敗", 0x10)
        return 1

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(run())
