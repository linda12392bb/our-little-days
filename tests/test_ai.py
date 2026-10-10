"""No external API calls. Verify charging gates, persistence and actual UI orchestration."""
from dataclasses import asdict, replace
import json
import sqlite3
import subprocess
import sys
import time

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from our_little_days.ai_cost import AIConfig, AIError, UsageLedger, cost_units
from our_little_days.ai_engine import ChangeGate, build_payload, save_turn, validate_turn
from our_little_days.capture import DemoBackend, UnsupportedBackend
from our_little_days.models import load_characters
from our_little_days.ui import MainWindow


def image(color='white'):
    value = QImage(640, 480, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    return value


def turn():
    return {'meaningful': True, 'event_key': 'Chrome:新事件一', 'summary': '画面出现新段落', 'important': True,
            'messages': [
                {'speaker_id': 'order', 'recipient_ids': ['rixo'], 'reply_to_index': None, 'segments': [{'kind': 'speech', 'text': 'ほんま、気になるんやろ♡', 'translation': '其實，你很在意吧♡'}]},
                {'speaker_id': 'rixo', 'recipient_ids': ['order'], 'reply_to_index': 0, 'segments': [{'kind': 'speech', 'text': 'うるせえ。俺に聞くな。', 'translation': '吵死了。別問我。'}]},
            ]}


def response(value=None):
    return {'id': 'fake-provider-test-only', 'usage': {'prompt_tokens': 100, 'completion_tokens': 120},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value or turn(), ensure_ascii=False)}}]}


def test_all_request_types_share_budget_and_uncertain_charges_persist(tmp_path):
    path = tmp_path / 'usage.sqlite3'
    ledger = UsageLedger(path)
    config = replace(AIConfig(), monthly_budget_twd=1)
    for kind in ('screen_test', 'reply_test', 'screen_event', 'user_reply', 'memory_summary', 'manual_retry'):
        rid, _ = ledger.reserve(config, kind, 1)
        ledger.settle(rid, {'prompt_tokens': 1, 'completion_tokens': 1}, 'mock')
    rid, reserved = ledger.reserve(config, 'screen_event', 1000)
    ledger.close()  # Simulate losing completion: never refund the outstanding amount.
    ledger = UsageLedger(path)
    assert ledger.totals()['month']['requests'] == 7
    assert ledger.totals()['month']['uncertain_twd'] == reserved
    assert ledger.db.execute('SELECT status FROM requests WHERE id=?', (rid,)).fetchone()[0] == 'interrupted'
    with pytest.raises(AIError, match='月預算'):
        ledger.reserve(config, 'manual_retry', 100000)
    assert ledger.totals()['month']['requests'] == 7
    ledger.close()


def test_atomic_budget_across_two_connections_and_price_snapshot(tmp_path):
    a = UsageLedger(tmp_path / 'budget.sqlite3')
    b = UsageLedger(tmp_path / 'budget.sqlite3')
    config = replace(AIConfig(), monthly_budget_twd=.20)
    rid, reserve = a.reserve(config, 'screen_test', 1000)
    with pytest.raises(AIError):
        b.reserve(config, 'reply_test', 1000)
    a.save_config(replace(config, twd_per_usd=50))
    actual = a.settle(rid, {'prompt_tokens': 1000, 'completion_tokens': 100}, 'mock')
    assert actual == cost_units(config, 1000, 100) / 1_000_000
    assert actual < reserve
    assert a.totals()['month']['uncertain_twd'] == 0
    a.close()
    b.close()


def test_invalid_usage_and_overestimate_fail_closed(tmp_path):
    ledger = UsageLedger(tmp_path / 'usage.sqlite3')
    config = AIConfig()
    rid, reserved = ledger.reserve(config, 'reply_test', 100)
    with pytest.raises(AIError):
        ledger.settle(rid, {'prompt_tokens': -1, 'completion_tokens': 100}, 'mock')
    assert ledger.totals()['month']['uncertain_twd'] == reserved
    with pytest.raises(AIError, match='超過預留'):
        ledger.settle(rid, {'prompt_tokens': 1_000_000, 'completion_tokens': 100}, 'mock')
    assert ledger.totals()['month']['twd'] > reserved
    ledger.close()


@pytest.mark.parametrize('field,value', [('monthly_budget_twd', float('nan')), ('input_usd_per_million', 0), ('twd_per_usd', -1), ('model', 'unpriced-unknown')])
def test_invalid_pricing_disallows_requests(field, value):
    with pytest.raises(AIError):
        replace(AIConfig(), **{field: value}).validate()


def test_ten_hours_static_and_clock_cursor_variations_never_trigger(app):
    clock = [0]
    gate = ChangeGate(clock=lambda: clock[0])
    original = image()
    assert not gate.ready(original, 120)
    assert gate.ready(original, 120)
    gate.acknowledge(original)
    for tick in range(6000):  # 10 hours at a 6 second capture interval.
        clock[0] += 6
        changed = original.copy()
        # A moving cursor / clock occupies a small area, below the local threshold.
        for x in range(tick % 500, tick % 500 + 10):
            for y in range(15):
                changed.setPixelColor(x, y, QColor('black'))
        assert not gate.ready(changed, 120)
    new = image('black')
    assert not gate.ready(new, 120)
    assert gate.ready(new, 120)
    gate.acknowledge(new)
    clock[0] += 36000
    assert not gate.ready(new, 120)


def test_payload_preserves_every_source_and_scopes_memory(store, app):
    store.add_memory('只有主線記憶', 'main', 'shogo')
    store.add_memory('不得跨線的記憶', 'glasshouse', 'shogo')
    store.append_message(speaker_id='user', speaker_name='月鹿', text='主線使用者內容')
    payload, upper = build_payload(AIConfig(), store.load_characters(), store, 'main', 'screen_event', image())
    context = json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['characters'] == json.loads(json.dumps([asdict(c) for c in store.load_characters()]))
    assert len(context['characters']) == 13
    assert context['long_term_memories'][0]['content'] == '只有主線記憶'
    assert '不得跨線的記憶' not in json.dumps(context['long_term_memories'], ensure_ascii=False)
    assert upper > 32768
    assert payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')


def test_ai_multi_character_replies_events_and_worldlines_preserve_personas(store):
    before = store.load_characters()
    value = validate_turn(response(), before, 'screen_event')
    records = save_turn(store, 'main', value, before, 'screen_event')
    assert records[1]['reply_to'] == records[0]['id']
    assert json.loads(records[1]['recipient_ids_json']) == ['order']
    assert 'ほんま' in records[0]['text'] and '其實' in records[0]['text']
    assert not save_turn(store, 'main', value, before, 'screen_event')
    assert len(store.events()) == 1 and len(store.messages()) == 2
    assert store.load_characters() == before
    assert not store.memories()  # Unconfirmed screen observations don't invent memories.
    assert len(save_turn(store, 'glasshouse', value, before, 'screen_event')) == 2
    assert len(store.messages('main')) == len(store.messages('glasshouse')) == 2


@pytest.mark.parametrize('change', ['translation', 'speaker', 'forward_reply', 'recipient', 'language'])
def test_bad_ai_output_rejected_before_writing(store, change):
    value = turn()
    if change == 'translation':
        value['messages'][0]['segments'][0]['translation'] = ''
    elif change == 'speaker':
        value['messages'][0]['speaker_id'] = 'invented14'
    elif change == 'forward_reply':
        value['messages'][0]['reply_to_index'] = 1
    elif change == 'recipient':
        value['messages'][1]['recipient_ids'] = ['user']
    else:
        value['messages'][0]['segments'][0]['text'] = '中文替代日文'
    with pytest.raises(AIError):
        validate_turn(response(value), store.load_characters(), 'screen_event')
    assert not store.messages() and not store.events()


def test_quiet_event_is_saved_without_forcing_speakers(store):
    value = turn()
    value['messages'] = []
    validate_turn(response(value), store.load_characters(), 'screen_event')
    assert not save_turn(store, 'main', value, store.load_characters(), 'screen_event')
    assert len(store.events()) == 1


def test_restore_never_rewinds_usage_journal(store, tmp_path):
    ledger = UsageLedger(store.path.parent / 'api-usage.sqlite3')
    snapshot = store.backup(tmp_path / 'memory-backup.sqlite3')
    rid, _ = ledger.reserve(AIConfig(), 'screen_event', 1000)
    ledger.abandon(rid)
    before = ledger.totals()
    store.restore(snapshot, confirmed=True)
    assert ledger.totals() == before
    ledger.close()


def test_single_cost_tests_require_actual_usage_and_key_model_match(tmp_path):
    ledger = UsageLedger(tmp_path / 'usage.sqlite3')
    config = replace(AIConfig(), key_revision='key-version-1')
    for kind in ('screen_test', 'reply_test'):
        rid, _ = ledger.reserve(config, kind, 1000)
        with pytest.raises(AIError):
            ledger.mark_test(config, kind, rid)
        ledger.settle(rid, {'prompt_tokens': 100, 'completion_tokens': 100}, 'mock')
        ledger.mark_test(config, kind, rid)
    assert ledger.tests_passed(config)
    assert not ledger.tests_passed(replace(config, key_revision='new-key'))
    assert not ledger.tests_passed(replace(config, model='gpt-4.1'))
    ledger.close()


def wait(predicate, seconds=3):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate()


def setup_fake(window, monkeypatch):
    import our_little_days.ai_ui as ai_ui
    c = window.ai
    c.config = replace(c.config, transmission_approved=True)
    c.ledger.save_config(c.config)
    monkeypatch.setattr(c.keys, 'read', lambda: 'mock-key-only')
    monkeypatch.setattr(c.keys, 'exists', lambda: True)
    monkeypatch.setattr(QMessageBox, 'information', lambda *args, **kwargs: None)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args, **kwargs: None)
    calls = []
    def fake(key, payload, cancelled):
        calls.append(payload)
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        value = turn()
        if context['task'] == 'screen_test':
            value['messages'] = []
        if context['task'] == 'reply_test':
            value['messages'] = [value['messages'][0]]
            value['messages'][0]['speaker_id'] = context['requested_character']
            value['messages'][0]['recipient_ids'] = ['user']
            value['messages'][0]['reply_to_index'] = None
            value['messages'][0]['reply_to_message_id'] = None
        return response(value)
    monkeypatch.setattr(ai_ui, 'request_api', fake)
    return calls


def test_request_schema_constrains_selected_character_and_real_reply_ids(store):
    characters = store.load_characters()
    before = [asdict(c) for c in characters]
    for character in characters:
        payload, _ = build_payload(AIConfig(), characters, store, 'main', 'reply_test', selected=character.id)
        schema = payload['response_format']['json_schema']['schema']
        messages = schema['properties']['messages']
        fields = messages['items']['properties']
        assert messages['minItems'] == messages['maxItems'] == 1
        assert fields['speaker_id']['enum'] == [character.id]
        assert fields['recipient_ids']['items']['enum'] == ['user']
        assert fields['reply_to_index'] == fields['reply_to_message_id'] == {'type':'null'}
        assert json.loads(payload['messages'][1]['content'][0]['text'])['characters'] == json.loads(json.dumps(before))
    with pytest.raises(AIError, match='選擇一位住戶'):
        build_payload(AIConfig(), characters, store, 'main', 'reply_test', selected='聖護')
    main = store.append_message(speaker_id='user', speaker_name='月鹿', text='原始對話')
    foreign = store.append_message(speaker_id='user', speaker_name='月鹿', text='其他世界線', worldline='glasshouse')
    test = store.append_message(speaker_id='order', speaker_name='序', text='介面測試', source_kind='test')
    payload, _ = build_payload(AIConfig(), characters, store, 'main', 'user_reply')
    fields = payload['response_format']['json_schema']['schema']['properties']['messages']['items']['properties']
    assert set(fields['speaker_id']['enum']) == {c.id for c in characters}
    assert fields['reply_to_message_id']['enum'] == [None, main['id']]
    assert foreign['id'] not in fields['reply_to_message_id']['enum'] and test['id'] not in fields['reply_to_message_id']['enum']
    assert [asdict(c) for c in store.load_characters()] == before


@pytest.mark.parametrize('change,reason', [
    ('speaker', '無效角色 ID'), ('selected', '所選住戶不同'),
    ('recipient', '無效對象 ID'), ('translation', '缺少繁體中文翻譯'),
    ('language', '日文發言'), ('truncated', '輸出 token 上限'),
    ('reference', '直接回覆使用者'), ('json', '無法解析'),
])
def test_failed_manual_button_shows_reason_and_paid_usage_without_retry(app, store, monkeypatch, change, reason):
    import our_little_days.ai_ui as ai_ui
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        calls = setup_fake(window, monkeypatch)
        window.character_combo.setCurrentIndex(next(i for i, c in enumerate(window.characters) if c.id == 'order'))
        warnings, successes = [], []
        monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, text: warnings.append((title,text)))
        monkeypatch.setattr(QMessageBox, 'information', lambda *args: successes.append(args))
        before_profiles = store.load_characters()
        store.add_memory('原始記憶保留')
        original = store.append_message(speaker_id='user', speaker_name='月鹿', text='原始聊天保留')
        before_messages = store.messages()
        def bad(key, payload, cancelled):
            calls.append(payload)
            value = turn()
            value['messages'] = value['messages'][:1]
            m = value['messages'][0]
            m['recipient_ids'] = ['user']
            m['reply_to_message_id'] = None
            if change == 'speaker': m['speaker_id'] = '序'
            if change == 'selected': m['speaker_id'] = 'rixo'
            if change == 'recipient': m['recipient_ids'] = ['月鹿']
            if change == 'translation': m['segments'][0]['translation'] = ''
            if change == 'language': m['segments'][0]['text'] = '中文替代日文'
            if change == 'reference': m['reply_to_message_id'] = original['id']
            result = response(value)
            result['usage'] = {'prompt_tokens':15054, 'completion_tokens':161}
            if change == 'truncated': result['choices'][0]['finish_reason'] = 'length'
            if change == 'json': result['choices'][0]['message']['content'] = '{'
            return result
        monkeypatch.setattr(ai_ui, 'request_api', bad)
        window.ai.pause()
        QTest.mouseClick(window.ai_reply_test, Qt.MouseButton.LeftButton)
        wait(lambda: window.ai.worker is None)
        assert len(calls) == 1 and len(warnings) == 1 and not successes
        assert reason in warnings[0][1]
        assert '15054 token／輸出 161 token' in warnings[0][1]
        assert 'NT$0.2072' in warnings[0][1]
        assert '未標記測試通過' in warnings[0][1]
        assert reason in window.ai_test_note.text()
        totals = window.ai.ledger.totals()['month']
        assert totals['requests'] == 1 and totals['input_tokens'] == 15054 and totals['uncertain_twd'] == 0
        assert not window.ai.ledger.tests_passed(window.ai.config) and not window.ai.enabled
        assert store.messages() == before_messages and store.memories()[0]['content'] == '原始記憶保留'
        assert store.load_characters() == before_profiles
        QTest.qWait(50)
        assert len(calls) == 1
    finally:
        window.close()
        app.processEvents()


def test_manual_preflight_failure_is_visible_and_never_paid(app, store, monkeypatch):
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        calls = setup_fake(window, monkeypatch)
        window.ai.config = replace(window.ai.config, transmission_approved=False)
        warnings = []
        monkeypatch.setattr(QMessageBox, 'warning', lambda parent,title,text: warnings.append(text))
        QTest.mouseClick(window.ai_reply_test, Qt.MouseButton.LeftButton)
        assert not calls and window.ai.ledger.totals()['month']['requests'] == 0
        assert len(warnings) == 1 and '未送出 API 請求' in warnings[0]
        assert '同意傳送' in window.ai_test_note.text()
    finally:
        window.close()
        app.processEvents()


def test_ui_offline_no_calls_then_two_tests_and_one_multispeaker_call(app, store, monkeypatch):
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        calls = setup_fake(window, monkeypatch)
        window.ai.config = replace(window.ai.config, transmission_approved=False)
        window.chat_input.setPlainText('離線也保存')
        window.send_message()
        assert not window.ai.start('reply_test')
        assert not calls
        assert window.ai.ledger.totals()['month']['requests'] == 0
        window.ai.config = replace(window.ai.config, transmission_approved=True)
        window.character_combo.setCurrentIndex(window.character_combo.findData(next(c for c in window.characters if c.id == 'order')))
        assert window.ai.start('screen_test', image=image())
        wait(lambda: window.ai.worker is None)
        assert window.ai.start('reply_test')
        wait(lambda: window.ai.worker is None)
        assert window.ai.ledger.tests_passed(window.ai.config)
        window.ai.enable(True)
        window.chat_input.setPlainText('請互相接話')
        window.send_message()
        wait(lambda: window.ai.worker is None)
        assert len(calls) == 3  # No per-character request fanout.
        ai_messages = [m for m in store.messages() if m['source_kind'] == 'ai']
        assert ai_messages[-1]['reply_to'] == ai_messages[-2]['id']
        assert '剩餘預算' in window.ai_usage.text() and '平台實際帳單' in window.ai_usage.text()
    finally:
        window.close()
        app.processEvents()


def test_pause_cancels_inflight_ai_without_retries_or_memory_loss(app, store, monkeypatch):
    import our_little_days.ai_ui as ai_ui
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        calls = setup_fake(window, monkeypatch)
        started = []
        def slow(key, payload, cancelled):
            started.append(True)
            while not cancelled():
                time.sleep(.01)
            raise AIError('cancelled mock request')
        monkeypatch.setattr(ai_ui, 'request_api', slow)
        store.add_memory('保留記憶')
        assert window.ai.start('reply_test')
        wait(lambda: bool(started))
        window.ai.pause()
        wait(lambda: window.ai.worker is None)
        assert len(started) == 1 and not calls
        assert not window.ai.enabled
        assert store.memories()[0]['content'] == '保留記憶'
        assert window.ai.ledger.totals()['month']['uncertain_twd'] > 0
    finally:
        window.close()
        app.processEvents()


def test_ai_remains_off_after_reopening_and_budget_display_survives(app, store, monkeypatch):
    first = MainWindow(DemoBackend(), store.load_characters(), store)
    rid, _ = first.ai.ledger.reserve(first.ai.config, 'reply_test', 100)
    first.ai.ledger.abandon(rid)
    first.ai.enabled = first.ai.autonomous = True
    amount = first.ai.ledger.totals()['month']['twd']
    first.close()
    app.processEvents()
    second = MainWindow(DemoBackend(), store.load_characters(), store)
    try:
        assert not second.ai.enabled and not second.ai.autonomous
        assert second.ai.ledger.totals()['month']['twd'] == amount
        assert not second.ai_enable.isChecked() and not second.ai_auto.isChecked()
    finally:
        second.close()
        app.processEvents()


def _echo_transport(connection, key, payload):
    connection.send(('ok', {'echo': len(payload['image']), 'has_key': key == 'mock-key-only'}))
    connection.close()


def _hang_transport(connection, key, payload):
    time.sleep(30)


def test_isolated_transport_large_payload_and_fast_cancellation():
    from our_little_days.ai_provider import request_api
    result = request_api('mock-key-only', {'image': 'x' * 400000}, lambda: False, _transport=_echo_transport)
    assert result == {'echo': 400000, 'has_key': True}
    started = time.monotonic()
    with pytest.raises(AIError, match='取消'):
        request_api('mock-key-only', {}, lambda: time.monotonic()-started > .3, _transport=_hang_transport)
    assert time.monotonic() - started < 3


def test_key_storage_has_no_nonwindows_plaintext_fallback(tmp_path):
    from our_little_days.ai_provider import WindowsKeyStore
    if sys.platform == 'win32':
        pytest.skip('Non-Windows fail-closed behavior')
    keys = WindowsKeyStore(tmp_path)
    with pytest.raises(AIError):
        keys.save('mock-key-only')
    assert not list(tmp_path.iterdir())


def test_redirect_never_forwards_authorization():
    from our_little_days.ai_provider import _NoRedirect
    with pytest.raises(AIError):
        _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.example')


def test_actual_controller_static_frames_do_not_schedule_more_calls(app, store, monkeypatch):
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        c = window.ai
        c.enabled = c.autonomous = True
        timeline = [0]
        c.gate = ChangeGate(clock=lambda: timeline[0])
        scheduled = []
        def schedule(kind, image=None):
            scheduled.append(kind)
            c.gate.acknowledge(image)
            return True
        monkeypatch.setattr(c, 'start', schedule)
        frame = image()
        for i in range(6000):
            timeline[0] = i * 6
            c.frame(frame)
        assert scheduled == ['screen_event']  # Initial observation only; ten hours then silent.
    finally:
        window.close()
        app.processEvents()


def test_atomic_ai_turn_rolls_back_event_and_all_speakers_on_write_failure(store):
    store.connection.execute("CREATE TRIGGER fail_rixo BEFORE INSERT ON messages WHEN NEW.speaker_id='rixo' BEGIN SELECT RAISE(ABORT,'mock disk failure'); END")
    with pytest.raises(sqlite3.DatabaseError):
        save_turn(store, 'main', turn(), store.load_characters(), 'screen_event')
    assert not store.events() and not store.messages()


def test_day_and_month_rollover_do_not_forget_history(tmp_path, monkeypatch):
    from datetime import datetime, timezone, timedelta
    import our_little_days.ai_cost as costs
    now = [datetime(2026, 10, 31, 23, 59, tzinfo=timezone(timedelta(hours=8)))]
    monkeypatch.setattr(costs, 'taipei_now', lambda: now[0])
    ledger = UsageLedger(tmp_path / 'usage.sqlite3')
    config = replace(AIConfig(), monthly_budget_twd=.15)
    rid, _ = ledger.reserve(config, 'reply_test', 1)
    with pytest.raises(AIError):
        ledger.reserve(config, 'reply_test', 1)
    now[0] += timedelta(minutes=2)
    assert ledger.totals()['month']['twd'] == 0
    ledger.reserve(config, 'reply_test', 1)
    assert ledger.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0] == 2
    ledger.close()


def test_usage_backup_merge_retains_new_charges_and_does_not_duplicate(tmp_path):
    ledger = UsageLedger(tmp_path / 'usage.sqlite3')
    config = AIConfig()
    first, _ = ledger.reserve(config, 'reply_test', 100)
    backup = tmp_path / 'backup.sqlite3'
    ledger.backup(backup)
    ledger.settle(first, {'prompt_tokens': 10, 'completion_tokens': 10}, 'mock')
    second, _ = ledger.reserve(config, 'screen_event', 1000)
    ledger.abandon(second)
    before = ledger.totals()
    assert ledger.merge_backup(backup) == 0
    assert ledger.totals() == before
    fresh = UsageLedger(tmp_path / 'reinstalled.sqlite3')
    assert fresh.merge_backup(backup) == 1
    assert fresh.merge_backup(backup) == 0
    assert fresh.totals()['month']['uncertain_twd'] > 0
    fresh.close()
    ledger.close()


def test_failed_key_rotation_still_invalidates_old_cost_tests(app, store, monkeypatch):
    from our_little_days.ai_ui import AISettingsDialog
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    try:
        c = window.ai
        c.config = replace(c.config, key_revision='old-version', transmission_approved=True)
        c.ledger.save_config(c.config)
        for kind in ('screen_test', 'reply_test'):
            rid, _ = c.ledger.reserve(c.config, kind, 100)
            c.ledger.settle(rid, {'prompt_tokens': 1, 'completion_tokens': 1}, 'mock')
            c.ledger.mark_test(c.config, kind, rid)
        assert c.ledger.tests_passed(c.config)
        def fail(value):
            raise AIError('mock DPAPI failure')
        monkeypatch.setattr(c.keys, 'save', fail)
        monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: None)
        dialog = AISettingsDialog(c)
        dialog.key.setText('mock-key-rotation')
        dialog.save()
        assert not c.ledger.tests_passed(c.ledger.config())
        assert not c.enabled
        dialog.close()
    finally:
        window.close()
        app.processEvents()
