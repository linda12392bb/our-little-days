from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from our_little_days.models import load_apartment_settings, load_characters
from our_little_days.storage import SQLiteStore, StorageError


def message(store, text="已提交的訊息", **kwargs):
    return store.append_message(speaker_id="user", speaker_name="☾月鹿", text=text, **kwargs)


def test_normal_close_and_restart_restores_every_data_type(store):
    path = store.path
    first = message(store)
    reply = store.append_message(speaker_id="shogo", speaker_name="📖聖護", text="角色測試紀錄", source_kind="test", reply_to=first["id"], recipient_ids=["user"])
    store.append_message(speaker_id="sensei", speaker_name="🪞先生", text="角色間測試紀錄", source_kind="test", reply_to=reply["id"], recipient_ids=["shogo"])
    event = store.add_event("手動提供的重要事件")
    store.add_memory("手動提供的長期記憶", character_id="shogo", source_event_id=event)
    edited = replace(store.load_characters()[0], tone="使用者明確編輯的語氣")
    store.save_character(edited)
    store.set_relationship("shogo", "main", {"status": "正式交往", "date_agreement": True, "edited": True}, confirmed=True)
    store.close()
    reopened = SQLiteStore(path)
    try:
        reopened.seed(load_characters(), load_apartment_settings())
        assert reopened.load_characters()[0].tone == edited.tone
        assert len(reopened.messages()) == 3
        assert reopened.messages()[2]["reply_to"] == reply["id"]
        assert json.loads(reopened.messages()[2]["recipient_ids_json"]) == ["shogo"]
        assert reopened.events()[0]["summary"] == "手動提供的重要事件"
        assert reopened.memories()[0]["source_event_id"] == event
        shogo = next(c for c in reopened.load_characters() if c.id == "shogo")
        assert shogo.relationships["worldlines"]["main"]["edited"] is True
    finally:
        reopened.close()


def test_repeated_initialization_preserves_worldline_and_does_not_duplicate(store):
    store.set_active_worldline("glasshouse")
    message(store, worldline="glasshouse")
    for _ in range(3):
        store.seed(load_characters(), load_apartment_settings())
    assert len(store.load_characters()) == 13
    assert len(store.messages("glasshouse")) == 1
    assert store.settings()["active_worldline"] == "glasshouse"


def test_crash_recovers_committed_data_and_discards_uncommitted_transaction(tmp_path):
    path = tmp_path / "crash.sqlite3"
    script = """
import os, sys
from our_little_days.storage import SQLiteStore
from our_little_days.models import load_characters, load_apartment_settings
store=SQLiteStore(sys.argv[1]); store.seed(load_characters(),load_apartment_settings())
store.append_message(speaker_id='user',speaker_name='月鹿',text='已提交')
event=store.add_event('已提交事件')
store.add_memory('已提交記憶',character_id='shogo',source_event_id=event)
store.connection.execute("INSERT INTO messages(worldline_id,speaker_id,speaker_name,text,source_kind,created_at) VALUES('main','user','月鹿','未提交','user','2026-10-10')")
os._exit(19)
"""
    result = subprocess.run([sys.executable, "-c", script, str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 19, result.stderr
    recovered = SQLiteStore(path)
    try:
        assert [row["text"] for row in recovered.messages()] == ["已提交"]
        assert recovered.events()[0]["summary"] == "已提交事件"
        assert recovered.memories()[0]["content"] == "已提交記憶"
        assert len(recovered.load_characters()) == 13
        assert recovered.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        recovered.close()


def test_worldline_scoping_and_cross_world_memory_reference_rejection(store):
    main_event = store.add_event("主線事件")
    store.add_memory("主線記憶", character_id="shogo", source_event_id=main_event)
    store.add_event("AU 事件", "glasshouse")
    store.add_memory("AU 記憶", "glasshouse", "shogo")
    with pytest.raises(StorageError, match="同一世界線"):
        store.add_memory("錯誤跨界引用", "glasshouse", "shogo", main_event)
    assert [row["content"] for row in store.memories()] == ["主線記憶"]
    assert [row["content"] for row in store.memories("glasshouse")] == ["AU 記憶"]


def test_backup_live_wal_and_confirmed_restore_include_safety_backup(store, tmp_path):
    message(store, "備份時的聊天")
    event = store.add_event("備份時的事件")
    store.add_memory("備份時的記憶", source_event_id=event)
    store.set_active_worldline("glasshouse")
    snapshot = store.backup(tmp_path / "snapshot.sqlite3")
    message(store, "備份後的聊天")
    with pytest.raises(StorageError, match="確認"):
        store.restore(snapshot)
    assert len(store.messages()) == 2
    safety = store.restore(snapshot, confirmed=True)
    assert safety.is_file()
    assert [row["text"] for row in store.messages()] == ["備份時的聊天"]
    assert store.settings()["active_worldline"] == "glasshouse"
    assert store.events()[0]["summary"] == "備份時的事件"
    assert store.memories()[0]["content"] == "備份時的記憶"
    assert len(store.load_characters()) == 13
    recovered_before_restore = SQLiteStore(safety)
    try:
        assert len(recovered_before_restore.messages()) == 2
    finally:
        recovered_before_restore.close()


def test_backup_will_not_overwrite_existing_file_or_live_database(store, tmp_path):
    destination = tmp_path / "existing.sqlite3"
    destination.write_bytes(b"keep this user file")
    with pytest.raises(StorageError, match="已存在"):
        store.backup(destination)
    assert destination.read_bytes() == b"keep this user file"
    with pytest.raises(StorageError, match="正在使用"):
        store.backup(store.path)


@pytest.mark.parametrize("kind", ["corrupt", "wrong_app", "newer_schema"])
def test_invalid_restore_cannot_change_current_data(store, tmp_path, kind):
    message(store, "目前資料保留")
    destination = tmp_path / f"bad-{kind}.sqlite3"
    if kind == "corrupt":
        destination.write_bytes(b"not sqlite")
    elif kind == "wrong_app":
        connection = sqlite3.connect(destination)
        connection.execute("CREATE TABLE unrelated(x TEXT)")
        connection.close()
    else:
        store.backup(destination)
        connection = sqlite3.connect(destination)
        connection.execute("PRAGMA user_version=999")
        connection.close()
    with pytest.raises((StorageError, sqlite3.DatabaseError)):
        store.restore(destination, confirmed=True)
    assert store.messages()[0]["text"] == "目前資料保留"
    assert not (store.path.parent / "backups").exists()


def test_corrupt_existing_database_is_not_reset(tmp_path):
    path = tmp_path / "apartment.sqlite3"
    original = b"corrupt original data to preserve"
    path.write_bytes(original)
    with pytest.raises(sqlite3.DatabaseError):
        SQLiteStore(path)
    assert path.read_bytes() == original


def test_clear_requires_confirmation_and_keeps_other_data(store):
    message(store)
    message(store, worldline="glasshouse")
    store.add_event("事件保留")
    store.add_memory("記憶保留")
    with pytest.raises(StorageError, match="確認"):
        store.clear_messages("main")
    assert len(store.messages()) == 1
    before = store.load_characters()
    store.clear_messages("main", confirmed=True)
    assert store.messages() == []
    assert len(store.messages("glasshouse")) == 1
    assert store.load_characters() == before
    assert store.events()[0]["summary"] == "事件保留"
    assert store.memories()[0]["content"] == "記憶保留"


def test_relationship_changes_require_confirmation(store):
    before = store.load_characters()
    with pytest.raises(StorageError, match="確認"):
        store.set_relationship("shogo", "main", {"status": "未確認交往"})
    assert store.load_characters() == before


def test_confirmed_order_supplement_preserves_other_fields_and_custom_tone(store):
    from dataclasses import asdict, replace
    before = {c.id: c for c in store.load_characters()}
    old = replace(before['order'], tone='使用者自訂的既有語氣', behavior=('既有行為不可丟失',))
    store.save_character(old)
    safety = store.apply_confirmed_order_speech_update()
    assert safety.is_file()
    after = {c.id: c for c in store.load_characters()}
    assert after['order'].tone.startswith(old.tone)
    assert after['order'].behavior[0] == old.behavior[0]
    for key in asdict(old).keys() - {'tone', 'behavior'}:
        assert asdict(after['order'])[key] == asdict(old)[key]
    assert all(after[key] == before[key] for key in before if key != 'order')
    assert store.apply_confirmed_order_speech_update() is None


def test_confirmed_shogo_update_migrates_existing_data_once_without_losing_personality(store):
    from dataclasses import replace, asdict
    profiles = store.load_characters()
    old = next(c for c in profiles if c.id=='shogo')
    original_behavior = tuple(b for b in old.behavior if b not in ('平常稱呼使用者為「月」。', '喜歡主動表達愛意；維持原作人格與獨立思考，依各世界線已確認關係互動，不以愛意擅自推進或改寫關係狀態。', '喜歡看使用者的各種反應；當使用者真的表現不喜歡時，會停下當下的互動。', '以柔和的自然日文常體說話，不使用敬語；日文自稱「僕」，不套用 REO 的全敬語模式。', '語氣柔和不等於溫柔；仍會有點壞心眼，不改成一味體貼、順從的性格。', '人格維持《PSYCHO-PASS》第一季槙島聖護；在已確認戀人的世界線，對使用者的對待與其他人有差別，但不因戀愛改成另一種人格。'))
    legacy = replace(old, user_address='かみそりちゃん', behavior=(*original_behavior,'自訂既有行為'), tone='既有自訂語氣。', self_reference=None, language_rules={**old.language_rules, 'register':'依角色語氣'})
    store.save_character(legacy)
    store.append_message(speaker_id='shogo', speaker_name='聖護', text='小鳥ちゃん', source_kind='ai')
    store.add_memory('正式約會記憶', character_id='shogo')
    before_messages, before_memories = store.messages(), store.memories()
    safety = store.apply_confirmed_shogo_address_update()
    assert safety and safety.is_file()
    updated = next(c for c in store.load_characters() if c.id=='shogo')
    a,b = asdict(legacy),asdict(updated)
    assert {key for key in a if a[key]!=b[key]} == {'user_address','behavior','tone','language_rules','self_reference'}
    assert updated.user_address=='月' and '自訂既有行為' in updated.behavior
    assert any('喜歡主動表達愛意' in b for b in updated.behavior)
    assert '喜歡看使用者的各種反應；當使用者真的表現不喜歡時，會停下當下的互動。' in updated.behavior
    assert '以柔和的自然日文常體說話，不使用敬語；日文自稱「僕」，不套用 REO 的全敬語模式。' in updated.behavior
    assert '語氣柔和不等於溫柔；仍會有點壞心眼，不改成一味體貼、順從的性格。' in updated.behavior
    assert updated.behavior[-1]=='人格維持《PSYCHO-PASS》第一季槙島聖護；在已確認戀人的世界線，對使用者的對待與其他人有差別，但不因戀愛改成另一種人格。'
    assert updated.tone.startswith(legacy.tone) and updated.language_rules['register']=='柔和的自然日文常體；不使用敬語'
    assert updated.source_text==legacy.source_text and updated.relationships==legacy.relationships
    assert [c for c in store.load_characters() if c.id!='shogo']==[c for c in profiles if c.id!='shogo']
    assert store.messages()==before_messages and store.memories()==before_memories
    backup = SQLiteStore(safety)
    try:
        assert next(c for c in backup.load_characters() if c.id=='shogo').user_address=='かみそりちゃん'
    finally:
        backup.close()
    store.save_character(replace(updated, user_address='使用者後續自訂'))
    assert store.apply_confirmed_shogo_address_update() is None
    assert next(c for c in store.load_characters() if c.id=='shogo').user_address=='使用者後續自訂'


def test_shogo_register_update_applies_after_previous_044_address_marker(store):
    shogo = next(c for c in store.load_characters() if c.id=='shogo')
    legacy = replace(shogo, behavior=shogo.behavior[:-3], self_reference=None,
                     tone='自然日文，從容。', language_rules={**shogo.language_rules, 'register':'依角色語氣'})
    store.save_character(legacy)
    with store.connection:
        store.connection.execute('INSERT INTO settings VALUES (?,?)',
                                 ('confirmed:shogo-address-affection-2026-10-11', '{}'))
    safety = store.apply_confirmed_shogo_address_update()
    assert safety and safety.is_file()
    current = next(c for c in store.load_characters() if c.id=='shogo')
    assert current.user_address=='月' and current.self_reference=='僕'
    assert '不使用敬語' in current.language_rules['register']
    assert current.behavior==shogo.behavior
    assert current.source_text==legacy.source_text and current.relationships==legacy.relationships
    assert store.apply_confirmed_shogo_address_update() is None
