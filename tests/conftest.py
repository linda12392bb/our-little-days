import os
import sys

# Windows native integration tests need the real desktop platform plugin.
if sys.platform != "win32":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def store(tmp_path):
    from our_little_days.storage import SQLiteStore
    from our_little_days.models import load_characters, load_apartment_settings
    database = SQLiteStore(tmp_path / "apartment.sqlite3")
    database.seed(load_characters(), load_apartment_settings())
    yield database
    database.close()
