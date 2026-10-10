import argparse
import multiprocessing
from pathlib import Path
import sys
import sqlite3

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QLockFile

from .capture import make_backend
from .models import load_characters, load_apartment_settings
from .storage import SQLiteStore, StorageError, default_data_dir
from .ui import MainWindow


def main():
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="月色不打烊 · Phase 2/3 Windows 本機陪伴")
    parser.add_argument("--demo", action="store_true", help="明確標示的合成畫面展示；不擷取任何真實視窗")
    parser.add_argument("--characters", type=Path, help="載入自訂十三聲道 JSON 設定檔")
    parser.add_argument("--data-dir", type=Path, help="指定本機持久資料目錄；預設為作業系統的使用者資料位置")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("OUR LITTLE DAYS")
    app.setOrganizationName("OurLittleDays")
    app.setFont(QFont("Microsoft JhengHei", 10))
    store = None
    try:
        directory = (args.data_dir or default_data_dir()).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        instance_lock = QLockFile(str(directory / "instance.lock"))
        instance_lock.setStaleLockTime(0)
        if not instance_lock.tryLock(0):
            raise StorageError("同一份公寓資料已由另一個程式使用，請先關閉另一個視窗。")
        characters = load_characters(args.characters)
        store = SQLiteStore(directory / "apartment.sqlite3")
        if args.characters and store.connection.execute("SELECT COUNT(*) FROM characters").fetchone()[0]:
            raise StorageError("這個資料目錄已有永久角色設定；不會自動覆寫。自訂 JSON 僅能用於新資料目錄；請先備份並用 --data-dir 選擇新的目錄。")
        store.seed(characters, load_apartment_settings())
        store.apply_confirmed_order_speech_update()
        characters = store.load_characters()
    except (OSError, ValueError, TypeError, KeyError, StorageError, sqlite3.DatabaseError) as exc:
        if store:
            store.close()
        QMessageBox.critical(None, "公寓資料載入失敗", str(exc))
        return 1
    window = MainWindow(make_backend(args.demo), characters, store)
    window.show()
    try:
        return app.exec()
    finally:
        store.close()
        instance_lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
