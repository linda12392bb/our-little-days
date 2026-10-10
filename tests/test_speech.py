"""Audio stays in RAM; tests do not open a microphone or make speech API calls."""
from dataclasses import replace
import json
import math
from pathlib import Path
import struct
import sys
import time

import pytest
from PySide6.QtGui import QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtCore import QObject, Signal, QBuffer, QIODevice, QByteArray
from PySide6.QtMultimedia import QAudioFormat, QtAudio

from our_little_days.speech import (FRAME_BYTES, PCMConverter, SpeechError, VoiceConfig, VoiceSegmenter,
    amplify, download_model, is_microphone_name, normalize_text, recognize_in_memory, rms_db, verify_model)
from our_little_days.capture import UnsupportedBackend
from our_little_days.ui import MainWindow


class EnergyDetector:
    def is_speech(self, pcm, rate):
        return any(struct.unpack('<'+'h'*(len(pcm)//2), pcm))


def tone(amplitude=150):
    return struct.pack('<320h', *(round(amplitude*math.sin(i*.12)) for i in range(320)))


def test_soft_60ms_word_and_1500ms_endpoint_keep_pre_roll():
    segmenter = VoiceSegmenter(VoiceConfig(), EnergyDetector())
    silence = bytes(FRAME_BYTES)
    assert not segmenter.feed(silence*12)
    assert not segmenter.feed(tone()*3)
    assert segmenter.speaking
    assert not segmenter.feed(silence*74)
    segments = segmenter.feed(silence)
    assert len(segments) == 1 and len(segments[0]) >= 3*FRAME_BYTES
    assert not segmenter.speaking
    assert not segmenter.feed(silence*100)


def test_adjustable_sensitivity_gain_and_endpoint_time():
    quiet = tone(35)
    insensitive = VoiceSegmenter(replace(VoiceConfig(), threshold_db=-25, gain_db=0), EnergyDetector())
    assert not insensitive.feed(quiet*10+bytes(FRAME_BYTES)*100)
    sensitive = VoiceSegmenter(replace(VoiceConfig(), threshold_db=-65, gain_db=12, end_silence=.5), EnergyDetector())
    assert not sensitive.feed(quiet*3+bytes(FRAME_BYTES)*24)
    assert len(sensitive.feed(bytes(FRAME_BYTES))) == 1
    assert rms_db(amplify(quiet,12)) > rms_db(quiet)+11
    assert rms_db(bytes(FRAME_BYTES)) == -120


def test_single_click_too_short_is_not_voice():
    segmenter = VoiceSegmenter(VoiceConfig(), EnergyDetector())
    assert not segmenter.feed(tone()*1+bytes(FRAME_BYTES)*100)
    assert not segmenter.speaking


def test_real_webrtc_vad_three_minutes_silence_never_requests_recognition():
    segmenter = VoiceSegmenter(VoiceConfig())
    for _ in range(180):
        assert not segmenter.feed(bytes(16000*2))
    assert not segmenter.speaking
    assert len(segmenter.pre_roll) <= 12
    assert len(segmenter.pending) < FRAME_BYTES


def test_long_utterance_and_fragmented_input_are_bounded():
    segmenter = VoiceSegmenter(VoiceConfig(), EnergyDetector())
    pcm = tone()*800
    outputs = []
    for i in range(0,len(pcm),337):
        outputs.extend(segmenter.feed(pcm[i:i+337]))
    assert len(outputs) == 1
    assert len(outputs[0]) <= 750*FRAME_BYTES
    assert len(segmenter.active) < 750
    assert len(segmenter.pending) < FRAME_BYTES


@pytest.mark.parametrize('kind,rate,channels,code,scale', [('int16',48000,2,'h',32768),('int32',44100,1,'i',2147483648),('float',48000,1,'f',1),('uint8',16000,1,'B',128)])
def test_stream_resample_and_mono_downmix(kind,rate,channels,code,scale):
    converter = PCMConverter(rate,channels,kind)
    values = [(.2*scale+128 if kind=='uint8' else .2*scale)]*(rate*channels)
    if kind!='float':
        values = list(map(round,values))
    raw = struct.pack('<'+code*len(values),*values)
    output = b''.join(converter.feed(raw[i:i+311]) for i in range(0,len(raw),311))
    assert abs(len(output)/2-16000)<3
    assert -15<rms_db(output)<-12
    assert len(converter.partial)<converter.bytes_per_sample*channels
    assert len(converter.samples)<8


def test_chinese_and_japanese_clause_normalization():
    assert normalize_text('这个游戏太难了！') == '這個遊戲太難了！'
    assert normalize_text('このゲームは難しい。不是吧？') == 'このゲームは難しい。不是吧？'
    assert normalize_text('蛤？欸？') == '蛤？欸？'


@pytest.mark.parametrize('name', ['Stereo Mix','立體聲混音','CABLE Output (VB-Audio)','What U Hear','Loopback Capture'])
def test_system_loopback_devices_are_rejected(name):
    assert not is_microphone_name(name)
    assert is_microphone_name('Microphone (USB Headset)')


def test_corrupt_model_and_import_leave_original_intact(tmp_path):
    original=tmp_path/'speech-models/ggml-base.bin'
    original.parent.mkdir();original.write_bytes(b'old-model-kept')
    bad=tmp_path/'bad.bin';bad.write_bytes(b'not-a-model')
    with pytest.raises(SpeechError,match='雜湊'):
        verify_model(bad,'base')
    with pytest.raises(SpeechError):
        download_model(tmp_path,'base',lambda:False,lambda *_:None,local_source=bad)
    assert original.read_bytes()==b'old-model-kept'
    assert not original.with_suffix('.download').exists()


def test_real_child_receives_wav_only_through_stdin_no_audio_files(tmp_path):
    command = [sys.executable, '-c', "import io,sys,wave; w=wave.open(io.BytesIO(sys.stdin.buffer.read())); assert w.getframerate()==16000 and w.getnchannels()==1 and len(w.readframes(16000))==1920; sys.stdout.buffer.write('这个游戏。何これ？'.encode('utf-8'))"]
    assert recognize_in_memory(command,tone()*3,lambda:False,cwd=tmp_path)=='這個遊戲。何これ？'
    assert not list(tmp_path.iterdir())


def test_cancel_stops_real_child_even_while_stdin_is_blocked(tmp_path):
    start=time.monotonic()
    command=[sys.executable,'-c','import time; time.sleep(20)']
    with pytest.raises(SpeechError,match='取消'):
        recognize_in_memory(command,bytes(480000),lambda:time.monotonic()-start>.15,cwd=tmp_path)
    assert time.monotonic()-start<3
    assert not list(tmp_path.iterdir())


def test_offline_child_failure_does_not_surface_or_log_audio(tmp_path):
    with pytest.raises(SpeechError,match='辨識失敗'):
        recognize_in_memory([sys.executable,'-c','import sys; sys.exit(1)'],tone()*3,lambda:False,cwd=tmp_path)
    assert not list(tmp_path.iterdir())


def wait(predicate):
    end=time.monotonic()+3
    while not predicate() and time.monotonic()<end:
        QTest.qWait(10)
    assert predicate()


@pytest.fixture
def voice_window(app,store):
    window=MainWindow(UnsupportedBackend(),store.load_characters(),store)
    assert window.voice is not None
    yield window
    window.close();app.processEvents()


@pytest.fixture
def microphone_backend(voice_window,store,monkeypatch):
    """Use real QtAudio enum values and synchronous signals, with no hardware."""
    import our_little_days.speech_ui as speech_ui
    class Device:
        def id(self): return QByteArray(b'test-mic')
        def description(self): return 'Microphone (USB Headset)'
        def isFormatSupported(self,format): return False
        def preferredFormat(self):
            format=QAudioFormat()
            format.setSampleRate(48000);format.setChannelCount(2)
            format.setSampleFormat(QAudioFormat.SampleFormat.Float)
            return format
    class Source(QObject):
        stateChanged=Signal(object)
        initial_error=QtAudio.Error.NoError
        initial_exception=None
        def __init__(self,device,format,parent):
            super().__init__(parent)
            self.audio_format=format
            self.input=QBuffer(self)
            self.input.open(QIODevice.OpenModeFlag.ReadOnly)
            self.stopped=False
        def setBufferSize(self,size): self.buffer_size=size
        def error(self): return self.initial_error
        def start(self):
            if self.initial_exception:
                raise self.initial_exception
            self.stateChanged.emit(QtAudio.State.ActiveState if self.error()==QtAudio.Error.NoError else QtAudio.State.StoppedState)
            return self.input
        def stop(self): self.stopped=True
    w=voice_window
    w.voice_device.clear();w.voice_device.addItem('Microphone (USB Headset)',Device())
    model=store.path.parent/'speech-models/ggml-base.bin'
    model.parent.mkdir();model.write_bytes(b'placeholder; startup does not run ASR')
    monkeypatch.setattr(speech_ui,'QAudioSource',Source)
    monkeypatch.setattr(speech_ui.sys,'platform','win32')
    return w,Source


def test_microphone_start_accepts_actual_qt_audio_no_error_and_native_format(microphone_backend,store):
    w,Source=microphone_backend
    w.voice.start()
    assert w.voice.source is not None
    assert w.voice.source.error()==QtAudio.Error.NoError
    assert w.voice.source.audio_format.sampleRate()==48000
    assert w.voice.converter.kind=='float' and w.voice.converter.channels==2
    assert '麥克風開啟' in w.voice_status.text()
    assert w.voice_toggle.text()=='關閉麥克風'
    assert w.ai.ledger.totals()['month']['requests']==0
    source=w.voice.source
    w.voice.stop()
    assert source.stopped and w.voice.source is None


def test_synchronous_open_error_does_not_dereference_stopped_source(microphone_backend):
    w,Source=microphone_backend
    Source.initial_error=QtAudio.Error.OpenError
    w.voice.start()
    assert w.voice.source is None and w.voice.input is None
    assert 'OpenError' in w.voice_note.text()
    assert 'AttributeError' not in w.voice_note.text()
    assert not w.voice.send_timer.isActive()


def test_later_hardware_error_stops_capture_and_reports_error_code(microphone_backend):
    w,Source=microphone_backend
    w.voice.start()
    source=w.voice.source
    assert source is not None
    source.initial_error=QtAudio.Error.IOError
    source.stateChanged.emit(QtAudio.State.StoppedState)
    assert source.stopped and w.voice.source is None
    assert 'IOError' in w.voice_note.text()


def test_start_exception_has_specific_diagnostic_and_leaves_mic_off(microphone_backend):
    w,Source=microphone_backend
    Source.initial_exception=TypeError('mock unsupported invocation')
    w.voice.start()
    assert w.voice.source is None
    assert 'TypeError' in w.voice_note.text()


def test_mic_defaults_off_no_audio_or_ai_and_settings_restore(voice_window,store,app):
    w=voice_window
    assert w.voice.source is None
    assert not w.voice_save.isChecked()
    assert w.voice_auto.isChecked()
    assert w.voice_silence.value()==1.5
    assert w.ai.ledger.totals()['month']['requests']==0
    w.voice_threshold.setValue(-55)
    w.voice_silence.setValue(2)
    w.voice_save.setChecked(True)
    assert w.voice.config.save_text and w.voice.config.threshold_db==-55
    w.voice.close()
    from our_little_days.speech_ui import load_voice_config
    assert load_voice_config(store).end_silence==2
    assert not list(store.path.parent.glob('*.wav'))


def test_unstored_transcript_preview_has_no_paid_call_when_ai_off(voice_window,store):
    w=voice_window;c=w.voice
    # Recognition result injected; do not open a real device.
    c.source=object();c.segment_worldline='main'
    c.recognized('蛤？',c.generation)
    assert w.voice_preview.toPlainText()=='蛤？'
    assert not c.send_timer.isActive()
    before=store.messages()
    c.deliver_preview()
    assert store.messages()==before
    assert w.ai.ledger.totals()['month']['requests']==0
    c.source=None
    c.cancel_preview()
    assert not c.pending_text


def test_auto_voice_delivery_persists_only_if_selected_and_uses_same_engine(voice_window,store,monkeypatch):
    w=voice_window;c=w.voice
    w.ai.enabled=True
    forwarded=[]
    monkeypatch.setattr(w.ai,'user_sent',lambda **kw: forwarded.append(kw) or True)
    c.source=object();c.segment_worldline='main'
    c.recognized('欸？',c.generation)
    assert c.send_timer.isActive()
    c.deliver_preview()
    assert forwarded[-1]=={'transient_text':'欸？','user_message_id':None,'include_image':True}
    assert not any(m['text']=='欸？' for m in store.messages())
    w.voice_save.setChecked(True)
    c.recognized('不是吧？',c.generation)
    c.deliver_preview()
    record=store.messages()[-1]
    assert record['text']=='不是吧？' and record['source_kind']=='voice'
    assert forwarded[-1]['user_message_id']==record['id']
    c.source=None


def test_cancel_preview_and_late_recognition_never_send(voice_window,store,monkeypatch):
    w=voice_window;c=w.voice
    w.ai.enabled=True
    forwarded=[]
    monkeypatch.setattr(w.ai,'user_sent',lambda **kw: forwarded.append(kw) or True)
    c.source=object();c.segment_worldline='main'
    c.recognized('蛤？',c.generation)
    c.cancel_preview();QTest.qWait(10)
    assert not forwarded and not c.send_timer.isActive()
    previous=c.generation
    c.source=None;c.stop()
    c.recognized('晚到的結果',previous)
    assert not c.pending_text and not forwarded


def test_rejected_retry_does_not_duplicate_saved_voice_message(voice_window,store,monkeypatch):
    w=voice_window;c=w.voice
    w.ai.enabled=True
    w.voice_save.setChecked(True)
    monkeypatch.setattr(w.ai,'user_sent',lambda **kw:False)
    c.source=object();c.segment_worldline='main'
    try:
        c.recognized('不是吧？',c.generation)
        c.deliver_preview();c.deliver_preview()
        assert sum(m['text']=='不是吧？' for m in store.messages())==1
        assert c.pending_text=='不是吧？'
    finally:
        c.source=None


def test_automatic_segment_recognition_preview_without_pressing_keys(voice_window,store,monkeypatch):
    w=voice_window;c=w.voice
    seen=[]
    def transcribe(pcm,config,cancelled):
        seen.append(pcm)
        return '蛤？何これ？'
    monkeypatch.setattr(c.recognizer,'transcribe',transcribe)
    c.source=object()
    try:
        segmenter=VoiceSegmenter(VoiceConfig(),EnergyDetector())
        for pcm in segmenter.feed(tone()*3+bytes(FRAME_BYTES)*75):
            c.enqueue(pcm)
        wait(lambda:c.worker is None)
        assert len(seen)==1 and w.voice_preview.toPlainText()=='蛤？何これ？'
        assert not c.send_timer.isActive() and w.ai.ledger.totals()['month']['requests']==0
        assert not list(store.path.parent.rglob('*.wav'))
    finally:
        c.source=None


def test_voice_payload_includes_current_selected_frame_and_no_audio(voice_window,store,monkeypatch):
    import our_little_days.ai_ui as ai_ui
    w=voice_window;c=w.ai
    from our_little_days.ai_cost import AIConfig
    from dataclasses import replace
    from PySide6.QtWidgets import QMessageBox
    c.config=replace(c.config,transmission_approved=True)
    c.enabled=True
    monkeypatch.setattr(c.keys,'read',lambda:'mock-key-only')
    monkeypatch.setattr(c.keys,'exists',lambda:True)
    seen=[]
    def fake(key,payload,cancelled):
        seen.append(payload)
        return {'id':'mock','usage':{'prompt_tokens':100,'completion_tokens':100},'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'meaningful':False,'event_key':'','summary':'','important':False,'messages':[]})}}]}
    monkeypatch.setattr(ai_ui,'request_api',fake)
    w.frame_image=QImage(64,64,QImage.Format.Format_RGB32);w.frame_image.fill(QColor('white'))
    assert c.user_sent(transient_text='蛤？何これ？',user_message_id=None,include_image=True)
    wait(lambda:c.worker is None)
    content=seen[0]['messages'][1]['content']
    context=json.loads(content[0]['text'])
    assert context['current_user_input']=={'kind':'voice_transcript','text':'蛤？何これ？','saved':False}
    assert len(context['characters'])==13
    assert content[1]['type']=='image_url'
    assert 'audio' not in json.dumps(seen[0])
    assert not any(m['text']=='蛤？何これ？' for m in store.messages())
    assert c.ledger.totals()['month']['requests']==1
