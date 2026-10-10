"""Isolated package verification; never opens the user's apartment or captures a screen."""
from dataclasses import asdict
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
