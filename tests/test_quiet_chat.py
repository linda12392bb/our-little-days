"""Explicit quiet chat, real controller/ledger; fake API only, no live requests."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from our_little_days.ai_cost import AIConfig, AIError, QuietBudgetExhausted, UsageLedger
from our_little_days.ai_engine import build_payload, save_turn, validate_turn
from our_little_days.capture import UnsupportedBackend
from our_little_days.storage import SQLiteStore
from our_little_days.ui import MainWindow


def conversation():
    return {'meaningful': False, 'event_key': '', 'summary': '', 'important': False, 'messages': [
        {'speaker_id': 'order', 'recipient_ids': ['rixo'], 'reply_to_index': None,
         'segments': [{'kind': 'speech', 'text': 'ほんま、気になるんやろ♡', 'translation': '其實，你很在意吧♡'}]},
        {'speaker_id': 'rixo', 'recipient_ids': ['order'], 'reply_to_index': 0,
         'segments': [{'kind': 'speech', 'text': 'うるせえ。俺に聞くな。', 'translation': '吵死了。別問我。'}]},
    ]}


def wait(predicate):
    deadline = time.monotonic()+3
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def varied_conversation(number):
    value = conversation()
    value['messages'][0]['segments'][0]['text'] += f'（{number}）'
    return value


@pytest.fixture
def quiet_window(app, store, monkeypatch):
    import our_little_days.ai_ui as module
    import our_little_days.ai_engine as engine
    import our_little_days.ai_cost as cost
    w = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    c = w.ai
    c.config = replace(c.config, transmission_approved=True, max_output_tokens=2000)
    c.ledger.save_config(c.config)
    for kind in ('screen_test', 'reply_test'):
        rid, _ = c.ledger.reserve(c.config, kind, 100)
        c.ledger.settle(rid, {'prompt_tokens': 100, 'completion_tokens': 100}, 'cost-fixture')
        c.ledger.mark_test(c.config, kind, rid)
    clock = [0]
    c.clock = lambda: clock[0]
    base = datetime.now(timezone(timedelta(hours=8)))
    for target in (module, engine, cost):
        monkeypatch.setattr(target, 'taipei_now', lambda: base+timedelta(seconds=clock[0]))
    c.activity()
    calls = []
    monkeypatch.setattr(c.keys, 'read', lambda: 'mock-key-only')
    monkeypatch.setattr(c.keys, 'exists', lambda: True)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args: None)
    def fake(key, payload, cancelled):
        calls.append(payload)
        return {'id': 'fake', 'usage': {'prompt_tokens': 100, 'completion_tokens': 120},
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(varied_conversation(len(calls)), ensure_ascii=False)}}]}
    monkeypatch.setattr(module, 'request_api', fake)
    try:
        yield w, clock, calls
    finally:
        w.close()
        app.processEvents()


def test_quiet_requires_explicit_enable_then_one_request_for_peer_chat_no_image(quiet_window, store):
    w, clock, calls = quiet_window
    c = w.ai
    assert not c.quiet_enabled and not w.ai_quiet.isEnabled()
    clock[0] += 36000
    assert not c.quiet_tick() and not calls
    c.enable(True)
    assert w.ai_quiet.isEnabled() and not c.quiet_enabled
    c.quiet(True)
    w.frame_image = QImage(64, 64, QImage.Format.Format_RGB32)
    w.frame_image.fill(QColor('white'))
    assert c.quiet_timer.interval() == 180000
    clock[0] += 359
    assert not c.quiet_tick()
    clock[0] += 1
    assert c.quiet_tick()
    wait(lambda: c.worker is None)
    assert len(calls) == 1
    payload = calls[0]
    assert payload['max_completion_tokens'] == 2000
    assert len(payload['messages'][1]['content']) == 1
    context = json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['task'] == 'quiet_chat' and context['current_user_input'] is None
    assert context['characters'] == json.loads(json.dumps([asdict(p) for p in store.load_characters()]))
    records = store.messages()
    assert records[-1]['reply_to'] == records[-2]['id']
    assert json.loads(records[-1]['recipient_ids_json']) == ['order']
    assert not store.events() and not store.memories()
    assert c.ledger.quiet_today() > 0
    assert not c.quiet_tick() and len(calls) == 1


def test_busy_user_typing_voice_preview_and_speech_postpone_quiet(quiet_window):
    w, clock, calls = quiet_window
    c = w.ai
    c.enable(True); c.quiet(True)
    clock[0] += 1800
    w.chat_input.setPlainText('還在打字')
    assert not c.quiet_tick()
    w.chat_input.clear()
    for state in ('pending_text', 'worker', 'speaking'):
        clock[0] += 1800
        if state == 'speaking':
            w.voice.segmenter = SimpleNamespace(speaking=True)
        else:
            setattr(w.voice, state, 'busy')
        assert not c.quiet_tick()
        if state == 'speaking': w.voice.segmenter = None
        else: setattr(w.voice, state, None)
        clock[0] += 60
        assert not c.quiet_tick()
    clock[0] += 1800
    assert c.quiet_tick()
    assert not c.quiet_tick()  # Request underway; no parallel request.
    wait(lambda: c.worker is None)
    assert len(calls) == 1
    c.pause(); clock[0] += 36000
    assert not c.quiet_tick() and len(calls) == 1


def test_quiet_day_cap_blocks_paid_work_but_preserves_voice_and_resumes_next_day(quiet_window, monkeypatch):
    import our_little_days.ai_ui as ui
    import our_little_days.ai_cost as cost
    w, clock, calls = quiet_window
    c = w.ai
    c.config = replace(c.config, quiet_daily_budget_twd=.01)
    c.enable(True); c.quiet(True); clock[0] += 1800
    assert not c.quiet_tick()
    assert c.enabled and c.quiet_enabled and c.quiet_blocked_day
    assert not calls and c.ledger.quiet_today() == 0
    clock[0] += 3600
    assert not c.quiet_tick()
    assert c.user_sent(transient_text='不是吧？', user_message_id=None)
    wait(lambda: c.worker is None)
    assert len(calls) == 1
    tomorrow = datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)
    monkeypatch.setattr(ui, 'taipei_now', lambda: tomorrow)
    monkeypatch.setattr(cost, 'taipei_now', lambda: tomorrow)
    c.config = replace(c.config, quiet_daily_budget_twd=10)
    clock[0] += 1800
    assert c.quiet_tick()
    wait(lambda: c.worker is None)
    assert len(calls) == 2


def test_month_cap_stops_quiet_and_all_ai_without_api_call(quiet_window):
    w, clock, calls = quiet_window
    c = w.ai
    c.config = replace(c.config, monthly_budget_twd=.01)
    c.enable(True); c.quiet(True); clock[0] += 1800
    assert not c.quiet_tick()
    assert not c.enabled and not c.quiet_enabled and not calls
    assert '月預算' in c.last_note


def test_ten_hours_quiet_has_at_most_one_hundred_shared_batches_and_restart_is_off(quiet_window, store, app):
    w, clock, calls = quiet_window
    c = w.ai
    c.enable(True); c.quiet(True)
    for _ in range(200):
        clock[0] += 180
        if c.quiet_tick():
            wait(lambda: c.worker is None)
        assert not c.quiet_tick()
    assert len(calls) == 100 and c.ledger.quiet_today() <= 10
    before = store.messages()
    profiles = store.load_characters()
    assert len([m for m in before if m['source_kind'] == 'ai']) == 200
    snapshot = store.backup(store.path.parent/'quiet-backup.sqlite3')
    w.close(); app.processEvents()
    reopened = SQLiteStore(store.path)
    fresh = MainWindow(UnsupportedBackend(), reopened.load_characters(), reopened)
    try:
        assert not fresh.ai.enabled and not fresh.ai.quiet_enabled
        assert not fresh.timer.isActive() and not fresh.voice.source
        assert reopened.messages() == before and reopened.load_characters() == profiles
        reopened.restore(snapshot, confirmed=True)
        assert reopened.messages() == before
        assert fresh.ai.ledger.quiet_today() > 0  # Restore does not erase costs.
    finally:
        fresh.close(); reopened.close(); app.processEvents()


def test_quiet_can_choose_silence_but_still_accounts_usage_and_waits(quiet_window, monkeypatch):
    import our_little_days.ai_ui as module
    w, clock, calls = quiet_window
    c = w.ai
    def silent(key, payload, cancelled):
        calls.append(payload)
        value = conversation(); value['messages'] = []
        return {'id': 'fake', 'usage': {'prompt_tokens': 100, 'completion_tokens': 50},
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}
    monkeypatch.setattr(module, 'request_api', silent)
    c.enable(True); c.quiet(True); clock[0] += 1800
    assert c.quiet_tick()
    wait(lambda: c.worker is None)
    assert len(calls) == 1 and c.ledger.quiet_today() > 0
    assert not [m for m in w.store.messages() if m['source_kind']=='ai'] and '仍計費' in c.last_note
    assert not c.quiet_tick()


def test_quiet_cancel_inflight_keeps_voice_enabled_no_retry(quiet_window, monkeypatch):
    import our_little_days.ai_ui as module
    w, clock, calls = quiet_window
    c = w.ai
    def slow(key, payload, cancelled):
        calls.append(payload)
        while not cancelled(): time.sleep(.01)
        raise AIError('cancelled mock')
    monkeypatch.setattr(module, 'request_api', slow)
    c.enable(True); c.quiet(True); clock[0] += 1800
    assert c.quiet_tick()
    wait(lambda: bool(calls))
    c.quiet(False)
    wait(lambda: c.worker is None)
    assert c.enabled and not c.quiet_enabled
    assert c.ledger.totals()['month']['uncertain_twd'] > 0
    clock[0] += 36000
    assert not c.quiet_tick() and len(calls) == 1


def test_new_quiet_preferences_keep_existing_cost_fingerprint_and_validate(tmp_path):
    config = AIConfig()
    old = asdict(config)
    for key in ('monthly_budget_twd', 'minimum_interval_seconds', 'transmission_approved', 'quiet_interval_minutes', 'quiet_daily_budget_twd'):
        old.pop(key)
    assert config.fingerprint == hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
    assert replace(config, quiet_interval_minutes=12, quiet_daily_budget_twd=4).fingerprint == config.fingerprint
    for value in (0, 31, float('nan')):
        with pytest.raises(AIError): replace(config, quiet_interval_minutes=value).validate()
    with pytest.raises(AIError): replace(config, quiet_daily_budget_twd=float('nan')).validate()


def test_atomic_quiet_daily_reservations_share_month_budget_across_connections(tmp_path):
    a = UsageLedger(tmp_path/'budget.sqlite3'); b = UsageLedger(tmp_path/'budget.sqlite3')
    try:
        config = replace(AIConfig(), quiet_daily_budget_twd=.15)
        a.reserve(config, 'quiet_chat', 1000)
        with pytest.raises(QuietBudgetExhausted): b.reserve(config, 'quiet_test', 1000)
        assert a.quiet_today() == a.totals()['month']['uncertain_twd']
        b.reserve(config, 'user_reply', 1000)
        assert a.totals()['month']['requests'] == 2
    finally:
        a.close(); b.close()


def result(value):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}


def test_cross_batch_reply_remembers_old_quotes_beyond_recent_chat_and_is_world_scoped(store):
    profiles = store.load_characters()
    first = conversation()
    first['messages'] = [{'speaker_id': 'light', 'recipient_ids': ['order'], 'reply_to_index': None,
                          'segments': [{'kind': 'speech', 'text': '序，你的反擊還在排隊嗎？', 'translation': ''}]}]
    original = save_turn(store, 'main', first, profiles, 'quiet_chat')[0]
    for index in range(70):
        store.append_message(speaker_id='user', speaker_name='月鹿', text=f'後續訊息 {index}')
    payload, _ = build_payload(AIConfig(), profiles, store, 'main', 'quiet_chat')
    context = json.loads(payload['messages'][1]['content'][0]['text'])
    assert original['id'] not in {m['id'] for m in context['recent_chat']}
    assert original in context['ongoing_conversation']['anchor_messages']
    comeback = conversation()
    comeback['messages'][0]['recipient_ids'] = ['light']
    comeback['messages'][0]['reply_to_message_id'] = original['id']
    valid = validate_turn(result(comeback), profiles, 'quiet_chat', store=store)
    records = save_turn(store, 'main', valid, profiles, 'quiet_chat')
    assert records[0]['reply_to'] == original['id']
    assert records[1]['reply_to'] == records[0]['id']
    assert store.conversation_state('main')['last_message_id'] == records[-1]['id']
    assert not store.conversation_context('glasshouse')['anchor_messages']
    with pytest.raises(AIError):
        validate_turn(result(comeback), profiles, 'quiet_chat', store=store, worldline='glasshouse')
    before = store.messages('glasshouse')
    with pytest.raises(AIError): save_turn(store, 'glasshouse', comeback, profiles, 'quiet_chat')
    assert store.messages('glasshouse') == before and store.load_characters() == profiles
    snapshot = store.backup(store.path.parent/'continuity.sqlite3')
    state = store.conversation_state()
    store.restore(snapshot, confirmed=True)
    assert store.conversation_state() == state


def test_duplicate_quiet_batch_does_not_repeat_messages_and_backoff_survives_restart(store):
    profiles = store.load_characters()
    assert len(save_turn(store, 'main', conversation(), profiles, 'quiet_chat')) == 2
    before = store.messages()
    assert not save_turn(store, 'main', conversation(), profiles, 'quiet_chat')
    assert store.messages() == before
    state = store.conversation_state()
    assert state['silent_batches'] == 1 and state['next_quiet_at']
    with SQLiteStore(store.path).connection as connection:
        persisted = json.loads(connection.execute("SELECT value FROM settings WHERE key='conversation:main'").fetchone()[0])
        assert persisted == state


def test_quiet_screen_event_priority_and_no_api_on_three_minute_check(quiet_window):
    w, clock, calls = quiet_window
    c = w.ai
    c.enable(True); c.quiet(True)
    clock[0] += 180
    assert not c.quiet_tick() and not calls
    c.autonomous = True
    w.frame_image = QImage(64, 64, QImage.Format.Format_RGB32)
    w.frame_image.fill(QColor('white'))
    clock[0] += 180
    assert not c.quiet_tick() and not calls  # Local stability check; no paid quiet call.
    assert c.quiet_tick()
    wait(lambda: c.worker is None)
    context = json.loads(calls[0]['messages'][1]['content'][0]['text'])
    assert context['task'] == 'screen_event'


def test_old_output_setting_upgrades_to_2000_without_erasing_usage(tmp_path):
    ledger = UsageLedger(tmp_path/'usage.sqlite3')
    old = asdict(AIConfig()); old['max_output_tokens'] = 2500
    ledger.db.execute("INSERT INTO config VALUES('ai',?)", (json.dumps(old),))
    ledger.db.commit()
    rid, _ = ledger.reserve(AIConfig(), 'user_reply', 100)
    ledger.settle(rid, {'prompt_tokens': 100, 'completion_tokens': 100}, 'old-fixture')
    totals = ledger.totals()
    assert ledger.config().max_output_tokens == 2000
    assert ledger.totals() == totals
    ledger.close()
