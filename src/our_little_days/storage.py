"""Transactional local persistence. No screenshots, API keys, or automatic resets."""
from dataclasses import asdict, replace
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from .models import Character, taipei_now, validate_characters

APP_ID = 0x4F4C4431
SCHEMA_VERSION = 1
TABLES = {"settings", "characters", "worldlines", "relationships", "messages", "events", "memories"}


def default_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "OurLittleDays"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "our-little-days"


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class StorageError(RuntimeError):
    pass


class SQLiteStore:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = None
        try:
            self.connection = sqlite3.connect(self.path, timeout=5)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys=ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            app_id = self.connection.execute("PRAGMA application_id").fetchone()[0]
            tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if version > SCHEMA_VERSION:
                raise StorageError("資料庫版本較新，請更新程式；不會重置資料。")
            if version == 0 and not tables:
                self._create_schema()
            elif version != SCHEMA_VERSION or app_id != APP_ID or not TABLES.issubset(tables):
                raise StorageError("資料庫不相容；請保留原檔並使用有效備份，程式不會重置。")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
        except Exception:
            self.close()
            raise

    def _create_schema(self):
        self.connection.executescript(f"""
            BEGIN IMMEDIATE;
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE characters (
                id TEXT PRIMARY KEY, display_order INTEGER NOT NULL,
                profile_json TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE worldlines (id TEXT PRIMARY KEY, label TEXT NOT NULL);
            CREATE TABLE relationships (
                character_id TEXT NOT NULL REFERENCES characters(id),
                worldline_id TEXT NOT NULL REFERENCES worldlines(id),
                relation_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(character_id, worldline_id)
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worldline_id TEXT NOT NULL REFERENCES worldlines(id),
                speaker_id TEXT NOT NULL, speaker_name TEXT NOT NULL,
                text TEXT NOT NULL, translation TEXT NOT NULL DEFAULT '',
                recipient_ids_json TEXT NOT NULL DEFAULT '[]',
                reply_to INTEGER REFERENCES messages(id) ON DELETE SET NULL,
                source_kind TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX messages_worldline ON messages(worldline_id, id);
            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                worldline_id TEXT NOT NULL REFERENCES worldlines(id),
                summary TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{{}}',
                importance TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                character_id TEXT REFERENCES characters(id),
                worldline_id TEXT NOT NULL REFERENCES worldlines(id),
                content TEXT NOT NULL,
                source_event_id INTEGER REFERENCES events(id) ON DELETE SET NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX memories_scope ON memories(worldline_id, character_id);
            PRAGMA application_id={APP_ID};
            PRAGMA user_version={SCHEMA_VERSION};
            COMMIT;
        """)

    def seed(self, characters, settings):
        """Insert missing defaults only: updates/restarts never overwrite saved profiles."""
        now = taipei_now().isoformat()
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO settings VALUES ('apartment', ?)", (encode(settings),))
            worlds = {"main": "常駐主線", "eleven_months": "十一個月 AU", "glasshouse": "GLASSHOUSE 哨嚮 AU", "brothers": "兄弟 AU"}
            for key, name in worlds.items():
                self.connection.execute("INSERT OR IGNORE INTO worldlines VALUES (?, ?)", (key, name))
            for index, character in enumerate(characters):
                profile = asdict(character)
                inserted = self.connection.execute("INSERT OR IGNORE INTO characters VALUES (?, ?, ?, ?)", (character.id, index, encode(profile), now)).rowcount
                if not inserted:
                    continue
                relationships = character.relationships or {}
                relations = relationships.get("worldlines", {"main": relationships})
                for world, relation in relations.items():
                    if world not in worlds:
                        self.connection.execute("INSERT OR IGNORE INTO worldlines VALUES (?, ?)", (world, relation.get("label", world)))
                    self.connection.execute("INSERT OR IGNORE INTO relationships VALUES (?, ?, ?, ?)", (character.id, world, encode(relation), now))

    def settings(self):
        return json.loads(self.connection.execute("SELECT value FROM settings WHERE key='apartment'").fetchone()[0])

    def set_active_worldline(self, worldline):
        if not self.connection.execute("SELECT 1 FROM worldlines WHERE id=?", (worldline,)).fetchone():
            raise StorageError("世界線不存在。")
        value = self.settings()
        value["active_worldline"] = worldline
        with self.connection:
            self.connection.execute("UPDATE settings SET value=? WHERE key='apartment'", (encode(value),))

    def load_characters(self):
        result = []
        for row in self.connection.execute("SELECT * FROM characters ORDER BY display_order"):
            profile = json.loads(row["profile_json"])
            relations = {entry["worldline_id"]: json.loads(entry["relation_json"]) for entry in self.connection.execute("SELECT * FROM relationships WHERE character_id=?", (row["id"],))}
            if "worldlines" in (profile.get("relationships") or {}):
                profile["relationships"] = {"worldlines": relations}
            elif relations.get("main"):
                profile["relationships"] = relations["main"]
            result.append(Character(**profile))
        validate_characters(result)
        return result

    def apply_confirmed_order_speech_update(self):
        """Only the user's approved flirting/heart additions; keep custom fields intact."""
        order = next(c for c in self.load_characters() if c.id == "order")
        addition = "說話常帶調戲語氣，常使用 ♡。"
        behavior = list(order.behavior)
        tone = order.tone
        changed = False
        if addition not in behavior:
            behavior.append(addition)
            changed = True
        if "♡" not in tone or "調戲" not in tone:
            tone += "；說話常帶調戲語氣與 ♡。"
            changed = True
        if not changed:
            return None
        safety = self.path.parent / "backups" / f"before-order-speech-{taipei_now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3"
        self.backup(safety)
        self.save_character(replace(order, tone=tone, behavior=tuple(behavior)))
        return safety

    def save_character(self, character):
        """Explicit edits only. Relationship changes use a separate scoped API."""
        with self.connection:
            count = self.connection.execute("UPDATE characters SET profile_json=?, updated_at=? WHERE id=?", (encode(asdict(character)), taipei_now().isoformat(), character.id)).rowcount
            if count != 1:
                raise StorageError("角色不存在。")

    def apply_confirmed_shogo_address_update(self, *, force=False):
        """One approved update; preserve original text, custom traits and all AU relations."""
        marker = 'confirmed:shogo-address-register-2026-10-11'
        if not force and self.connection.execute('SELECT 1 FROM settings WHERE key=?', (marker,)).fetchone():
            return None
        shogo = next(c for c in self.load_characters() if c.id == 'shogo')
        behavior = list(shogo.behavior)
        for addition in ('平常稱呼使用者為「月」。',
                         '喜歡主動表達愛意；維持原作人格與獨立思考，依各世界線已確認關係互動，不以愛意擅自推進或改寫關係狀態。',
                         '喜歡看使用者的各種反應；當使用者真的表現不喜歡時，會停下當下的互動。',
                         '以柔和的自然日文常體說話，不使用敬語；日文自稱「僕」，不套用 REO 的全敬語模式。',
                         '語氣柔和不等於溫柔；仍會有點壞心眼，不改成一味體貼、順從的性格。',
                         '人格維持《PSYCHO-PASS》第一季槙島聖護；在已確認戀人的世界線，對使用者的對待與其他人有差別，但不因戀愛改成另一種人格。'):
            if addition not in behavior:
                behavior.append(addition)
        tone = shogo.tone or ''
        if '不使用敬語' not in tone or '柔和' not in tone:
            tone += '語氣柔和，以常體說話，不使用敬語。日文自稱「僕」。'
        rules = dict(shogo.language_rules or {})
        rules['register'] = '柔和的自然日文常體；不使用敬語'
        changed = shogo.user_address != '月' or behavior != list(shogo.behavior) or tone != shogo.tone or rules != shogo.language_rules or shogo.self_reference != '僕'
        safety = None
        if changed:
            safety = self.path.parent / 'backups' / f'before-shogo-address-{taipei_now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3'
            self.backup(safety)
        with self.connection:
            if changed:
                updated = replace(shogo, user_address='月', behavior=tuple(behavior), tone=tone, language_rules=rules, self_reference='僕')
                self.connection.execute('UPDATE characters SET profile_json=?,updated_at=? WHERE id=?',
                                        (encode(asdict(updated)), taipei_now().isoformat(), 'shogo'))
            self.connection.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (marker, encode({'user_address':'月','proactive_affection':True})))
        return safety

    def record_persona_preparation(self, characters, worldline, kind, model):
        """Local diagnostic only; never contains keys, screenshots or chat text."""
        import hashlib
        value = {'prepared_at': taipei_now().isoformat(), 'worldline': worldline, 'kind': kind, 'model': model,
                 'characters': {c.id: {'user_address': c.user_address, 'tone': c.tone, 'self_reference': c.self_reference, 'behavior': list(c.behavior), 'language_rules': c.language_rules,
                                      'source_text_sha256': hashlib.sha256((c.source_text or '').encode()).hexdigest()}
                                for c in characters}}
        with self.connection:
            self.connection.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                                    ('ai-persona-preparation:'+worldline, encode(value)))

    def persona_preparation(self, worldline):
        row = self.connection.execute('SELECT value FROM settings WHERE key=?', ('ai-persona-preparation:'+worldline,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_relationship(self, character_id, worldline, relation, *, confirmed=False):
        if not confirmed:
            raise StorageError("修改角色關係需要使用者確認。")
        with self.connection:
            self.connection.execute("INSERT INTO relationships VALUES (?, ?, ?, ?) ON CONFLICT(character_id, worldline_id) DO UPDATE SET relation_json=excluded.relation_json, updated_at=excluded.updated_at", (character_id, worldline, encode(relation), taipei_now().isoformat()))

    def append_message(self, *, speaker_id, speaker_name, text, translation="", worldline="main", source_kind="user", recipient_ids=(), reply_to=None):
        now = taipei_now().isoformat()
        with self.connection:
            cursor = self.connection.execute("INSERT INTO messages(worldline_id,speaker_id,speaker_name,text,translation,recipient_ids_json,reply_to,source_kind,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (worldline, speaker_id, speaker_name, text, translation, encode(list(recipient_ids)), reply_to, source_kind, now))
        return dict(self.connection.execute("SELECT * FROM messages WHERE id=?", (cursor.lastrowid,)).fetchone())

    def messages(self, worldline="main"):
        return [dict(row) for row in self.connection.execute("SELECT * FROM messages WHERE worldline_id=? ORDER BY id", (worldline,))]

    def conversation_state(self, worldline="main"):
        row = self.connection.execute('SELECT value FROM settings WHERE key=?', ('conversation:'+worldline,)).fetchone()
        return json.loads(row[0]) if row else {}

    def roster_status(self, worldline="main"):
        """Latest saved user turn only; read decisions require explicit AI evidence."""
        user = self.connection.execute("SELECT id FROM messages WHERE worldline_id=? AND speaker_id='user' AND source_kind IN ('user','voice') ORDER BY id DESC LIMIT 1", (worldline,)).fetchone()
        if not user:
            return {'user_message_id':None, 'replied':[], 'read':[]}
        connected, replied = {user['id']}, []
        messages = self.connection.execute("SELECT id,speaker_id,reply_to FROM messages WHERE worldline_id=? AND id>? AND source_kind='ai' ORDER BY id", (worldline,user['id']))
        for m in messages:
            if m['reply_to'] in connected:
                connected.add(m['id'])
                replied.append(m['speaker_id'])
        row = self.connection.execute('SELECT value FROM settings WHERE key=?', ('roster:'+worldline,)).fetchone()
        decision = json.loads(row[0]) if row else {}
        read = decision.get('read_character_ids', []) if decision.get('user_message_id')==user['id'] else []
        return {'user_message_id':user['id'], 'replied':list(dict.fromkeys(replied)), 'read':read}

    def conversation_context(self, worldline="main"):
        state = self.conversation_state(worldline)
        # Original quotes and IDs survive long chat histories; no invented summary.
        anchors = state.get('anchor_ids', [])
        messages = [dict(row) for row in self.connection.execute('SELECT * FROM messages WHERE worldline_id=? ORDER BY id', (worldline,)) if row['id'] in anchors]
        return {'state': state, 'anchor_messages': messages}

    def clear_messages(self, worldline, *, confirmed=False):
        if not confirmed:
            raise StorageError("刪除聊天紀錄需要使用者確認。")
        with self.connection:
            self.connection.execute("DELETE FROM messages WHERE worldline_id=?", (worldline,))
            self.connection.execute('DELETE FROM settings WHERE key=?', ('conversation:'+worldline,))
            self.connection.execute('DELETE FROM settings WHERE key=?', ('roster:'+worldline,))

    def add_event(self, summary, worldline="main", evidence=None, importance="important"):
        if not summary.strip():
            raise StorageError("重要事件不可空白。")
        with self.connection:
            cursor = self.connection.execute("INSERT INTO events(worldline_id,summary,evidence_json,importance,created_at) VALUES(?,?,?,?,?)", (worldline, summary, encode(evidence or {}), importance, taipei_now().isoformat()))
        return cursor.lastrowid

    def add_memory(self, content, worldline="main", character_id=None, source_event_id=None):
        if not content.strip():
            raise StorageError("長期記憶不可空白。")
        if source_event_id is not None:
            event = self.connection.execute("SELECT worldline_id FROM events WHERE id=?", (source_event_id,)).fetchone()
            if not event or event[0] != worldline:
                raise StorageError("記憶來源事件必須屬於同一世界線。")
        with self.connection:
            cursor = self.connection.execute("INSERT INTO memories(character_id,worldline_id,content,source_event_id,updated_at) VALUES(?,?,?,?,?)", (character_id, worldline, content, source_event_id, taipei_now().isoformat()))
        return cursor.lastrowid

    def events(self, worldline="main"):
        return [dict(row) for row in self.connection.execute("SELECT * FROM events WHERE worldline_id=? ORDER BY id", (worldline,))]

    def memories(self, worldline="main"):
        return [dict(row) for row in self.connection.execute("SELECT * FROM memories WHERE worldline_id=? ORDER BY id", (worldline,))]

    @staticmethod
    def validate_backup(path):
        path = Path(path).resolve()
        if not path.is_file():
            raise StorageError("備份檔案不存在。")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute("PRAGMA application_id").fetchone()[0] != APP_ID or source.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise StorageError("這不是相容的公寓備份。")
            if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or source.execute("PRAGMA foreign_key_check").fetchone():
                raise StorageError("備份完整性檢查未通過。")
            tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not TABLES.issubset(tables):
                raise StorageError("備份缺少必要資料表。")
            settings = json.loads(source.execute("SELECT value FROM settings WHERE key='apartment'").fetchone()[0])
            rows = source.execute("SELECT id,profile_json FROM characters").fetchall()
            if len(rows) != 13 or settings.get("active_worldline") is None:
                raise StorageError("備份缺少十三角色或公寓設定。")
            profiles = [Character(**json.loads(row[1])) for row in rows]
            validate_characters(profiles)
            if any(profile.id != row[0] for profile, row in zip(profiles, rows)):
                raise StorageError("備份角色 ID 不一致。")
            if not source.execute("SELECT 1 FROM worldlines WHERE id=?", (settings["active_worldline"],)).fetchone():
                raise StorageError("備份的目前世界線不存在。")

    def backup(self, destination):
        destination = Path(destination).resolve()
        if destination in {self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm"), Path(str(self.path) + "-journal")}:
            raise StorageError("備份位置不能是正在使用的資料庫。")
        if destination.exists():
            raise StorageError("備份檔已存在，請選擇新的檔名。")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids overwriting an existing user file.
        destination.touch(exist_ok=False)
        try:
            with closing(sqlite3.connect(destination)) as output:
                self.connection.backup(output)
            self.validate_backup(destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return destination

    def restore(self, source_path, *, confirmed=False):
        if not confirmed:
            raise StorageError("還原會替換目前資料，必須先取得使用者確認。")
        source_path = Path(source_path).resolve()
        if source_path == self.path:
            raise StorageError("不能把正在使用的資料庫當成還原來源。")
        self.validate_backup(source_path)
        safety = self.path.parent / "backups" / f"before-restore-{taipei_now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.sqlite3"
        self.backup(safety)
        # SQLite's backup API copies a consistent snapshot, including live WAL data.
        # The destination transaction is committed only when the copy finishes.
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as incoming:
            incoming.backup(self.connection)
        self.connection.execute("PRAGMA foreign_keys=ON")
        return safety

    def close(self):
        if self.connection is not None:
            self.connection.close()
            self.connection = None
