"""Manual mic on, then automatic local VAD/ASR; no press-to-talk requirement."""
from dataclasses import asdict
import json
import sys
import time
from collections import deque

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtMultimedia import QtAudio, QAudioFormat, QAudioSource, QMediaDevices
from PySide6.QtWidgets import QFileDialog

from .models import taipei_now
from .speech import (MODELS, PCMConverter, SpeechError, VoiceConfig, VoiceSegmenter,
                     WhisperRecognizer, download_model, is_microphone_name, model_path, rms_db)


def load_voice_config(store):
    row = store.connection.execute("SELECT value FROM settings WHERE key='voice'").fetchone()
    config = VoiceConfig(**json.loads(row[0])) if row else VoiceConfig()
    config.validate()
    return config


class RecognitionTask(QThread):
    recognized = Signal(str, int)
    failed = Signal(str, int)
    progress = Signal(str, int)

    def __init__(self, recognizer, pcm, config, generation, parent):
        super().__init__(parent)
        self.recognizer, self.pcm, self.config, self.generation = recognizer, pcm, config, generation

    def run(self):
        try:
            self.recognized.emit(self.recognizer.transcribe(self.pcm, self.config, self.isInterruptionRequested,
                progress=lambda stage: self.progress.emit(stage, self.generation)), self.generation)
        except Exception as exc:
            self.failed.emit(str(exc) if isinstance(exc, SpeechError) else '離線辨識失敗；未保存或上傳原始音訊。', self.generation)
        finally:
            self.pcm = None


class ModelTask(QThread):
    progress = Signal(int, int)
    complete = Signal(str)
    failed = Signal(str)

    def __init__(self, directory, model, parent, local_source=None):
        super().__init__(parent)
        self.directory, self.model, self.local_source = directory, model, local_source

    def run(self):
        try:
            path = download_model(self.directory, self.model, self.isInterruptionRequested, self.progress.emit, self.local_source)
            self.complete.emit(str(path))
        except Exception as exc:
            self.failed.emit(str(exc) if isinstance(exc, SpeechError) else '離線模型未準備完成。')


class VoiceController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.config = load_voice_config(window.store)
        self.source = self.input = None
        self.generation = 0
        self.worker = self.model_worker = None
        self.recognizer = WhisperRecognizer(window.store.path.parent)
        self.segmenter = self.converter = None
        self.queue = deque()
        self.pending_text = None
        self.pending_worldline = None
        self.saved_preview = None
        self.closed = False
        self.devices = QMediaDevices(self)
        self.devices.audioInputsChanged.connect(self.refresh_devices)
        self.send_timer = QTimer(self)
        self.send_timer.setSingleShot(True)
        self.send_timer.setInterval(2500)
        self.send_timer.timeout.connect(self.deliver_preview)
        self.recognition_timer = QTimer(self)
        self.recognition_timer.setInterval(1000)
        self.recognition_timer.timeout.connect(self.recognition_tick)
        self.recognition_phase = ''
        self.recognition_started = 0
        self.dropped_segments = 0
        self.refresh_devices()
        self.refresh()

    def refresh(self):
        if self.closed:
            return
        w = self.window
        w.voice_toggle.setText('關閉麥克風' if self.source else '開啟麥克風 · 自動偵測')
        w.voice_toggle.setEnabled(self.model_worker is None)
        w.voice_device.setEnabled(self.source is None)
        ready = model_path(w.store.path.parent, self.config.model).is_file()
        w.voice_model_note.setText(f"{MODELS[self.config.model]['label']}\n" + ('已下載；辨識前會驗證官方雜湊' if ready else '尚未下載模型 · 請先按下載或匯入'))
        w.voice_download.setEnabled(self.model_worker is None and self.worker is None)
        w.voice_import.setEnabled(self.model_worker is None and self.worker is None)
        w.voice_model.setEnabled(self.source is None and self.model_worker is None)
        for widget in (w.voice_threshold, w.voice_gain, w.voice_silence, w.voice_language):
            widget.setEnabled(self.source is None)
        w.voice_submit.setEnabled(self.pending_text is not None)
        w.voice_cancel.setEnabled(self.pending_text is not None)

    def read_controls(self):
        w = self.window
        value = VoiceConfig(threshold_db=w.voice_threshold.value(), gain_db=w.voice_gain.value(), end_silence=w.voice_silence.value(),
                            model=w.voice_model.currentData(), language=w.voice_language.currentData(), save_text=w.voice_save.isChecked(), auto_send=w.voice_auto.isChecked(),
                            device_id=bytes(w.voice_device.currentData().id()).hex() if w.voice_device.currentData() else '')
        value.validate()
        with w.store.connection:
            w.store.connection.execute("INSERT INTO settings VALUES('voice',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(asdict(value)),))
        self.config = value

    def preference_changed(self, *_):
        if self.closed:
            return
        try:
            self.read_controls()
            if not self.config.auto_send:
                self.send_timer.stop()
            self.refresh()
        except Exception:
            self.window.voice_note.setText('語音設定未保存；請檢查本機資料。')

    def refresh_devices(self):
        w = self.window
        if self.source:
            selected = self.config.device_id
            if not any(bytes(device.id()).hex() == selected for device in QMediaDevices.audioInputs()):
                self.stop()
                w.voice_note.setText('麥克風已移除；已停止收音，不改收其他裝置。')
        w.voice_device.blockSignals(True)
        w.voice_device.clear()
        if sys.platform == 'win32':
            for device in QMediaDevices.audioInputs():
                if is_microphone_name(device.description()):
                    w.voice_device.addItem(device.description(), device)
        if not w.voice_device.count():
            w.voice_device.addItem('沒有可用 Windows 麥克風', None)
        for index in range(w.voice_device.count()):
            device = w.voice_device.itemData(index)
            if device and bytes(device.id()).hex() == self.config.device_id:
                w.voice_device.setCurrentIndex(index)
        w.voice_device.blockSignals(False)

    def toggle(self):
        if self.source:
            self.stop()
            return
        self.start()

    def start(self):
        if self.worker:
            self.window.voice_note.setText('上一段辨識正在取消，請稍後再開啟。')
            return
        try:
            if sys.platform != 'win32':
                raise SpeechError('麥克風收音限 Windows；雲端不會開啟麥克風。')
            self.read_controls()
            device = self.window.voice_device.currentData()
            if not device or not is_microphone_name(device.description()):
                raise SpeechError('請選擇實際麥克風，不使用系統混音或迴路裝置。')
            if not model_path(self.window.store.path.parent, self.config.model).is_file():
                raise SpeechError('請先下載一次離線多語模型。')
            format = QAudioFormat()
            format.setSampleRate(16000); format.setChannelCount(1); format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if not device.isFormatSupported(format):
                format = device.preferredFormat()
            kind = {QAudioFormat.SampleFormat.Int16:'int16', QAudioFormat.SampleFormat.Int32:'int32', QAudioFormat.SampleFormat.Float:'float', QAudioFormat.SampleFormat.UInt8:'uint8'}.get(format.sampleFormat())
            self.converter = PCMConverter(format.sampleRate(), format.channelCount(), kind)
            self.segmenter = VoiceSegmenter(self.config)
            self.generation += 1
            self.source = QAudioSource(device, format, self)
            self.source.setBufferSize(max(4096, format.bytesForDuration(250000)))
            self.source.stateChanged.connect(self.state_changed)
            self.input = self.source.start()
            # start() can synchronously emit an error and state_changed() may stop it.
            if self.source is None:
                self.input = None
                return
            if self.input is None or self.source.error() != QtAudio.Error.NoError:
                error = self.source.error().name
                raise SpeechError(f'麥克風無法開啟（{error}）；請檢查 Windows 麥克風隱私權與裝置。')
            self.input.readyRead.connect(self.receive_audio)
            self.window.voice_status.setText('● 麥克風開啟 · 本機自動偵測')
            self.window.voice_note.setText('安靜時只執行本機 VAD；請戴耳機避免喇叭聲進入麥克風。')
            self.refresh()
        except Exception as exc:
            self.stop()
            self.window.voice_note.setText(str(exc) if isinstance(exc, SpeechError) else f'麥克風啟動失敗（{type(exc).__name__}）；請回報此錯誤代碼。')

    def state_changed(self, state):
        if self.source and self.source.error() != QtAudio.Error.NoError:
            error = self.source.error().name
            self.stop()
            self.window.voice_note.setText(f'麥克風中斷或無法開啟（{error}），已停止收音；請檢查 Windows 麥克風權限與輸入裝置。')

    def receive_audio(self):
        if not self.source or not self.input:
            return
        try:
            # Draining bounded chunks prevents one huge allocation after a UI delay.
            while self.source and self.input and self.input.bytesAvailable() > 0:
                raw = bytes(self.input.read(min(32768, self.input.bytesAvailable())))
                if not raw:
                    break
                pcm = self.converter.feed(raw)
                if not pcm:
                    continue
                level = rms_db(pcm)
                self.window.voice_level.setValue(round(max(0, min(100, (level+70)/70*100))))
                segments = self.segmenter.feed(pcm)
                state = '等待說話'
                if self.segmenter.speaking:
                    state = ('偵測到說話' if not self.segmenter.silence else
                             f'等待停語 {self.segmenter.silence*.02:.1f}／{self.config.end_silence:.1f} 秒')
                self.window.voice_level_label.setText(f'音量 {level:.1f} dBFS · {state}')
                for segment in segments:
                    self.enqueue(segment)
        except Exception:
            self.stop()
            self.window.voice_note.setText('本機收音處理失敗；已停止，不保存原始音訊。')

    def enqueue(self, pcm):
        if len(self.queue) >= 3:
            self.dropped_segments += 1
            self.recognition_tick()
            return
        self.queue.append((pcm, self.window.worldline))
        self.next_segment()

    def next_segment(self):
        if self.worker or not self.source or not self.queue:
            return
        pcm, worldline = self.queue.popleft()
        self.segment_worldline = worldline
        self.worker = RecognitionTask(self.recognizer, pcm, self.config, self.generation, self)
        self.worker.recognized.connect(self.recognized)
        self.worker.failed.connect(self.failed)
        self.worker.progress.connect(self.recognition_progress)
        self.worker.finished.connect(self.finished)
        self.recognition_started = time.monotonic()
        self.recognition_seconds = len(pcm)/32000
        self.recognition_phase = '準備本機離線辨識'
        self.recognition_timer.start()
        self.recognition_tick()
        self.worker.start()

    def recognition_progress(self, stage, generation):
        if self.closed or generation != self.generation or not self.source or not self.worker:
            return
        self.recognition_phase = stage
        self.recognition_tick()

    def recognition_tick(self):
        if self.closed or not self.source or not self.worker:
            self.recognition_timer.stop()
            return
        elapsed = int(time.monotonic()-self.recognition_started)
        note = (f'{self.recognition_phase} · {self.worker.config.model}\n'
                f'{self.recognition_seconds:.1f} 秒語音 · 已等待 {elapsed} 秒 · 待處理 {len(self.queue)} 段\n'
                '原始音訊不保存／上傳')
        if elapsed >= 20:
            note += '\n可關閉麥克風取消；較慢時可改用 base／tiny。'
        if self.dropped_segments:
            note += f'\n辨識忙碌，已略過 {self.dropped_segments} 段；正在辨識的語音保留。'
        self.window.voice_note.setText(note)

    def recognized(self, text, generation):
        if self.closed or generation != self.generation or not self.source or self.segment_worldline != self.window.worldline:
            return
        self.recognition_timer.stop()
        if not text.strip():
            self.window.voice_note.setText('這段沒有可辨識文字；不呼叫 AI。')
            return
        # Never overwrite an unsent preview: keep two utterances as one pending turn.
        existing = self.window.voice_preview.toPlainText().strip() if self.pending_text is not None else ''
        self.pending_text = (existing+'\n'+text).strip()
        if len(self.pending_text) > 4000:
            self.stop()
            self.window.voice_note.setText('待送辨識文字超過上限，已停止麥克風。')
            return
        self.pending_worldline = self.window.worldline
        self.window.voice_preview.setPlainText(self.pending_text)
        if self.config.auto_send and self.window.ai and self.window.ai.enabled:
            self.send_timer.start()
            self.window.voice_note.setText('2.5 秒後自動送給住戶；可修改或取消，無需按鍵說話。')
        else:
            self.window.voice_note.setText('辨識預覽已就緒；AI 未啟用或自動送出關閉，不呼叫付費 API。')
        self.refresh()

    def deliver_preview(self):
        self.send_timer.stop()
        if self.pending_text is None:
            return
        w = self.window
        text = w.voice_preview.toPlainText().strip()
        if not text or len(text)>4000 or self.pending_worldline != w.worldline:
            self.cancel_preview()
            return
        if not w.ai or not w.ai.enabled:
            w.voice_note.setText('文字 AI 尚未啟用；辨識文字留在預覽，不呼叫 API。')
            return
        try:
            anchor = None
            if w.voice_save.isChecked():
                if self.saved_preview and self.saved_preview[:2] == (text, w.worldline):
                    anchor = self.saved_preview[2]
                else:
                    record = w.store.append_message(speaker_id='user', speaker_name='☾ 月鹿 · 語音', text=text, worldline=w.worldline, source_kind='voice')
                    anchor = record['id']
                    self.saved_preview = (text, w.worldline, anchor)
                    w.render_message(record)
            accepted = w.ai.user_sent(transient_text=text, user_message_id=anchor, include_image=True)
            if not accepted:
                w.voice_note.setText('住戶引擎未接受這段文字；請先檢查 AI 預算／忙碌狀態，預覽保留。')
                return
            w.voice_note.setText('文字已交給現有住戶引擎（同一預算）；原始音訊沒有上傳。')
            self.pending_text = self.pending_worldline = None
            self.saved_preview = None
            self.refresh()
        except Exception:
            self.stop()
            w.voice_note.setText('辨識文字未能送出；已停止自動收音，請保留預覽內容。')

    def cancel_preview(self):
        self.send_timer.stop()
        self.pending_text = self.pending_worldline = None
        self.saved_preview = None
        self.window.voice_preview.clear()
        self.window.voice_note.setText('已取消本段文字；未送出新的 AI 請求。')
        self.refresh()

    def failed(self, text, generation):
        if self.closed or generation != self.generation:
            return
        self.stop()
        self.window.voice_note.setText(text)

    def finished(self):
        if self.closed:
            return
        self.recognition_timer.stop()
        task = self.worker
        self.worker = None
        if task:
            task.deleteLater()
        self.refresh()
        self.next_segment()

    def prepare_model(self, import_local=False):
        if self.model_worker:
            return
        self.stop()
        self.read_controls()
        filename = None
        if import_local:
            filename, _ = QFileDialog.getOpenFileName(self.window, '匯入官方多語 ggml 模型（雜湊驗證）', '', 'Whisper model (*.bin)')
            if not filename:
                return
        self.model_worker = ModelTask(self.window.store.path.parent, self.config.model, self, filename)
        self.model_worker.progress.connect(lambda current,total: self.window.voice_note.setText(f'下載／匯入模型 {current/1048576:.1f} MiB' + (f'／{total/1048576:.1f} MiB' if total else '') + ' · 不上傳音訊'))
        self.model_worker.complete.connect(lambda path: self.window.voice_note.setText('離線多語模型已驗證，現在可開啟麥克風。'))
        self.model_worker.failed.connect(lambda text: self.window.voice_note.setText(text))
        self.model_worker.finished.connect(self.model_finished)
        self.model_worker.start()
        self.refresh()

    def model_finished(self):
        if self.closed:
            return
        task = self.model_worker
        self.model_worker = None
        if task:
            task.deleteLater()
        self.refresh()

    def stop(self):
        self.generation += 1
        self.send_timer.stop()
        self.recognition_timer.stop()
        self.queue.clear()
        self.dropped_segments = 0
        if self.source:
            self.source.blockSignals(True)
            if self.input:
                try:
                    self.input.readyRead.disconnect(self.receive_audio)
                except (RuntimeError, TypeError):
                    pass
            self.source.stop()
            self.source.deleteLater()
        self.source = self.input = self.segmenter = self.converter = None
        if self.worker:
            self.worker.requestInterruption()
        self.pending_text = self.pending_worldline = None
        self.saved_preview = None
        self.window.voice_level.setValue(0)
        self.window.voice_status.setText('○ 麥克風關閉')
        self.window.voice_level_label.setText('沒有收音')
        self.window.voice_note.setText('麥克風已關閉 · 不保存原始音訊')
        self.refresh()

    def close(self):
        if self.closed:
            return
        self.stop()
        self.closed = True
        for task in (self.worker, self.model_worker):
            if task:
                task.requestInterruption()
                task.wait()
                task.deleteLater()
        self.worker = self.model_worker = None
