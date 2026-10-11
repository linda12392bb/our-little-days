"""Isolated package verification; never opens the user's apartment or captures a screen."""
from dataclasses import asdict, replace
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def _spawn_probe(connection):
    # The real capture worker needs this module and Windows DLLs in the frozen child.
    if sys.platform == "win32":
        from . import win32
        assert callable(win32.capture_window)
    connection.send("spawn-ok")
    connection.close()


def _transport_probe(connection, key, payload):
    import ssl
    ssl.create_default_context()
    connection.send(('ok', {'probe': key == 'package-test-not-an-api-key', 'bytes': len(payload['image'])}))
    connection.close()


def _check_manual_buttons(window):
    """Exercise the shipped button, failure dialog and ledger with no network."""
    from dataclasses import replace
    import time
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from . import ai_ui
    c = window.ai
    saved = (ai_ui.request_api, QMessageBox.warning, QMessageBox.information,
             c.keys.read, c.keys.exists, c.config)
    calls, warnings, successes = [], [], []
    profiles=window.store.load_characters()
    def fake(key, payload, cancelled):
        calls.append(payload)
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        kind = context['task']
        assert [m['role'] for m in payload['messages']] == ['system','user','system','user']
        focus = json.loads(payload['messages'][-1]['content'])
        assert focus['task']==kind and focus['visual_evidence']==context['visual_evidence']
        assert '常体を使い、丁寧語・敬語で話さない' in payload['messages'][2]['content']
        schema = payload['response_format']['json_schema']['schema']['properties']['messages']
        if 'anyOf' in schema:
            assert schema['anyOf'][-1]['maxItems']==13
            schema=schema['anyOf'][0]
        item = schema['items']
        variants = item.get('anyOf', [item])
        assert all(variant['properties']['reply_to_index'] == {'type':'null'} for variant in variants)
        if kind == 'reply_test':
            assert schema['items']['properties']['speaker_id']['enum'] == ['order']
            assert schema['minItems'] == schema['maxItems'] == 1
        if kind == 'user_reply':
            assert schema['minItems'] == 1 and all(v['properties']['reply_to_message_id'] == {'type':'null'} for v in variants)
            assert context['current_user_input'] == {'kind':'typed_text','text':'晚安','saved':True}
            assert focus['current_user_input']==context['current_user_input']
            assert context['character_address_rules']['shogo'] == '月'
            assert context['character_address_rules']['reo'] == '小鳥ちゃん'
            assert context['visual_evidence']['image_attached'] is False
        root_schema=payload['response_format']['json_schema']['schema']
        for variant in variants:
            props = variant['properties']
            if props['reply_to_message_id']=={'type':'null'}:
                continue
            authors = props['recipient_ids']['items']['enum']
            assert len(authors) == 1
            exposed = {row['id']:row for row in [*context['recent_chat'], *context['ongoing_conversation']['anchor_messages']]}
            reference=props['reply_to_message_id']
            for message_id in root_schema['$defs'][reference['$ref'].split('/')[-1]]['enum']:
                assert exposed[message_id]['speaker_id'] == authors[0]
        value = {'meaningful': True, 'event_key': '', 'summary': '', 'important': False, 'read_character_ids':['shogo'] if kind=='user_reply' else [],
                 'messages':[{'speaker_id':'序' if len(calls) == 1 else 'order',
                              'recipient_ids':['user'], 'reply_to_index':None, 'reply_to_message_id':None,
                              'segments':[{'kind':'speech', 'text':'表示テストやで♡', 'translation':'這是顯示測試喔♡'}]}]}
        if kind == 'quiet_test':
            value['meaningful'] = False
            value['messages'] = [
                {'speaker_id':'light','recipient_ids':['order'],'reply_to_index':None,'reply_to_message_id':None,
                 'segments':[{'kind':'speech','text':'本機接話測試。','translation':''}]},
                {'speaker_id':'order','recipient_ids':['light'],'reply_to_index':None,'reply_to_message_id':None,
                 'segments':[{'kind':'speech','text':'表示テストやで♡','translation':'這是顯示測試喔♡'}]},
                {'speaker_id':'reo','recipient_ids':['order'],'reply_to_index':None,'reply_to_message_id':None,
                 'segments':[{'kind':'speech','text':'僕も参加いたします。','translation':'我也來參與。'}]},
            ]
        usage = {'prompt_tokens':15759, 'completion_tokens':346} if kind == 'quiet_test' else {'prompt_tokens':15054, 'completion_tokens':161}
        if kind=='user_reply' and len(calls)==5:
            value['read_character_ids']=[]
            value['messages'][0].update(speaker_id='shogo', segments=[{'kind':'speech','text':'はい、小鳥ちゃん、こちらの画面は確かに見えております。','translation':'是的，小鳥ちゃん，我確實看見畫面。'}])
        if kind=='user_reply' and len(calls)==6:
            value['read_character_ids']=[]
            value['messages']=[]
            for i,character in enumerate(profiles):
                native='はい。' if character.language.startswith('ja') else 'Here.' if character.language=='en' else '在這裡。'
                value['messages'].append({'speaker_id':character.id,'recipient_ids':['user' if i==0 else profiles[i-1].id],'reply_to_index':None,'reply_to_message_id':None,
                                          'segments':[{'kind':'speech','text':native,'translation':'在這裡。' if character.translation_required else ''}]})
            usage={'prompt_tokens':15054,'completion_tokens':1500}
        if kind=='user_reply' and len(calls)==7:
            return {'id':'offline-output-limit','model':'gpt-4.1-mini-offline-test','usage':{'prompt_tokens':15054,'completion_tokens':2000},
                    'choices':[{'finish_reason':'length','message':{'content':'{"messages":[{"speaker_id":"shogo","segments":[{"kind":"speech","text":"未完成'}}]}
        return {'id':'offline-test-only', 'model':'gpt-4.1-mini-offline-test', 'usage':usage,
                'choices':[{'finish_reason':'stop', 'message':{'content':json.dumps(value)}}]}
    try:
        ai_ui.request_api = fake
        QMessageBox.warning = lambda parent,title,text: warnings.append(text)
        QMessageBox.information = lambda parent,title,text: successes.append(text)
        c.keys.read = lambda: 'package-test-not-an-api-key'
        c.keys.exists = lambda: True
        c.config = replace(c.config, transmission_approved=True)
        window.character_combo.setCurrentIndex(1)
        c.pause()
        before = window.store.messages()
        for number in (1, 2):
            QTest.mouseClick(window.ai_reply_test, Qt.MouseButton.LeftButton)
            deadline = time.monotonic()+10
            while c.worker is not None and time.monotonic() < deadline:
                QTest.qWait(10)
            assert c.worker is None and len(calls) == number
            if number == 1:
                assert window.store.messages() == before and len(warnings) == 1 and not successes
                assert '無效角色 ID' in warnings[0] and 'NT$0.2072' in warnings[0]
                assert '無效角色 ID' in window.ai_test_note.text()
        assert len(successes) == 1 and len(window.store.messages()) == len(before)+1
        assert c.ledger.totals()['month']['requests'] == 2
        assert c.ledger.totals()['month']['uncertain_twd'] == 0
        rid, _ = c.ledger.reserve(c.config, 'screen_test', 100)
        c.ledger.settle(rid, {'prompt_tokens':100,'completion_tokens':100}, 'offline-cost-gate-only')
        c.ledger.mark_test(c.config, 'screen_test', rid)
        c.enable(True)
        assert window.ai_quiet_test.isEnabled() and not c.quiet_enabled
        QTest.mouseClick(window.ai_quiet_test, Qt.MouseButton.LeftButton)
        deadline = time.monotonic()+10
        while c.worker is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert c.worker is None and len(calls) == 3 and len(successes) == 2 and len(warnings) == 1, (len(calls), successes, warnings)
        records = window.store.messages()[-3:]
        assert records[0]['reply_to'] is None and records[1]['reply_to'] == records[0]['id']
        assert records[2]['reply_to'] == records[1]['id']
        assert abs(c.ledger.quiet_today()-.226288) < 1e-9 and not c.quiet_enabled
        c.quiet_enabled = True
        c.refresh()
        window.chat_input.setPlainText('晚安')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic()+10
        while c.worker is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert c.worker is None and len(calls) == 4
        records = window.store.messages()[-2:]
        assert records[0]['speaker_id'] == 'user' and records[1]['reply_to'] == records[0]['id']
        assert window.roster_labels['order'].text()=='○ 已回覆'
        assert window.roster_labels['shogo'].text()=='✓ 已讀未回'
        assert all(v.text()=='· 未出場' for ident,v in window.roster_labels.items() if ident not in {'order','shogo'})
        assert window.store.roster_status()['read']==['shogo']
        from .ui import display_message_text
        assert display_message_text('うん。\n（嗯。）\n（嗯。）')[0]=='うん。\n（嗯。）'
        assert display_message_text('うん。\n（嗯。）\nうん。\n（嗯。）')[0].count('（嗯。）')==2
        assert c.enabled and c.quiet_enabled and len(warnings) == 1
        window.chat_input.setPlainText('晚安')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic()+10
        while c.worker is not None and time.monotonic()<deadline:
            QTest.qWait(10)
        assert c.worker is None and len(calls)==5 and c.enabled and c.quiet_enabled
        assert '回覆檢查提示' in c.last_note and '沒有自動重試' in c.last_note
        diagnostic=window.store.reply_diagnostic(window.worldline)
        assert diagnostic['image_attached'] is False and diagnostic['provider_model']=='gpt-4.1-mini-offline-test'
        assert len(diagnostic['warnings'][0]['notes'])==3
        assert window.store.messages()[-1]['text'].startswith('はい、小鳥ちゃん')
        assert c.ledger.totals()['month']['uncertain_twd']==0
        from PySide6.QtWidgets import QDialog, QTabWidget
        window.character_combo.setCurrentIndex(11)
        original_exec = QDialog.exec
        viewed = []
        def inspect(dialog):
            tabs = dialog.findChild(QTabWidget)
            assert tabs.tabText(0) == '目前套用設定'
            assert '平常稱呼：月' in tabs.widget(0).toPlainText()
            assert '不使用敬語' in tabs.widget(0).toPlainText() and '自稱：僕' in tabs.widget(0).toPlainText()
            assert '準備使用的稱呼：月' in tabs.widget(1).toPlainText()
            assert 'かみそりちゃん' in tabs.widget(2).toPlainText()
            assert tabs.tabText(6)=='最近回覆檢查' and '本次實際附圖：否' in tabs.widget(6).toPlainText()
            assert '直接使用其他角色的稱呼' in tabs.widget(6).toPlainText()
            viewed.append(True)
            return QDialog.DialogCode.Accepted
        try:
            QDialog.exec = inspect
            window.inspect_character()
            assert viewed and len(calls) == 5
        finally:
            QDialog.exec = original_exec
        window.chat_input.setPlainText('晚安')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        deadline=time.monotonic()+10
        while c.worker is not None and time.monotonic()<deadline:QTest.qWait(10)
        assert c.worker is None and len(calls)==6
        residents=window.store.messages()[-13:]
        assert len({r['speaker_id'] for r in residents})==13
        assert all(residents[i]['reply_to']==residents[i-1]['id'] for i in range(1,13))
        assert window.store.api_result(window.worldline)['completion_tokens']==1500
        before_limit=window.store.messages()
        window.chat_input.setPlainText('晚安')
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        deadline=time.monotonic()+10
        while c.worker is not None and time.monotonic()<deadline:QTest.qWait(10)
        assert c.worker is None and len(calls)==7 and not c.enabled
        assert len(window.store.messages())==len(before_limit)+1 and window.store.messages()[-1]['speaker_id']=='user'
        assert '輸出 2000／設定上限 2000 tokens' in c.last_note
        evidence=window.store.api_result(window.worldline)
        assert evidence['status']=='output_limit' and evidence['finish_reason']=='length' and evidence['completion_tokens']==2000
        assert not evidence['content_json_complete'] and c.ledger.totals()['month']['uncertain_twd']==0
        def inspect_limit(dialog):
            tabs=dialog.findChild(QTabWidget)
            assert tabs.tabText(7)=='最近 API 結果'
            assert '回報輸出：2000 tokens' in tabs.widget(7).toPlainText()
            return QDialog.DialogCode.Accepted
        try:
            QDialog.exec=inspect_limit;window.inspect_character();assert len(calls)==7
        finally:QDialog.exec=original_exec
    finally:
        (ai_ui.request_api, QMessageBox.warning, QMessageBox.information,
         c.keys.read, c.keys.exists, c.config) = saved
        c.pause()


def _check_autonomous_peer_router(window):
    """Run actual idle scheduling and cross-batch routing with a local fake API."""
    from datetime import datetime, timedelta, timezone
    import time
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from . import ai_ui, ai_engine, ai_cost
    c = window.ai
    originals = (ai_ui.request_api, QMessageBox.warning, QMessageBox.information,
                 c.keys.read, c.keys.exists, c.config, c.clock,
                 ai_ui.taipei_now, ai_engine.taipei_now, ai_cost.taipei_now)
    profiles = window.store.load_characters()
    memories, events = window.store.memories(), window.store.events()
    baseline = len(window.store.messages())
    clock, calls, first_id = [0], [], []
    base = datetime.now(timezone(timedelta(hours=8)))
    def now():
        return base + timedelta(seconds=clock[0])
    def wait():
        deadline = time.monotonic() + 10
        while c.worker is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        assert c.worker is None
    def fake(key, payload, cancelled):
        calls.append(payload)
        context = json.loads(payload['messages'][1]['content'][0]['text'])
        task = context['task']
        assert len(context['characters']) == 13 and payload['max_completion_tokens'] == 2000
        assert len(payload['messages'][1]['content']) == 1
        def message(speaker, recipient, text, translation='', reply=None):
            return {'speaker_id': speaker, 'recipient_ids': [recipient], 'reply_to_index': None,
                    'reply_to_message_id': reply,
                    'segments': [{'kind': 'speech', 'text': text, 'translation': translation}]}
        if len(calls) == 1:
            assert task == 'quiet_chat' and context['current_user_input'] is None
            assert context['resident_conversation']['active']
            messages = [message('light', 'order', '本機測試，序，你怎麼想？')]
        elif len(calls) == 2:
            assert task == 'quiet_chat' and context['current_user_input'] is None
            assert any(t['message_id'] == first_id[0] for t in context['resident_conversation']['recent_peer_threads'])
            messages = [message('order', 'light', 'まだその話しとるんか♡', '還在說那件事呀♡', first_id[0]),
                        message('reo', 'order', 'そのお話は続いておりますよ。', '那個話題還在繼續呢。')]
        else:
            assert len(calls) == 3 and task == 'user_reply'
            assert context['current_user_input']['text'] == '蛤？'
            assert not context['resident_conversation']['active']
            messages = [message('order', 'user', '月ちゃん、どうしたん？', '月ちゃん，怎麼了？')]
        value = {'meaningful': False, 'event_key': '', 'summary': '', 'important': False, 'messages': messages}
        return {'id': 'offline-idle-router-test', 'usage': {'prompt_tokens': 100, 'completion_tokens': 150},
                'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value, ensure_ascii=False)}}]}
    try:
        ai_ui.request_api = fake
        QMessageBox.warning = lambda *a: None
        QMessageBox.information = lambda *a: None
        c.keys.read, c.keys.exists = lambda: 'mock-key-only', lambda: True
        c.config = replace(c.config, transmission_approved=True, max_output_tokens=2000)
        c.clock = lambda: clock[0]
        ai_ui.taipei_now = ai_engine.taipei_now = ai_cost.taipei_now = now
        c.activity()
        assert not c.quiet_enabled and c.quiet_timer.interval() == 180000
        c.enable(True)
        c.quiet(True)
        clock[0] += 1800
        assert c.quiet_tick()
        wait()
        first_id.append(window.store.messages()[-1]['id'])
        clock[0] += 1800
        assert c.quiet_tick()
        wait()
        records = window.store.messages()[baseline:]
        assert len(records) == 3 and [r['speaker_id'] for r in records] == ['light', 'order', 'reo']
        assert records[1]['reply_to'] == records[0]['id'] and records[2]['reply_to'] == records[1]['id']
        assert [json.loads(r['recipient_ids_json']) for r in records] == [['order'], ['light'], ['order']]
        window.chat_input.setPlainText('蛤？')
        window.send_message()
        wait()
        assert len(calls) == 3 and c.enabled and c.quiet_enabled
        latest = window.store.messages()[-2:]
        assert latest[0]['speaker_id'] == 'user' and latest[1]['reply_to'] == latest[0]['id']
        assert c.ledger.quiet_today() > 0
        assert window.store.load_characters() == profiles
        assert window.store.memories() == memories and window.store.events() == events
    finally:
        (ai_ui.request_api, QMessageBox.warning, QMessageBox.information,
         c.keys.read, c.keys.exists, c.config, c.clock,
         ai_ui.taipei_now, ai_engine.taipei_now, ai_cost.taipei_now) = originals
        c.pause()


def check_package(speech_test_dir=None):
    from PySide6.QtWidgets import QApplication
    from .capture import DemoBackend
    from .models import load_apartment_settings, load_characters
    from .storage import SQLiteStore
    from .ui import MainWindow

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance() or QApplication([])
    characters = load_characters()
    original = json.loads(json.dumps({c.id: asdict(c) for c in characters}, ensure_ascii=False))
    by_id = {c.id: c for c in characters}
    assert len(characters) == 13
    assert all(c.source_text and c.decision_rules and c.behavior for c in characters)
    assert by_id["light"].appearance["height_cm"] == 186
    assert by_id["shogo"].appearance["height_cm"] == 180
    assert by_id["shogo"].relationships["worldlines"]["main"]["date_agreement"]
    checks = ["all 13 profiles, original sections and confirmed appearances loaded"]
    with TemporaryDirectory(prefix="our-little-days-package-") as temporary:
        folder = Path(temporary) / "公寓封裝測試"
        store = SQLiteStore(folder / "apartment.sqlite3")
        window = None
        try:
            store.seed(characters, load_apartment_settings())
            shogo = next(c for c in characters if c.id == 'shogo')
            legacy = replace(shogo, user_address='かみそりちゃん', behavior=tuple(shogo.behavior[:-6]),
                             language_rules={k:v for k,v in shogo.language_rules.items() if k!='dialogue_style_reference'})
            store.save_character(legacy)
            assert store.apply_confirmed_shogo_address_update().is_file()
            assert store.apply_confirmed_shogo_address_update() is None
            assert store.apply_confirmed_shogo_dialogue_reference().is_file()
            assert store.apply_confirmed_shogo_dialogue_reference() is None
            assert json.loads(json.dumps({c.id:asdict(c) for c in store.load_characters()})) == original
            checks.append('confirmed Shogo 月 address, proactive affection and stopping unwanted interaction migrate once with backup; original source, personality and AU preserved')
            from .ai_engine import reply_quality_notes, build_payload
            from .ai_cost import AIConfig
            current_shogo=next(c for c in store.load_characters() if c.id=='shogo')
            reference=current_shogo.language_rules['dialogue_style_reference']['native']
            assert not reply_quality_notes(current_shogo,reference,store.load_characters())
            assert reply_quality_notes(current_shogo,'この画面はログが見えますね。読みづらいですが、背景はダークテーマです。',store.load_characters())
            voice_payload,_=build_payload(AIConfig(),store.load_characters(),store,'main','user_reply',current_input={'kind':'typed_text','text':'已讀不回','saved':False})
            voice_schema=voice_payload['response_format']['json_schema']['schema']
            for array in voice_schema['properties']['messages']['anyOf']:
                for item in array['items']['anyOf']:
                    properties=item['properties']
                    if properties['speaker_id'].get('enum')==['shogo']:
                        description=voice_schema['$defs'][properties['segments']['$ref'].split('/')[-1]]['items']['properties']['text']['description']
                        assert reference in description and '自分の観察・判断・関心' in description and '会話の目的' in description
                    else:
                        assert 'shogo' not in voice_schema['$defs']['other_resident_id']['enum']
            checks.append('frozen Shogo user-approved dialogue reference migrates once with backup, stays separate from memories and binds independent purpose/rhythm to own output field; reported polite connective forms flagged locally, no API')
            window = MainWindow(DemoBackend(), store.load_characters(), store)
            window.show()
            app.processEvents()
            window.chat_input.setPlainText("封裝測試：重新開啟仍保留 ☾")
            window.send_message()
            window.character_combo.setCurrentIndex(11)
            window.add_test_message()
            store.add_event("封裝測試事件")
            store.add_memory("封裝測試長期記憶", character_id="shogo")
            messages = store.messages()
            profiles = store.load_characters()
            assert json.loads(json.dumps({c.id: asdict(c) for c in profiles}, ensure_ascii=False)) == original
            assert window.ai is not None
            assert window.voice is not None and window.voice.source is None
            assert not window.voice_save.isChecked() and window.voice_auto.isChecked()
            from .speech import VoiceConfig, VoiceSegmenter, normalize_text, runtime_folder
            assert not VoiceSegmenter(VoiceConfig()).feed(bytes(32000))
            assert normalize_text('这个游戏。難しいね。') == '這個遊戲。難しいね。'
            checks.append('microphone defaults OFF; local WebRTC VAD and packaged OpenCC dictionaries load; no audio capture')
            if sys.platform == 'win32':
                from PySide6.QtMultimedia import QtAudio, QAudioSource
                audio_probe = QAudioSource(parent=window.voice)
                assert isinstance(audio_probe.error(), QtAudio.Error)
                audio_probe.deleteLater()
                checks.append('frozen QAudioSource error uses QtAudio enum; source constructed only, microphone not started')
                import subprocess
                runtime = runtime_folder()
                manifest = json.loads((runtime / 'SOURCE.json').read_text(encoding='utf-8'))
                assert all(hashlib.sha256((runtime / name).read_bytes()).hexdigest() == digest for name, digest in manifest['files_sha256'].items())
                env = os.environ.copy()
                env['PATH'] = str(runtime)+os.pathsep+str(Path(sys.executable).parent/'_internal')+os.pathsep+env.get('PATH','')
                probe = subprocess.run([str(runtime / 'whisper-cli.exe'), '--help'], env=env, cwd=runtime, capture_output=True, timeout=30)
                assert probe.returncode == 0 and b'--language' in probe.stderr+probe.stdout
                checks.append('official Windows Whisper CLI checksum and native help smoke check; model and microphone not opened')
            assert not window.ai.enabled and not window.ai.autonomous
            assert window.ai.ledger.totals()['month']['requests'] == 0
            assert '剩餘預算' in window.ai_usage.text()
            from .ai_engine import build_payload
            from .ai_cost import AIConfig
            payload, upper = build_payload(AIConfig(), profiles, store, 'main', 'user_reply')
            context = json.loads(payload['messages'][1]['content'][0]['text'])
            assert len(context['characters']) == 13
            assert {c['id']: c['source_text'] for c in context['characters']} == {c.id: c.source_text for c in characters}
            checks.append('AI offline by default; budget UI and complete 13-persona payload verified without API calls')
            capacity_store = SQLiteStore(folder/'context-capacity.sqlite3')
            try:
                from PySide6.QtGui import QImage, QColor
                capacity_store.seed(characters, load_apartment_settings())
                capacity_store.add_memory('x'*80_000, character_id='shogo')
                synthetic = QImage(640,480,QImage.Format.Format_RGB32)
                synthetic.fill(QColor('white'))
                visual_payload, visual_upper = build_payload(AIConfig(), characters, capacity_store, 'main', 'screen_event', synthetic)
                visual_context = json.loads(visual_payload['messages'][1]['content'][0]['text'])
                assert 120_000 < visual_upper+2000 <= 256_000
                assert len(visual_context['characters'])==13 and visual_context['long_term_memories'][0]['content']=='x'*80_000
                assert any(p['type']=='image_url' for p in visual_payload['messages'][1]['content'])
                from .ai_cost import AIError
                try:
                    window.ai.ledger.reserve(replace(AIConfig(),monthly_budget_twd=.01),'screen_event',visual_upper)
                except AIError:
                    pass
                else:
                    raise AssertionError('Extended context must still enforce shared budget')
                assert window.ai.ledger.totals()['month']['requests']==0
            finally:
                capacity_store.close()
            checks.append('frozen full-persona image payload over old 120k guard succeeds under GPT-4.1 256k application cap; original memory intact and shared budget still blocks insufficient allowance, no API')
            assert not window.ai.quiet_enabled and window.ai.quiet_timer.interval() == 180000
            assert window.ai.config.max_output_tokens == 2000
            assert window.ai.config.quiet_daily_budget_twd == 10
            from .ai_engine import save_turn, validate_turn
            sample_turn = {'meaningful': False, 'event_key': '', 'summary': '', 'important': False,
                          'messages': [{'speaker_id': 'light', 'recipient_ids': ['order'], 'reply_to_index': None,
                                        'segments': [{'kind': 'speech', 'text': '封裝接話測試。', 'translation': ''}]}]}
            original = save_turn(store, 'main', sample_turn, profiles, 'quiet_chat')[0]
            sample_turn['messages'] = [{'speaker_id': 'order', 'recipient_ids': ['light'], 'reply_to_index': None,
                                      'reply_to_message_id': original['id'],
                                      'segments': [{'kind': 'speech', 'text': '表示テストやで♡', 'translation': '這是顯示測試喔♡'}]}]
            validated = validate_turn({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(sample_turn)}}]}, profiles, 'quiet_chat', store=store)
            reply = save_turn(store, 'main', validated, profiles, 'quiet_chat')[0]
            assert reply['reply_to'] == original['id']
            assert store.conversation_context('main')['anchor_messages']
            assert not store.conversation_context('glasshouse')['anchor_messages']
            _check_manual_buttons(window)
            _check_autonomous_peer_router(window)
            checks.append('frozen actual quiet scheduler opens light-to-order topic, later order replies to historical light ID and REO joins; typed user interrupts idle routing; all personas/memory preserved, one mock request per batch, no network')
            checks.append('frozen manual reply button works while AI paused; strict selected ID schema, safe failure dialog with settled usage and success dialog verified with mock provider, no network or retry')
            checks.append('frozen quiet_test button creates light-to-order-to-reo peer replies in one mock request; null model indexes resolved locally to prior explicit recipients and persisted; scheduler remains OFF')
            checks.append('frozen send button replies immediately to pinned typed input while quiet mode remains ON; history-ID schema couples each ID with its true speaker, no network')
            checks.append('frozen persona viewer shows actual SQLite 月 address, soft plain Japanese/no keigo/僕 and prepared request diagnostic separately from retained original source; inspection makes no API call')
            checks.append('frozen roster uses latest user round: actual responder only, explicit Shogo read decision, others absent; exact adjacent translation repeats hidden without extra requests')
            checks.append('frozen reply advisory detects Shogo mixed nickname/keigo and unattached-image claims; actual provider model/image status persisted and viewer free; reply retained, modes stay ON, one mock request without retry; latest input after full persona context')
            checks.append('frozen 13 residents reply in one mock batch with all peer links; API output cap 2000 retained and participant-dependent schema lengths verified')
            checks.append('frozen length result settles 2000 reported output tokens, saves local metadata only, keeps incomplete batch out of chat and offers free API-result viewer; pauses without retry')
            continuity = store.conversation_state('main')
            messages = store.messages()
            checks.append('quiet chat defaults OFF; 3-minute local check, 2000 shared output cap, NT$10 daily cap and persistent cross-batch peer replies verified with synthetic data, no API')
            if sys.platform == 'win32':
                from .ai_provider import WindowsKeyStore
                keys = WindowsKeyStore(folder)
                dummy = 'package-test-not-an-api-key'
                keys.save(dummy)
                assert dummy.encode() not in keys.path.read_bytes()
                assert WindowsKeyStore(folder).read() == dummy
                checks.append('Windows DPAPI dummy-key encrypted roundtrip (build environment only)')
            assert window.grab().width() > 0
            snapshot = store.backup(folder / "backup.sqlite3")
            checks.append("Qt offscreen interface and Unicode SQLite paths work")
            window.close()
            app.processEvents()
            window = None
            store.close()
            store = SQLiteStore(folder / "apartment.sqlite3")
            store.seed(characters, load_apartment_settings())
            window = MainWindow(DemoBackend(), store.load_characters(), store)
            assert store.messages() == messages
            assert store.load_characters() == profiles
            assert len(store.events()) == len(store.memories()) == 1
            assert not window.timer.isActive() and window.frame_image is None
            assert len(window.cards) == len(messages)
            assert store.conversation_state('main') == continuity
            assert not window.ai.quiet_enabled
            checks.append("close/reopen preserves profiles, relationships, chat, events and memory")
            store.append_message(speaker_id="user", speaker_name="月鹿", text="備份之後")
            safety = store.restore(snapshot, confirmed=True)
            assert safety.is_file() and store.messages() == messages
            checks.append("consistent SQLite backup and restore with safety copy")
        finally:
            if window is not None:
                window.close()
                app.processEvents()
            store.close()
    from .ai_provider import request_api
    transport = request_api('package-test-not-an-api-key', {'image': 'x' * 200000}, lambda: False, _transport=_transport_probe)
    assert transport == {'probe': True, 'bytes': 200000}
    checks.append('frozen cancellable transport spawn and TLS context initialized; mock transport, no network')
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_spawn_probe, args=(sender,))
    try:
        process.start()
        sender.close()
        if not receiver.poll(30):
            raise RuntimeError("Frozen child process did not respond")
        assert receiver.recv() == "spawn-ok"
        process.join(5)
        assert process.exitcode == 0
        checks.append("spawn child process completed" + (" and imported Windows capture bindings" if sys.platform == "win32" else " (Windows bindings not checked on Linux)"))
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
                process.join(5)
            process.close()
        receiver.close()
        sender.close()
    if speech_test_dir:
        import wave
        from .speech import VoiceConfig, WhisperRecognizer
        speech_test_dir = Path(speech_test_dir)
        sample = speech_test_dir / 'public-jfk.wav'
        assert hashlib.sha1(sample.read_bytes()).hexdigest() == 'c8c011f58c14f4d5a39beef7551280c846e3d047', 'Only the official public reference is accepted, never user recordings'
        with wave.open(str(sample), 'rb') as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            pcm = wav.readframes(wav.getnframes())
        transcript = WhisperRecognizer(speech_test_dir).transcribe(pcm, VoiceConfig(model='tiny', language='auto'), lambda: False)
        assert 'country' in transcript.lower(), 'Frozen ASR did not return reference text'
        pcm = None
        checks.append('frozen offline Whisper transcribed official public English reference with checksum-verified trained tiny weights; no microphone, API or audio files created')
    return {
        "status": "passed",
        "frozen": bool(getattr(sys, "frozen", False)),
        "python_platform": sys.platform,
        "qt_platform": app.platformName(),
        "checks": checks,
        "source_text_sha256": {c.id: hashlib.sha256(c.source_text.encode("utf-8")).hexdigest() for c in characters},
        "windows_manual_acceptance": "not_run",
        "limitations": ["synthetic/offscreen UI only", "no real selected-window capture", "no live model API or actual billed cost test", "no native Windows acceptance", "no real microphone or offline model accuracy acceptance"],
    }


def write_report(destination, speech_test_dir=None):
    import traceback
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = check_package(speech_test_dir=speech_test_dir)
        code = 0
    except Exception:
        report = {"status": "failed", "traceback": traceback.format_exc(), "windows_manual_acceptance": "not_run"}
        code = 1
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return code
