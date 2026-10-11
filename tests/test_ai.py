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
from our_little_days.ai_engine import ChangeGate, build_payload, reply_quality_notes, save_turn, validate_turn
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
        if context['task'] == 'user_reply':
            value['messages'][0]['recipient_ids'] = ['user']
            value['messages'][1]['recipient_ids'] = ['order']
            for message in value['messages']:
                message['reply_to_index'] = message['reply_to_message_id'] = None
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
    payload, _ = build_payload(AIConfig(), characters, store, 'main', 'quiet_chat')
    variants = payload['response_format']['json_schema']['schema']['properties']['messages']['anyOf'][0]['items']['anyOf']
    fields = variants[0]['properties']
    historical = variants[1]['properties']
    assert set(fields['speaker_id']['enum']) == {c.id for c in characters}
    assert fields['reply_to_message_id'] == {'type':'null'}
    assert historical['reply_to_message_id']['enum'] == [main['id']]
    assert historical['recipient_ids']['items']['enum'] == ['user']
    assert foreign['id'] not in historical['reply_to_message_id']['enum'] and test['id'] not in historical['reply_to_message_id']['enum']
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


def test_user_reply_schema_uses_local_references_and_requires_a_reply(store):
    profiles = store.load_characters()
    store.append_message(speaker_id='order', speaker_name='序', text='ほんま♡')
    for input_kind in ('typed_text', 'voice_transcript'):
        current = {'kind':input_kind, 'text':'晚安', 'saved':False}
        payload, _ = build_payload(AIConfig(), profiles, store, 'main', 'user_reply', current_input=current)
        schema = payload['response_format']['json_schema']['schema']['properties']['messages']
        assert schema['anyOf'][0]['minItems'] == 1
        fields = schema['anyOf'][0]['items']['properties']
        assert fields['reply_to_index'] == fields['reply_to_message_id'] == {'type':'null'}
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        assert context['current_user_input'] == current and len(context['characters']) == 13
    empty = turn()
    empty['messages'] = []
    with pytest.raises(AIError, match='訊息數量'):
        validate_turn(response(empty), profiles, 'user_reply', store=store)


def test_historical_reference_schema_couples_ids_to_actual_speakers(store):
    profiles = store.load_characters()
    a = store.append_message(speaker_id='light', speaker_name='光', text='序，換你接話')
    b = store.append_message(speaker_id='order', speaker_name='序', text='ほんま♡')
    foreign = store.append_message(speaker_id='light', speaker_name='光', text='AU', worldline='glasshouse')
    payload, _ = build_payload(AIConfig(), profiles, store, 'main', 'quiet_chat')
    branches = payload['response_format']['json_schema']['schema']['properties']['messages']['anyOf'][0]['items']['anyOf']
    pairs = {}
    for branch in branches[1:]:
        fields = branch['properties']
        assert fields['recipient_ids']['minItems'] == fields['recipient_ids']['maxItems'] == 1
        author, = fields['recipient_ids']['items']['enum']
        pairs.update({message_id:author for message_id in fields['reply_to_message_id']['enum']})
    assert pairs == {a['id']:'light', b['id']:'order'} and foreign['id'] not in pairs
    value = turn()
    value.update(meaningful=False, event_key='', summary='', important=False)
    value['messages'] = [value['messages'][0]]
    value['messages'][0].update(recipient_ids=['light'], reply_to_message_id=b['id'])
    before = store.messages()
    with pytest.raises(AIError, match='說話者不一致'):
        validate_turn(response(value), profiles, 'quiet_chat', store=store)
    assert store.messages() == before
    value['messages'][0]['reply_to_message_id'] = a['id']
    valid = validate_turn(response(value), profiles, 'quiet_chat', store=store)
    assert save_turn(store, 'main', valid, profiles, 'quiet_chat')[0]['reply_to'] == a['id']


def test_typing_while_quiet_enabled_pins_current_input_during_inflight_request(app, store, monkeypatch):
    import our_little_days.ai_ui as ai_ui
    import threading
    window = MainWindow(UnsupportedBackend(), store.load_characters(), store)
    release = threading.Event()
    try:
        calls = setup_fake(window, monkeypatch)
        c = window.ai
        for kind in ('screen_test', 'reply_test'):
            rid, _ = c.ledger.reserve(c.config, kind, 100)
            c.ledger.settle(rid, {'prompt_tokens':100,'completion_tokens':100}, 'mock')
            c.ledger.mark_test(c.config, kind, rid)
        c.enable(True)
        c.quiet_enabled = True
        c.refresh()
        def delayed(key, payload, cancelled):
            calls.append(payload)
            release.wait(3)
            value = turn()
            value['messages'][0]['recipient_ids'] = ['user']
            value['messages'][1]['recipient_ids'] = ['order']
            for message in value['messages']:
                message['reply_to_index'] = message['reply_to_message_id'] = None
            return response(value)
        monkeypatch.setattr(ai_ui, 'request_api', delayed)
        window.chat_input.setPlainText('晚安')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        first_id = store.messages()[-1]['id']
        wait(lambda: len(calls) == 1)
        window.chat_input.setPlainText('我還在')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        second_id = store.messages()[-1]['id']
        assert first_id != second_id
        release.set()
        wait(lambda: len(calls) == 2 and c.worker is None)
        replies = [row for row in store.messages() if row['source_kind'] == 'ai']
        assert replies[0]['reply_to'] == first_id and replies[2]['reply_to'] == second_id
        assert replies[1]['reply_to'] == replies[0]['id'] and replies[3]['reply_to'] == replies[2]['id']
        inputs = [json.loads(p['messages'][1]['content'][0]['text'])['current_user_input'] for p in calls]
        assert inputs == [{'kind':'typed_text','text':text,'saved':True} for text in ('晚安', '我還在')]
        assert c.enabled and c.quiet_enabled and window.ai_enable.isChecked() and window.ai_quiet.isChecked()
        assert c.ledger.tests_passed(c.config)
    finally:
        release.set()
        window.close()
        app.processEvents()


def test_each_saved_address_and_voice_style_remains_independent_of_past_errors(store):
    profiles = store.load_characters()
    before = [asdict(c) for c in profiles]
    store.append_message(speaker_id='shogo', speaker_name='聖護', text='はい、小鳥ちゃん、確かに見えております。', source_kind='ai')
    payload, _ = build_payload(AIConfig(), profiles, store, 'main', 'user_reply', current_input={'kind':'typed_text','text':'晚安','saved':True})
    context = json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['character_address_rules'] == {c.id:c.user_address for c in profiles if c.user_address}
    assert context['character_address_rules']['shogo'] == '月'
    assert context['character_address_rules']['reo'] == '小鳥ちゃん'
    assert 'light' not in context['character_address_rules']  # No invented nickname.
    instructions = payload['messages'][0]['content']
    assert '不隨前一位角色繼承' in instructions and '不沿用這些錯誤' in instructions
    assert '聖護使用自然日文，不套用 REO' in instructions
    assert 'はい、小鳥ちゃん' in context['recent_chat'][0]['text']  # History retained verbatim.
    assert context['characters'] == json.loads(json.dumps(before))
    assert [asdict(c) for c in store.load_characters()] == before


@pytest.mark.parametrize('kind,with_image', [('user_reply',False), ('user_reply',True), ('reply_test',False), ('quiet_chat',False), ('screen_event',True)])
def test_visual_evidence_describes_actual_payload_not_historical_observations(store, app, kind, with_image):
    store.add_event('過去看見遊戲選單', importance='screen_observation')
    payload, _ = build_payload(AIConfig(), store.load_characters(), store, 'main', kind,
                              image=image() if with_image else None, selected='shogo' if kind=='reply_test' else None)
    context = json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['visual_evidence'] == {'image_attached':with_image, 'scope':'one_selected_window_image' if with_image else 'no_current_image'}
    actual_images = [part for part in payload['messages'][1]['content'] if part['type']=='image_url']
    assert bool(actual_images) == with_image
    instructions = payload['messages'][0]['content']
    assert ('確實附有一張指定視窗圖片' if with_image else '沒有附上圖片；不得聲稱看見目前畫面') in instructions
    assert '不能當成現在畫面的證據' in instructions
    assert context['recent_screen_events'][0]['summary']=='過去看見遊戲選單'


def test_final_generation_review_uses_current_saved_shogo_after_full_history(store):
    from dataclasses import replace
    profiles=store.load_characters()
    shogo=next(c for c in profiles if c.id=='shogo')
    for _ in range(65):
        store.append_message(speaker_id='shogo',speaker_name='聖護',text='はい、小鳥ちゃん。',source_kind='ai')
    current=replace(shogo,user_address='月',tone='自然日文常體，較少使用敬語。')
    store.save_character(current)
    payload,upper=build_payload(AIConfig(),store.load_characters(),store,'main','user_reply')
    assert [m['role'] for m in payload['messages']]==['system','user','system','user']
    review=payload['messages'][2]['content']
    assert '"user_address": "月"' in review and '自然日文常體' in review
    assert '各 speaker_id 只使用自己那列' in review and '小鳥ちゃん' in review
    assert '常体を使い、丁寧語・敬語で話さない' in review
    assert '喜歡主動表達愛意' in review and '真的表現不喜歡' in review
    assert '"self_reference": "僕"' in review and '柔和不等於溫柔' in review
    assert '第一季槙島聖護' in review and '已確認戀人的世界線' in review
    assert upper==len(json.dumps(payload,ensure_ascii=False).encode())+2048
    context=json.loads(payload['messages'][1]['content'][0]['text'])
    assert len(context['characters'])==13 and len(context['recent_chat'])==60
    assert len(store.messages())==65 and current.source_text==shogo.source_text
    assert all(row['voice_note']['do_not_imitate_voice'] for row in context['recent_chat'])
    assert all('voice_note' not in row for row in store.messages())


def test_successful_request_diagnostic_matches_prepared_profile_without_keys_or_chat(app,store,monkeypatch):
    window=MainWindow(UnsupportedBackend(),store.load_characters(),store)
    try:
        calls=setup_fake(window,monkeypatch)
        assert window.ai.start('reply_test')
        wait(lambda:window.ai.worker is None)
        diagnostic=store.persona_preparation('main')
        actual=json.loads(calls[0]['messages'][1]['content'][0]['text'])
        assert diagnostic['characters']['shogo']['user_address']==actual['character_address_rules']['shogo']=='月'
        assert len(diagnostic['characters'])==13 and diagnostic['model']=='gpt-4.1-mini'
        assert 'mock-key-only' not in json.dumps(diagnostic) and 'recent_chat' not in diagnostic
        assert not store.persona_preparation('glasshouse')
        reopened=__import__('our_little_days.storage',fromlist=['SQLiteStore']).SQLiteStore(store.path)
        try:assert reopened.persona_preparation('main')==diagnostic
        finally:reopened.close()
    finally:
        window.close();app.processEvents()


def test_current_input_is_last_message_without_losing_history_or_all_voices(store):
    old=store.append_message(speaker_id='shogo',speaker_name='聖護',text='はい、小鳥ちゃん、こちらの画面は確かに見えております。',source_kind='ai')
    store.append_message(speaker_id='user',speaker_name='月鹿',text='舊輸入')
    transient={'kind':'voice_transcript','text':'月って呼んで','saved':False}
    payload,upper=build_payload(AIConfig(),store.load_characters(),store,'main','user_reply',current_input=transient)
    context=json.loads(payload['messages'][1]['content'][0]['text'])
    focus=json.loads(payload['messages'][-1]['content'])
    assert focus['current_user_input']==transient and not focus['visual_evidence']['image_attached']
    assert context['recent_chat'][0]['id']==old['id'] and context['recent_chat'][0]['text']==old['text']
    assert context['recent_chat'][0]['voice_note']['preserve_event_and_reply_chain']
    assert len(context['characters'])==13 and upper==len(json.dumps(payload,ensure_ascii=False).encode())+2048
    assert 'voice_note' not in store.messages()[0]
    payload,_=build_payload(AIConfig(),store.load_characters(),store,'main','user_reply')
    assert json.loads(payload['messages'][-1]['content'])['current_user_input']['text']=='舊輸入'
    payload,_=build_payload(AIConfig(),store.load_characters(),store,'main','quiet_chat')
    assert json.loads(payload['messages'][-1]['content'])['current_user_input'] is None


def residents_turn(profiles):
    messages=[]
    for i,c in enumerate(profiles):
        native='はい。' if c.language.startswith('ja') else 'Here.' if c.language=='en' else '在這裡。'
        messages.append({'speaker_id':c.id,'recipient_ids':['user' if i==0 else profiles[i-1].id],
                         'reply_to_index':None,'reply_to_message_id':None,
                         'segments':[{'kind':'speech','text':native,'translation':'在這裡。' if c.translation_required else ''}]})
    return {'meaningful':False,'event_key':'','summary':'','important':False,'read_character_ids':[],'messages':messages}


@pytest.mark.parametrize('kind',['user_reply','quiet_chat','screen_event'])
def test_output_envelope_allows_thirteen_with_shorter_many_speaker_branches(store,app,kind):
    profiles=store.load_characters();before=[asdict(c) for c in profiles]
    payload,_=build_payload(AIConfig(),profiles,store,'main',kind,image=image() if kind=='screen_event' else None)
    arrays=payload['response_format']['json_schema']['schema']['properties']['messages']['anyOf']
    assert [b['maxItems'] for b in arrays]==[3,7,13]
    assert [b['minItems'] for b in arrays]==[1 if kind=='user_reply' else 0,4,8]
    caps=[]
    for b in arrays:
        variants=b['items'].get('anyOf',[b['items']])
        for item in variants:
            fields=item['properties']
            assert fields['speaker_id']['enum']==[c.id for c in profiles]
            assert fields['reply_to_index']=={'type':'null'}
        seg=variants[0]['properties']['segments']
        caps.append((seg['maxItems'],seg['items']['properties']['text']['maxLength'],seg['items']['properties']['translation']['maxLength']))
    assert caps==[(2,64,80),(1,32,40),(1,16,20)]
    assert payload['max_completion_tokens']==2000
    assert json.loads(payload['messages'][-1]['content'])['output_plan']['max_messages']==13
    assert json.loads(payload['messages'][1]['content'][0]['text'])['characters']==json.loads(json.dumps(before))
    assert [asdict(c) for c in store.load_characters()]==before


def test_thirteen_speakers_share_one_reply_batch_and_preserve_all_peer_links(store):
    profiles=store.load_characters()
    user=store.append_message(speaker_id='user',speaker_name='月鹿',text='大家在嗎')
    value=residents_turn(profiles)
    raw=response(value);raw['usage']['completion_tokens']=1500
    parsed=validate_turn(raw,profiles,'user_reply',store=store)
    rows=save_turn(store,'main',parsed,profiles,'user_reply',user['id'],image_attached=False)
    assert len(rows)==13 and len({r['speaker_id'] for r in rows})==13
    assert rows[0]['reply_to']==user['id']
    assert all(rows[i]['reply_to']==rows[i-1]['id'] for i in range(1,13))
    assert len(store.roster_status()['replied'])==13
    assert len(store.load_characters())==13
    value['messages'].append(value['messages'][0])
    with pytest.raises(AIError,match='訊息數量'):
        validate_turn(response(value),profiles,'user_reply')


def test_large_peer_history_reuses_resident_enum_under_provider_schema_limit(store):
    profiles=store.load_characters();anchors=[]
    for i in range(100):
        c=profiles[i%13]
        row=store.append_message(speaker_id=c.id,speaker_name=c.name,text='本機格式計數測試',source_kind='ai')
        if i<40:anchors.append(row['id'])
    store.append_message(speaker_id='user',speaker_name='月鹿',text='大家在嗎')
    with store.connection:
        store.connection.execute('INSERT INTO settings VALUES(?,?)',('conversation:main',json.dumps({'anchor_ids':anchors})))
    payload,_=build_payload(AIConfig(),profiles,store,'main','quiet_chat')
    schema=payload['response_format']['json_schema']['schema']
    def enum_count(value):
        if isinstance(value,dict):return len(value.get('enum',[]))+sum(enum_count(v) for key,v in value.items() if key!='enum')
        if isinstance(value,list):return sum(map(enum_count,value))
        return 0
    assert enum_count(schema)<=500  # Conservative bound across supported models.
    assert schema['$defs']['resident_id']['enum']==[c.id for c in profiles]
    for array in schema['properties']['messages']['anyOf']:
        for historic in array['items']['anyOf'][1:]:
            assert historic['properties']['speaker_id']=={'$ref':'#/$defs/resident_id'}
    assert len(store.messages())==101


def test_output_limit_uses_reported_usage_and_keeps_failure_metrics_without_raw_chat(app,store,monkeypatch):
    from our_little_days import ai_ui
    from our_little_days.storage import SQLiteStore
    from PySide6.QtWidgets import QDialog,QTabWidget
    window=MainWindow(UnsupportedBackend(),store.load_characters(),store)
    try:
        setup_fake(window,monkeypatch);c=window.ai
        for kind in ('screen_test','reply_test'):
            rid,_=c.ledger.reserve(c.config,kind,100);c.ledger.settle(rid,{'prompt_tokens':10,'completion_tokens':10},'offline');c.ledger.mark_test(c.config,kind,rid)
        c.enable(True);c.quiet_enabled=True;c.refresh()
        before=[asdict(p) for p in store.load_characters()]
        profiles=store.load_characters()
        previous_messages=store.messages()
        store.add_memory('不能刪除的記憶',character_id='shogo')
        calls=[]
        def fake(key,payload,cancelled):
            calls.append(payload)
            raw=response(residents_turn(profiles))
            raw['model']='gpt-4.1-mini-test-report';raw['usage']={'prompt_tokens':16000,'completion_tokens':2000}
            raw['choices'][0]['finish_reason']='length'
            raw['choices'][0]['message']['content']='{"messages":[{"speaker_id":"shogo","segments":[{"kind":"speech","text":"診斷秘密詞"},{"kind":"speech","text":"診斷秘密詞"},{"kind":"speech","text":"未完成'
            return raw
        monkeypatch.setattr(ai_ui,'request_api',fake)
        window.chat_input.setPlainText('這個畫面呢')
        QTest.mouseClick(window.send_button,Qt.MouseButton.LeftButton)
        wait(lambda:c.worker is None)
        assert len(calls)==1 and '輸出 2000／設定上限 2000 tokens' in c.last_note
        assert not c.enabled and not c.quiet_enabled  # Preserve explicit pause, no background paid retry.
        assert len(store.messages())==len(previous_messages)+1 and store.messages()[-1]['speaker_id']=='user'
        assert store.messages()[:-1]==previous_messages
        assert c.ledger.totals()['month']['output_tokens']==2020 and c.ledger.totals()['month']['uncertain_twd']==0
        result=store.api_result('main')
        assert result['finish_reason']=='length' and result['completion_tokens']==2000 and result['prompt_tokens']==16000
        assert result['configured_output_limit']==2000 and not result['content_json_complete'] and result['status']=='output_limit'
        assert result['complete_text_field_count']==2 and result['repeated_complete_text_fields']==1
        assert result['message_field_occurrences']==1 and result['segment_field_occurrences']==3
        assert '診斷秘密詞' not in json.dumps(result) and 'mock-key-only' not in json.dumps(result)
        assert [asdict(p) for p in store.load_characters()]==before and store.memories()[0]['content']=='不能刪除的記憶'
        assert not store.api_result('glasshouse')
        viewed=[]
        def inspect(dialog):
            tabs=dialog.findChild(QTabWidget)
            assert tabs.tabText(7)=='最近 API 結果'
            text=tabs.widget(7).toPlainText()
            assert '回報輸出：2000 tokens' in text and '輸出上限：2000 tokens' in text and '截斷' in text
            viewed.append(True);return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog,'exec',inspect);window.inspect_character()
        assert viewed and len(calls)==1
        reopened=SQLiteStore(store.path)
        try:assert reopened.api_result('main')==result
        finally:reopened.close()
    finally:window.close();app.processEvents()


@pytest.mark.parametrize('text', ['月、僕はここにいるよ。', 'REOは「はい、小鳥ちゃん、ご安心ください」と言った。', '画面は見えていない。', '画面が見えてるとは言えない。', '君の気持ちが見えてる。', '僕は本をずっと読んでる。'])
def test_reply_advisories_do_not_flag_plain_voice_quotes_or_absent_visuals(store,text):
    profiles=store.load_characters()
    shogo=next(c for c in profiles if c.id=='shogo')
    assert reply_quality_notes(shogo,text,profiles,image_attached=False)==[]


def test_screenshot_reply_mixups_are_advisory_not_rewritten_and_worldline_isolated(store):
    profiles=store.load_characters()
    original=[asdict(c) for c in profiles]
    native='はい、小鳥ちゃん、こちらの画面は確かに見えております。ご安心ください。'
    value={'meaningful':False,'event_key':'','summary':'','important':False,'read_character_ids':[],
           'messages':[{'speaker_id':'shogo','recipient_ids':['user'],'reply_to_index':None,'reply_to_message_id':None,'segments':[{'kind':'speech','text':native,'translation':'是的，小鳥ちゃん，我確實看見畫面。請放心。'}]}]}
    records=save_turn(store,'main',validate_turn(response(value),profiles,'user_reply'),profiles,'user_reply',image_attached=False,response_model='provider-reported-model')
    diagnostic=store.reply_diagnostic('main')
    assert diagnostic['image_attached'] is False and diagnostic['provider_model']=='provider-reported-model'
    assert diagnostic['message_ids']==[records[0]['id']] and diagnostic['replied_character_ids']==['shogo']
    notes=diagnostic['warnings'][0]['notes']
    assert len(notes)==3 and '小鳥ちゃん' in notes[0] and '敬語' in notes[1] and '未附圖' in notes[2]
    assert records[0]['text'].startswith(native) and store.messages()[0]['text']==records[0]['text']
    assert [asdict(c) for c in store.load_characters()]==original and not store.reply_diagnostic('glasshouse')
    assert 'translation' not in diagnostic and 'source_text' not in diagnostic
    from our_little_days.storage import SQLiteStore
    reopened=SQLiteStore(store.path)
    try:assert reopened.reply_diagnostic('main')==diagnostic
    finally:reopened.close()
    reo=next(c for c in profiles if c.id=='reo')
    assert reply_quality_notes(reo,native,profiles,image_attached=True)==[]
    assert len(reply_quality_notes(reo,native,profiles,image_attached=False))==1


def test_reply_advisory_keeps_enabled_modes_and_uses_one_request(app,store,monkeypatch):
    from our_little_days import ai_ui
    from PySide6.QtWidgets import QDialog,QTabWidget
    window=MainWindow(UnsupportedBackend(),store.load_characters(),store)
    try:
        setup_fake(window,monkeypatch)
        c=window.ai
        for kind in ('screen_test','reply_test'):
            rid,_=c.ledger.reserve(c.config,kind,100)
            c.ledger.settle(rid,{'prompt_tokens':10,'completion_tokens':10},'offline')
            c.ledger.mark_test(c.config,kind,rid)
        c.enable(True); c.autonomous=c.quiet_enabled=True; c.refresh()
        calls=[]
        def fake(key,payload,cancelled):
            calls.append(payload)
            value=turn(); value['messages']=value['messages'][:1]
            value['messages'][0].update(speaker_id='shogo',recipient_ids=['user'],reply_to_index=None,reply_to_message_id=None)
            value['messages'][0]['segments']=[{'kind':'speech','text':'はい、小鳥ちゃん、こちらの画面は確かに見えております。','translation':'是的，小鳥ちゃん，我確實看見畫面。'}]
            raw=response(value); raw['model']='gpt-4.1-mini-test-report'
            return raw
        monkeypatch.setattr(ai_ui,'request_api',fake)
        window.chat_input.setPlainText('有看到畫面嗎')
        QTest.mouseClick(window.send_button,Qt.MouseButton.LeftButton)
        wait(lambda:c.worker is None)
        assert len(calls)==1 and c.enabled and c.autonomous and c.quiet_enabled
        assert '回覆檢查提示' in c.last_note and '沒有自動重試' in c.last_note
        assert c.ledger.totals()['month']['requests']==3 and c.ledger.totals()['month']['uncertain_twd']==0
        assert store.reply_diagnostic('main')['image_attached'] is False
        window.character_combo.setCurrentIndex(11)
        viewed=[]
        def inspect(dialog):
            tabs=dialog.findChild(QTabWidget)
            assert tabs.tabText(6)=='最近回覆檢查'
            text=tabs.widget(6).toPlainText()
            assert 'gpt-4.1-mini-test-report' in text and '本次實際附圖：否' in text and '其他角色的稱呼' in text
            viewed.append(True)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog,'exec',inspect)
        window.inspect_character()
        assert viewed and len(calls)==1
    finally:window.close();app.processEvents()


@pytest.mark.parametrize('bad_readers', [['不存在的住戶'], ['reo','reo'], 'reo', [None]])
def test_invalid_explicit_read_decisions_are_rejected(bad_readers,store):
    value=json.loads(response()['choices'][0]['message']['content'])
    value['read_character_ids']=bad_readers
    value['messages']=value['messages'][:1]
    for m in value['messages']:
        m['reply_to_index']=None
        m['reply_to_message_id']=None
    raw=response();raw['choices'][0]['message']['content']=json.dumps(value)
    with pytest.raises(AIError,match='已讀住戶'):
        validate_turn(raw,store.load_characters(),'user_reply')
    assert store.roster_status()['read']==[]


def test_image_context_over_old_guard_preserves_all_personas_memory_and_budget(store,app):
    memory='完整記憶保留'+('x'*80_000)
    store.add_memory(memory,character_id='shogo')
    before=store.load_characters()
    config=AIConfig()
    payload,upper=build_payload(config,before,store,'main','screen_event',image())
    assert upper+config.max_output_tokens>120_000
    assert upper+config.max_output_tokens<=256_000
    context=json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['characters']==json.loads(json.dumps([asdict(c) for c in before]))
    assert context['long_term_memories'][0]['content']==memory
    assert any(p['type']=='image_url' for p in payload['messages'][1]['content'])
    ledger=UsageLedger(store.path.parent/'context-budget-test.sqlite3')
    try:
        with pytest.raises(AIError,match='預算'):
            ledger.reserve(replace(config,monthly_budget_twd=.01),'screen_event',upper)
        assert ledger.totals()['month']['requests']==0
    finally:ledger.close()
    with pytest.raises(AIError,match='120,000'):
        build_payload(replace(config,model='gpt-4o-mini'),before,store,'main','screen_event',image())
    assert store.memories()[0]['content']==memory and store.load_characters()==before


def test_true_context_overflow_stops_without_deleting_memory(store):
    store.add_memory('x'*300_000,character_id='shogo')
    before=store.load_characters()
    with pytest.raises(AIError,match='本次未送出、不刪除人格或記憶'):
        build_payload(AIConfig(),before,store,'main','user_reply')
    assert len(store.memories()[0]['content'])==300_000 and store.load_characters()==before


def test_unsaved_voice_roster_is_ephemeral_and_does_not_mark_old_typed_round(app,store,monkeypatch):
    window=MainWindow(UnsupportedBackend(),store.load_characters(),store)
    try:
        calls=setup_fake(window,monkeypatch)
        window.ai.enabled = True  # Explicitly enabled test controller; no real provider.
        old=store.append_message(speaker_id='user',speaker_name='月',text='之前的打字')
        assert window.ai.user_sent(transient_text='不是吧？',user_message_id=None)
        wait(lambda:window.ai.worker is None)
        assert window.transient_roster is not None
        assert window.roster_labels['order'].text()=='○ 已回覆'
        assert store.roster_status()['replied']==[] and store.roster_status()['user_message_id']==old['id']
        assert not any('不是吧' in row['text'] for row in store.messages())
    finally:window.close();app.processEvents()
