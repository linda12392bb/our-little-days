"""Local-only microphone segmentation and offline Whisper transport. No audio files."""
from collections import deque
from dataclasses import dataclass
import hashlib
import io
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import wave

import webrtcvad
from opencc import OpenCC

RATE = 16000
FRAME_BYTES = 640  # 20 ms, mono signed 16-bit PCM.
MODELS = {
    'base': {'sha1': '465707469ff3a37a2b9b8d8f89f2f99de7299dac', 'size_limit': 160_000_000, 'label': 'base 多語（約 142 MiB，建議）'},
    'tiny': {'sha1': 'bd577a113a864445d4c299885e0cb97d4ba92b5f', 'size_limit': 85_000_000, 'label': 'tiny 多語（約 75 MiB，較快）'},
    'small': {'sha1': '55356645c2b361a969dfd0ef2c5a50d530afd8d5', 'size_limit': 510_000_000, 'label': 'small 多語（約 466 MiB，較慢）'},
}
MODEL_SOURCE = 'https://raw.githubusercontent.com/ggml-org/whisper.cpp/v1.8.2/models/README.md'


class SpeechError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoiceConfig:
    threshold_db: float = -48.0
    gain_db: float = 6.0
    end_silence: float = 1.5
    model: str = 'base'
    language: str = 'auto'
    save_text: bool = False
    auto_send: bool = True
    device_id: str = ''

    def validate(self):
        if not -65 <= self.threshold_db <= -25 or not 0 <= self.gain_db <= 20 or not .5 <= self.end_silence <= 3:
            raise SpeechError('靈敏度、增益或分句等待時間不正確。')
        if self.model not in MODELS or self.language not in {'auto', 'zh', 'ja'}:
            raise SpeechError('請選擇多語模型與支援語言。')


def rms_db(pcm):
    samples = struct.unpack('<' + 'h' * (len(pcm)//2), pcm)
    rms = math.sqrt(sum(v*v for v in samples)/max(1, len(samples))) / 32768
    return max(-120.0, 20*math.log10(max(rms, 1e-6)))


def amplify(pcm, db):
    samples = struct.unpack('<' + 'h' * (len(pcm)//2), pcm)
    gain = 10**(db/20)
    return struct.pack('<'+'h'*len(samples), *(max(-32768, min(32767, round(v*gain))) for v in samples))


class VoiceSegmenter:
    """Permissive WebRTC VAD + adjustable energy gate; 60ms voiced words accepted."""
    def __init__(self, config, detector=None):
        config.validate()
        self.config = config
        self.vad = detector or webrtcvad.Vad(0)
        self.pending = bytearray()
        self.pre_roll = deque(maxlen=12)
        self.active = []
        self.voiced = self.silence = self.hits = 0

    @property
    def speaking(self):
        return bool(self.active)

    def feed(self, pcm):
        self.pending.extend(pcm)
        segments = []
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES]); del self.pending[:FRAME_BYTES]
            frame = amplify(frame, self.config.gain_db)
            speech = rms_db(frame) >= self.config.threshold_db and self.vad.is_speech(frame, RATE)
            if not self.active:
                self.pre_roll.append(frame)
                self.hits = self.hits+1 if speech else 0
                if self.hits >= 2:
                    self.active = list(self.pre_roll)
                    self.voiced = self.hits
                    self.silence = 0
                continue
            self.active.append(frame)
            if speech:
                self.voiced += 1
                self.silence = 0
            else:
                self.silence += 1
            if self.silence*0.02 >= self.config.end_silence or len(self.active) >= 750:
                if self.voiced >= 3:
                    # Keep <=300ms trailing silence, not the full 1.5s endpoint wait.
                    keep = max(1, len(self.active)-max(0, self.silence-15))
                    segments.append(b''.join(self.active[:keep]))
                self.active.clear(); self.pre_roll.clear()
                self.voiced = self.silence = self.hits = 0
        return segments


class PCMConverter:
    """Streaming mono linear resampler for Qt Int16/Int32/Float/UInt8 formats."""
    def __init__(self, rate, channels, kind):
        if not 8000 <= rate <= 192000 or not 1 <= channels <= 8 or kind not in {'int16', 'int32', 'float', 'uint8'}:
            raise SpeechError('不支援這個麥克風音訊格式。')
        self.rate, self.channels, self.kind = rate, channels, kind
        self.bytes_per_sample = {'int16': 2, 'int32': 4, 'float': 4, 'uint8': 1}[kind]
        self.partial = bytearray()
        self.samples = []
        self.position = 0.0

    def feed(self, raw):
        self.partial.extend(raw)
        frame_bytes = self.bytes_per_sample*self.channels
        count = len(self.partial)//frame_bytes*frame_bytes
        raw = bytes(self.partial[:count]); del self.partial[:count]
        code = {'int16': 'h', 'int32': 'i', 'float': 'f', 'uint8': 'B'}[self.kind]
        values = struct.unpack('<'+code*(len(raw)//self.bytes_per_sample), raw)
        for i in range(0, len(values), self.channels):
            vals = values[i:i+self.channels]
            mono = sum(vals)/self.channels
            mono = (mono-128)/128 if self.kind == 'uint8' else mono/({'int16':32768, 'int32':2147483648}.get(self.kind, 1))
            self.samples.append(max(-1, min(1, mono)) if math.isfinite(mono) else 0)
        output = []
        step = self.rate/RATE
        while self.position+1 < len(self.samples):
            left = int(self.position); fraction = self.position-left
            value = self.samples[left]*(1-fraction)+self.samples[left+1]*fraction
            output.append(max(-32768, min(32767, round(value*32768))))
            self.position += step
        consumed = min(int(self.position), max(0, len(self.samples)-1))
        if consumed:
            del self.samples[:consumed]; self.position -= consumed
        return struct.pack('<'+'h'*len(output), *output)


def is_microphone_name(description):
    # AudioInputs excludes playback endpoints; reject known capture-loopback devices.
    name = description.casefold()
    return not any(word in name for word in ('stereo mix','立體聲混音','立体声混音','what u hear','loopback','迴路','回环','cable output','vb-audio','virtual cable','聲音混音'))


def model_path(directory, model):
    return Path(directory)/'speech-models'/f'ggml-{model}.bin'


def verify_model(path, model, cancelled=lambda: False):
    digest = hashlib.sha1()
    size = 0
    try:
        with Path(path).open('rb') as stream:
            while block := stream.read(1024*1024):
                if cancelled():
                    raise SpeechError('模型檢查已取消。')
                size += len(block)
                if size > MODELS[model]['size_limit']:
                    raise SpeechError('模型大小不正確。')
                digest.update(block)
    except OSError:
        raise SpeechError('離線模型尚未下載，或無法讀取。') from None
    if digest.hexdigest() != MODELS[model]['sha1']:
        raise SpeechError('離線模型雜湊不符合官方 v1.8.2 清單；不會載入。')


def download_model(directory, model, cancelled, progress, local_source=None):
    destination = model_path(directory, model)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix('.download')
    source = None
    try:
        if local_source:
            source = Path(local_source).open('rb')
            total = Path(local_source).stat().st_size
        else:
            # Download weights only, never microphone audio or transcript.
            source = urllib.request.urlopen(f'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{model}.bin', timeout=5)
            total = int(source.headers.get('Content-Length', 0))
        copied = 0
        with partial.open('wb') as output:
            while block := source.read(256*1024):
                if cancelled():
                    raise SpeechError('模型下載已取消。')
                copied += len(block)
                if copied > MODELS[model]['size_limit']:
                    raise SpeechError('下載模型大小超出預期。')
                output.write(block)
                progress(copied, total)
        verify_model(partial, model, cancelled)
        if cancelled():
            raise SpeechError('模型下載已取消。')
        partial.replace(destination)
        return destination
    except SpeechError:
        raise
    except Exception:
        raise SpeechError('模型下載／匯入失敗。請檢查網路或從官方下載多語模型後匯入；原始音訊不會上傳。') from None
    finally:
        if source:
            source.close()
        partial.unlink(missing_ok=True)


def runtime_folder():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent/'speech-runtime'
    return Path(__file__).resolve().parents[2]/'packaging/speech-runtime'


def normalize_text(text):
    converter = OpenCC('s2twp')
    # Preserve Japanese clauses with kana; convert Chinese-only clauses to TW form.
    return ''.join(part if re.search('[ぁ-ゖァ-ヺ]', part) else converter.convert(part) for part in re.split(r'([，。！？、\n])', text)).strip()


def recognize_in_memory(command, pcm, cancelled, *, cwd=None, env=None, timeout=90, progress=None):
    """Feed a WAV through stdin, cancellably; never create an audio file."""
    if cancelled():
        raise SpeechError('離線辨識已取消。')
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE); wav.writeframes(pcm)
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   cwd=cwd, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except Exception:
        audio.close()
        raise SpeechError('無法啟動離線辨識；請檢查執行檔與 CPU 相容性。') from None
    result = {}
    completed = threading.Event()
    def communicate():
        try:
            result['data'] = process.communicate(input=audio.getvalue())
        except Exception:
            result['error'] = True
        finally:
            completed.set()
    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    deadline = time.monotonic()+timeout
    try:
        if progress:
            progress('Whisper 載入模型／辨識中')
        while not completed.wait(.05):
            if cancelled() or time.monotonic() >= deadline:
                raise SpeechError('離線辨識已取消或逾時；音訊不保存。')
        if cancelled():
            raise SpeechError('離線辨識已取消。')
        if process.returncode != 0 or 'data' not in result:
            raise SpeechError(f'離線辨識失敗（退出碼 {process.returncode}）；請檢查模型與 CPU 相容性。')
        text = result['data'][0].decode('utf-8', errors='replace').strip()
        if len(text) > 4000:
            raise SpeechError('辨識文字過長，請分句。')
        return normalize_text(text)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        thread.join(timeout=3)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe and not pipe.closed:
                pipe.close()
        result.clear()
        audio.close()


def whisper_command(folder, path, config):
    # v1.8.2 defaults output basename to the input "-", which disables the
    # transcript callback for stdin. A non-"-" basename restores stdout text.
    # No --output-* format is enabled, so this creates no transcript/audio file.
    return [str(Path(folder)/'whisper-cli.exe'), '-m', str(path), '-f', '-',
            '-of', 'stdout-transcript', '-l', config.language,
            '-t', str(max(1, min(4, os.cpu_count() or 2))), '-nt', '-np', '-ng',
            '--suppress-nst', '--prompt', '繁體中文、日本語。']


class WhisperRecognizer:
    def __init__(self, directory):
        self.directory = directory
        self.verified = None

    def transcribe(self, pcm, config, cancelled, progress=None):
        config.validate()
        if sys.platform != 'win32':
            raise SpeechError('此試用版的離線 Whisper 執行檔限 Windows x64。')
        path = model_path(self.directory, config.model)
        try:
            stamp = (str(path), path.stat().st_size, path.stat().st_mtime_ns)
        except OSError:
            raise SpeechError('請先下載離線多語模型。') from None
        if stamp != self.verified:
            if progress:
                progress('驗證離線模型完整性')
            verify_model(path, config.model, cancelled)
            self.verified = stamp
        folder = runtime_folder()
        manifest = __import__('json').loads((folder/'SOURCE.json').read_text())
        for name, digest in manifest['files_sha256'].items():
            if hashlib.sha256((folder/name).read_bytes()).hexdigest() != digest:
                raise SpeechError('離線辨識執行檔完整性檢查失敗。')
        env = os.environ.copy()
        env['PATH'] = str(folder)+os.pathsep+str(Path(sys.executable).parent/'_internal')+os.pathsep+env.get('PATH','')
        command = whisper_command(folder, path, config)
        return recognize_in_memory(command, pcm, cancelled, cwd=folder, env=env,
                                   timeout=180 if config.model == 'small' else 90, progress=progress)
