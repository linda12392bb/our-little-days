"""角色設定與介面狀態；Phase 1 不推論角色人格或假裝 API 回覆。"""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from importlib.resources import files
import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from datetime import timedelta, timezone


class MonitorState(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


@dataclass(frozen=True)
class WindowInfo:
    handle: int
    title: str
    pid: int


@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    pixels: bytes  # top-down BGRA; never persisted


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    icon: str
    day_bubble: str
    day_text: str
    night_bubble: str
    language: str
    speech_label: str | None = None
    tone: str | None = None
    personality_core: str | None = None
    preferences: tuple = ()
    user_address: str | None = None
    relationships: dict | None = None
    speaking_conditions: tuple = ()
    prohibitions: tuple = ()
    identity: dict | None = None
    appearance: dict | None = None
    decision_rules: tuple = ()
    behavior: tuple = ()
    self_reference: str | None = None
    narration_language: str | None = None
    translation_required: bool = False
    language_rules: dict | None = None
    source_text: str | None = None
    pending_details: tuple = ()


def load_characters(path: Path | None = None) -> list[Character]:
    if path is None:
        directory = files("our_little_days").joinpath("data/characters")
        # Display order only, never a speaking or importance priority.
        ids = ["light", "order", "noel", "haru", "reo", "luoyang", "rixo", "senya", "mero", "sensei", "hao", "shogo", "yuri"]
        data = [json.loads(directory.joinpath(f"{key}.json").read_text(encoding="utf-8")) for key in ids]
    elif path.is_dir():
        data = [json.loads(item.read_text(encoding="utf-8")) for item in sorted(path.glob("*.json"))]
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) != 13:
        raise ValueError("角色設定必須包含完整十三聲道。")
    characters = [Character(**entry) for entry in data]
    validate_characters(characters)
    return characters


def validate_characters(characters):
    if len(characters) != 13:
        raise ValueError("角色設定必須包含完整十三聲道。")
    if len({c.id for c in characters}) != 13:
        raise ValueError("角色 ID 不可重複。")
    import re
    for c in characters:
        if not c.id or not c.name or not c.icon or c.language not in {"zh-TW", "ja", "ja-Kansai", "en"}:
            raise ValueError("角色 ID、名稱、圖示及主要語言不可空白或無效。")
        if not all(re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in (c.day_bubble, c.day_text, c.night_bubble)):
            raise ValueError(f"{c.name} 的顏色必須使用 #RRGGBB。")
        if c.language_rules and c.language_rules.get("speech_language") != c.language:
            raise ValueError(f"{c.name} 的語言欄位與語言規則不一致。")
        if c.source_text is not None and not isinstance(c.source_text, str):
            raise ValueError(f"{c.name} 的原文欄位必須為文字。")


def load_apartment_settings() -> dict:
    return json.loads(files("our_little_days").joinpath("data/apartment.json").read_text(encoding="utf-8"))


def taipei_now() -> datetime:
    # Windows Python installs may not have an IANA database; Taiwan uses UTC+8.
    try:
        tz = ZoneInfo("Asia/Taipei")
    except ZoneInfoNotFoundError:
        tz = timezone(timedelta(hours=8))
    return datetime.now(tz)


def is_night(hour: int) -> bool:
    return 0 <= hour < 6
