"""Windows apartment desktop with explicit, budgeted AI controls."""
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMainWindow,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QVBoxLayout,
    QWidget, QPlainTextEdit, QTabWidget, QMessageBox, QDoubleSpinBox, QProgressBar,
)

from .models import MonitorState, is_night, taipei_now
from .ai_engine import ChangeGate
import json


def label(text="", object_name="", wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if object_name:
        widget.setObjectName(object_name)
    return widget


class CaptureTask(QThread):
    captured = Signal(object, int)
    failed = Signal(str, int)

    def __init__(self, backend, window, generation, parent):
        super().__init__(parent)
        self.backend = backend
        self.window = window
        self.generation = generation

    def run(self):
        try:
            frame = self.backend.capture(self.window, self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.captured.emit(frame, self.generation)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(str(exc), self.generation)


class ChatInput(QPlainTextEdit):
    send_requested = Signal()

    def __init__(self):
        super().__init__()
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if not self.composing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.send_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class MessageCard(QFrame):
    def __init__(self, name, text, translation, time, character=None, user=False):
        super().__init__()
        self.character, self.user = character, user
        self.setObjectName("messageCard")
        self.setMaximumWidth(700)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 11, 15, 11)
        layout.setSpacing(7)
        heading = QHBoxLayout()
        self.name_label = label(name, "messageName")
        heading.addWidget(self.name_label)
        heading.addStretch()
        heading.addWidget(label(time, "messageTime"))
        layout.addLayout(heading)
        content = label(text, "messageText", True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(content)
        if translation:
            self.translation_label = label(f"（{translation}）", "translation", True)
            self.translation_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self.translation_label)

    def apply_theme(self, night):
        if self.character:
            background = self.character.night_bubble if night else self.character.day_bubble
            foreground = "#FFFFFF" if night else self.character.day_text
        elif self.user:
            background, foreground = ("#794B6A", "#FFFFFF") if night else ("#E8C4DC", "#633B58")
        else:
            background, foreground = ("#493747", "#F9E9F3") if night else ("#EDE5DC", "#64475D")
        self.setStyleSheet(f"""
            QFrame#messageCard {{ background: {background}; border: none; border-radius: 15px; }}
            QFrame#messageCard QLabel {{ color: {foreground}; background: transparent; border: none; }}
            QLabel#messageName {{ font-weight: 600; }}
            QLabel#messageTime, QLabel#translation {{ font-size: 11px; }}
        """)


class MainWindow(QMainWindow):
    def __init__(self, backend, characters, store):
        super().__init__()
        self.backend, self.characters = backend, characters
        self.store = store
        self.worldline = store.settings()["active_worldline"]
        self.state = MonitorState.STOPPED
        self.target = None
        self.generation = 0
        self.worker = None
        self.cards = []
        self.frame_image = None
        self.night = False
        self.follow_latest = True
        self.scrolling_programmatically = False
        self.setWindowTitle("月色不打烊｜公寓還是一樣很吵")
        self.resize(1200, 800)
        self.setMinimumSize(920, 640)
        self.ai = None
        self.voice = None
        self._build_ui()
        from .ai_ui import AIController
        try:
            self.ai = AIController(self)
            self.ai.refresh()
        except Exception:
            self.ai_note.setText("API 用量資料無法載入；AI 已停用，不會重置資料。")
        from .speech_ui import VoiceController
        try:
            self.voice = VoiceController(self)
        except Exception:
            self.voice_note.setText("本機語音設定無法載入；麥克風保持關閉，不重置資料。")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.request_capture)
        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(30_000)
        self.theme_timer.timeout.connect(self.apply_theme)
        self.theme_timer.timeout.connect(lambda: self.ai.refresh() if self.ai else None)
        self.theme_timer.start()
        self.apply_theme()
        self.refresh_windows()
        self.load_history()
        if not self.cards:
            self.add_message("☾ 公寓便箋", "歡迎回家。先選擇想一起看的視窗，再按「開始陪伴」。", "", system=True)
            self.add_message("☾ 公寓便箋", "AI 預設關閉。請到 API 安全設定輸入金鑰，先做兩項單次成本測試；角色、聊天與記憶會儲存在本機。", "", system=True)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 16)
        outer.setSpacing(16)
        header = QHBoxLayout()
        header.addWidget(label("☾", "moon"))
        titles = QVBoxLayout()
        titles.addWidget(label("月色不打烊", "title"))
        titles.addWidget(label("公寓還是一樣很吵  /  OUR LITTLE DAYS · 2026", "subtitle"))
        header.addLayout(titles)
        header.addStretch()
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["台灣時間自動", "日間 · 奶油日記", "夜間 · 月色公寓"])
        self.theme_combo.currentIndexChanged.connect(self.apply_theme)
        header.addWidget(self.theme_combo)
        self.topmost = QCheckBox("永遠置頂")
        self.topmost.toggled.connect(self.toggle_topmost)
        header.addWidget(self.topmost)
        outer.addLayout(header)

        status = QHBoxLayout()
        self.capture_status = label("○ 尚未開始", "status")
        self.api_status = label("API 離線 · 尚未串接", "status")
        status.addWidget(self.capture_status)
        status.addWidget(self.api_status)
        self.voice_status = label("○ 麥克風關閉", "status")
        status.addWidget(self.voice_status)
        status.addStretch()
        self.clock = label("", "subtitle")
        status.addWidget(self.clock)
        outer.addLayout(status)

        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(12)
        outer.addWidget(columns, 1)

        # Full roster; statuses reflect only messages actually inserted.
        roster = QFrame()
        roster.setObjectName("panel")
        roster_layout = QVBoxLayout(roster)
        roster_layout.setContentsMargins(16, 16, 16, 16)
        roster_layout.addWidget(label("公寓住戶", "sectionTitle"))
        roster_layout.addWidget(label("十三聲道 · 都在名單裡", "hint"))
        self.roster_labels = {}
        for character in self.characters:
            row = QVBoxLayout()
            row.setSpacing(1)
            row.addWidget(label(f"{character.icon} {character.name}", "resident"))
            state = label("· 未出場", "hint")
            self.roster_labels[character.id] = state
            row.addWidget(state)
            roster_layout.addLayout(row)
        roster_layout.addStretch()
        roster_layout.addWidget(label("○ 已回覆\n✓ 已讀未回\n· 未出場", "hint", True))
        roster_scroll = QScrollArea()
        roster_scroll.setWidgetResizable(True)
        roster_scroll.setFrameShape(QFrame.Shape.NoFrame)
        roster_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        roster_scroll.setMinimumWidth(170)
        roster_scroll.setMaximumWidth(210)
        roster_scroll.setWidget(roster)
        columns.addWidget(roster_scroll)

        chat = QFrame()
        chat.setObjectName("panel")
        chat.setMinimumWidth(310)
        chat_layout = QVBoxLayout(chat)
        chat_layout.setContentsMargins(18, 16, 18, 14)
        chat_heading = QHBoxLayout()
        chat_heading.addWidget(label("今天，也一起待著。", "sectionTitle"))
        chat_heading.addStretch()
        clear = QPushButton("清空")
        clear.setToolTip("確認後刪除目前世界線的聊天；角色關係與長期記憶保留。")
        clear.clicked.connect(self.clear_chat)
        chat_heading.addWidget(clear)
        chat_layout.addLayout(chat_heading)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.messages = QWidget()
        self.messages.setObjectName("messages")
        self.message_layout = QVBoxLayout(self.messages)
        self.message_layout.setContentsMargins(0, 8, 5, 8)
        self.message_layout.setSpacing(12)
        self.message_layout.addStretch()
        self.scroll.setWidget(self.messages)
        chat_layout.addWidget(self.scroll, 1)
        self.jump_button = QPushButton("↓ 回到最新訊息")
        self.jump_button.clicked.connect(self.scroll_to_bottom)
        self.jump_button.hide()
        chat_layout.addWidget(self.jump_button)
        self.scroll.verticalScrollBar().valueChanged.connect(self.scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.scroll_range_changed)
        composer = QHBoxLayout()
        self.chat_input = ChatInput()
        self.chat_input.setPlaceholderText("☾ 月鹿，想說什麼？（AI 未啟用時仍會保存訊息）")
        self.chat_input.setFixedHeight(64)
        self.chat_input.send_requested.connect(self.send_message)
        composer.addWidget(self.chat_input, 1)
        self.send_button = QPushButton("送出 ↗")
        self.send_button.setObjectName("primary")
        self.send_button.clicked.connect(self.send_message)
        composer.addWidget(self.send_button)
        chat_layout.addLayout(composer)
        chat_layout.addWidget(label("Enter 送出 · Shift＋Enter 換行 · 聊天自動保存，截圖不保存", "hint"))
        columns.addWidget(chat)

        controls = QFrame()
        controls.setObjectName("panel")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 16, 16, 16)
        controls_layout.setSpacing(12)
        controls_layout.addWidget(label("一起看哪個視窗？", "sectionTitle"))
        controls_layout.addWidget(label(self.backend.label, "hint", True))
        self.window_combo = QComboBox()
        self.window_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.window_combo.currentIndexChanged.connect(self.select_window)
        controls_layout.addWidget(self.window_combo)
        self.refresh_button = QPushButton("↻ 重新整理視窗")
        self.refresh_button.clicked.connect(self.refresh_windows)
        controls_layout.addWidget(self.refresh_button)
        self.target_label = label("尚未選定監看視窗", "hint", True)
        controls_layout.addWidget(self.target_label)
        self.preview = label("還沒有畫面\n開始後，只擷取選定視窗", "preview", True)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(140)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        controls_layout.addWidget(self.preview)
        self.preview_info = label("預覽不會寫入磁碟", "hint", True)
        controls_layout.addWidget(self.preview_info)
        self.error_label = label("", "error", True)
        self.error_label.setAccessibleName("擷取錯誤")
        self.error_label.hide()
        controls_layout.addWidget(self.error_label)
        interval_row = QHBoxLayout()
        interval_row.addWidget(label("擷取間隔", "hint"))
        interval_row.addStretch()
        self.interval = QSpinBox()
        self.interval.setRange(1, 60)
        self.interval.setValue(6)
        self.interval.setSuffix(" 秒")
        self.interval.valueChanged.connect(self.update_interval)
        interval_row.addWidget(self.interval)
        controls_layout.addLayout(interval_row)
        self.start_button = QPushButton("▷ 開始陪伴")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start_monitoring)
        controls_layout.addWidget(self.start_button)
        buttons = QHBoxLayout()
        self.pause_button = QPushButton("Ⅱ 暫停")
        self.pause_button.clicked.connect(self.pause_monitoring)
        self.stop_button = QPushButton("□ 停止")
        self.stop_button.clicked.connect(self.stop_monitoring)
        buttons.addWidget(self.pause_button)
        buttons.addWidget(self.stop_button)
        controls_layout.addLayout(buttons)
        controls_layout.addWidget(label("訊息氣泡測試", "sectionTitle"))
        self.character_combo = QComboBox()
        for character in self.characters:
            self.character_combo.addItem(f"{character.icon} {character.name}", character)
        controls_layout.addWidget(self.character_combo)
        test_button = QPushButton("＋ 加入測試訊息")
        test_button.clicked.connect(self.add_test_message)
        controls_layout.addWidget(test_button)
        inspect_button = QPushButton("查看這位住戶的設定")
        inspect_button.clicked.connect(self.inspect_character)
        controls_layout.addWidget(inspect_button)
        memory_button = QPushButton("記憶、事件與備份")
        memory_button.clicked.connect(self.manage_memory)
        controls_layout.addWidget(memory_button)
        controls_layout.addWidget(label("AI 與費用控制", "sectionTitle"))
        settings_button = QPushButton("API 安全設定／月預算")
        settings_button.clicked.connect(lambda: self.ai.settings() if self.ai else None)
        controls_layout.addWidget(settings_button)
        self.ai_usage = label("本月剩餘預算 NT$500.00 · AI 關閉", "hint", True)
        controls_layout.addWidget(self.ai_usage)
        self.ai_screen_test = QPushButton("單次畫面分析 · 測量費用")
        self.ai_screen_test.clicked.connect(lambda: self.ai.start("screen_test") if self.ai else None)
        controls_layout.addWidget(self.ai_screen_test)
        self.ai_reply_test = QPushButton("單次所選角色回覆 · 測量費用")
        self.ai_reply_test.clicked.connect(lambda: self.ai.start("reply_test") if self.ai else None)
        controls_layout.addWidget(self.ai_reply_test)
        self.ai_enable = QCheckBox("啟用文字 AI 回覆")
        self.ai_enable.toggled.connect(lambda checked: self.ai.enable(checked) if self.ai else None)
        controls_layout.addWidget(self.ai_enable)
        self.ai_auto = QCheckBox("啟用畫面事件自主陪伴")
        self.ai_auto.toggled.connect(lambda checked: self.ai.auto(checked) if self.ai else None)
        controls_layout.addWidget(self.ai_auto)
        ai_pause = QPushButton("暫停所有 AI · 保留記憶")
        ai_pause.clicked.connect(lambda: self.ai.pause() if self.ai else None)
        controls_layout.addWidget(ai_pause)
        self.ai_note = label("AI 關閉 · 不會呼叫 API", "hint", True)
        controls_layout.addWidget(self.ai_note)
        controls_layout.addWidget(label("語音輸入 · 本機離線", "sectionTitle"))
        from .speech_ui import load_voice_config
        from .speech import MODELS, VoiceConfig
        try:
            voice_settings = load_voice_config(self.store)
        except Exception:
            voice_settings = VoiceConfig()
        self.voice_device = QComboBox()
        controls_layout.addWidget(self.voice_device)
        refresh_mic = QPushButton("重新整理麥克風")
        refresh_mic.clicked.connect(lambda: self.voice.refresh_devices() if self.voice else None)
        controls_layout.addWidget(refresh_mic)
        self.voice_model = QComboBox()
        for name, value in MODELS.items():
            self.voice_model.addItem(value['label'], name)
        self.voice_model.setCurrentIndex(self.voice_model.findData(voice_settings.model))
        controls_layout.addWidget(self.voice_model)
        self.voice_model_note = label("首次下載模型後離線使用", "hint", True)
        controls_layout.addWidget(self.voice_model_note)
        self.voice_download = QPushButton("下載所選離線模型（一次）")
        self.voice_download.clicked.connect(lambda: self.voice.prepare_model() if self.voice else None)
        controls_layout.addWidget(self.voice_download)
        self.voice_import = QPushButton("匯入已下載的官方模型…")
        self.voice_import.clicked.connect(lambda: self.voice.prepare_model(True) if self.voice else None)
        controls_layout.addWidget(self.voice_import)
        self.voice_language = QComboBox()
        for name, code in [("自動 · 繁中／日文／混合", "auto"), ("繁體中文優先", "zh"), ("日本語優先", "ja")]:
            self.voice_language.addItem(name, code)
        self.voice_language.setCurrentIndex(self.voice_language.findData(voice_settings.language))
        controls_layout.addWidget(self.voice_language)
        for attr, title, low, high, value, suffix in [
            ('voice_threshold', '靈敏度門檻（越低越敏感）', -65, -25, voice_settings.threshold_db, ' dBFS'),
            ('voice_gain', '輕聲增益', 0, 20, voice_settings.gain_db, ' dB'),
            ('voice_silence', '說話結束等待', .5, 3, voice_settings.end_silence, ' 秒'),
        ]:
            controls_layout.addWidget(label(title, "hint", True))
            spin = QDoubleSpinBox()
            spin.setDecimals(1); spin.setRange(low, high); spin.setValue(value); spin.setSuffix(suffix)
            setattr(self, attr, spin)
            controls_layout.addWidget(spin)
        self.voice_toggle = QPushButton("開啟麥克風 · 自動偵測")
        self.voice_toggle.clicked.connect(lambda: self.voice.toggle() if self.voice else None)
        controls_layout.addWidget(self.voice_toggle)
        self.voice_level = QProgressBar()
        self.voice_level.setRange(0, 100); self.voice_level.setValue(0); self.voice_level.setTextVisible(False)
        controls_layout.addWidget(self.voice_level)
        self.voice_level_label = label("沒有收音", "hint")
        controls_layout.addWidget(self.voice_level_label)
        self.voice_save = QCheckBox("永久保存辨識文字到聊天")
        self.voice_save.setChecked(voice_settings.save_text)
        controls_layout.addWidget(self.voice_save)
        self.voice_auto = QCheckBox("預覽後自動交給住戶（需文字 AI）")
        self.voice_auto.setChecked(voice_settings.auto_send)
        controls_layout.addWidget(self.voice_auto)
        self.voice_preview = QPlainTextEdit()
        self.voice_preview.setPlaceholderText("辨識文字預覽 · 可修改或取消")
        self.voice_preview.setMaximumHeight(90)
        controls_layout.addWidget(self.voice_preview)
        self.voice_submit = QPushButton("送出這段辨識文字")
        self.voice_submit.clicked.connect(lambda: self.voice.deliver_preview() if self.voice else None)
        controls_layout.addWidget(self.voice_submit)
        self.voice_cancel = QPushButton("取消這段文字")
        self.voice_cancel.clicked.connect(lambda: self.voice.cancel_preview() if self.voice else None)
        controls_layout.addWidget(self.voice_cancel)
        self.voice_note = label("麥克風預設關閉；不保存／上傳原始音訊。\n請戴耳機減少遊戲／動畫聲進入麥克風。", "hint", True)
        controls_layout.addWidget(self.voice_note)
        for widget in (self.voice_model, self.voice_language, self.voice_device):
            widget.currentIndexChanged.connect(lambda *_: self.voice.preference_changed() if self.voice else None)
        for widget in (self.voice_threshold, self.voice_gain, self.voice_silence):
            widget.valueChanged.connect(lambda *_: self.voice.preference_changed() if self.voice else None)
        for widget in (self.voice_save, self.voice_auto):
            widget.toggled.connect(lambda *_: self.voice.preference_changed() if self.voice else None)
        controls_layout.addStretch()
        controls_layout.addWidget(label("Windows 本機 · 語音試用 0.3.1", "sectionTitle"))
        controls_layout.addWidget(label("聊天與角色資料永久保存在本機。\n不錄影、不存截圖。只有手動啟用 AI 後，\n選定視窗圖片與所需文字才會傳送 API。\n暫停觀察不會刪除記憶。", "hint", True))
        controls_scroll = QScrollArea()
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setMinimumWidth(265)
        controls_scroll.setMaximumWidth(330)
        controls_scroll.setWidget(controls)
        columns.addWidget(controls_scroll)
        columns.setSizes([170, 620, 285])
        outer.addWidget(label("語音試用 0.3.1  /  自動收音 · 本機辨識 · 十三聲道                         ☾ 給日常留一盞燈", "footer"))
        self.update_controls()

    def apply_theme(self, *_):
        hour = taipei_now().hour
        selection = self.theme_combo.currentIndex()
        self.night = selection == 2 or (selection == 0 and is_night(hour))
        self.clock.setText(taipei_now().strftime("台灣時間 %H:%M  ·  ") + ("夜間" if self.night else "日間"))
        bg, border, text, panel, muted, button = (
            ("#302332", "#A67F9E", "#F9E9F3", "#392B3B", "#D1BACC", "#5D3D56") if self.night else
            ("#F5F0E9", "#CDB8C7", "#64475D", "#FFFCF8", "#786674", "#F1E4EC")
        )
        self.setStyleSheet(f"""
            QWidget {{ color: {text}; font-family: 'Microsoft JhengHei', 'Noto Sans CJK TC', sans-serif; font-size: 12px; }}
            QMainWindow, QMainWindow > QWidget {{ background: {bg}; }}
            QDialog {{ background: {bg}; }}
            QFrame#panel {{ background: {panel}; border: 1px solid {border}; border-radius: 18px; }}
            QLabel {{ background: transparent; border: none; }}
            QLabel#title {{ font-size: 27px; font-weight: 700; }}
            QLabel#moon {{ font-size: 46px; padding-right: 6px; }}
            QLabel#subtitle, QLabel#hint, QLabel#footer {{ color: {muted}; font-size: 11px; }}
            QLabel#sectionTitle {{ font-weight: 600; font-size: 14px; }}
            QLabel#resident {{ font-weight: 600; }}
            QLabel#status {{ background: {button}; border-radius: 10px; padding: 7px 12px; }}
            QLabel#preview {{ background: {bg}; border: 1px dashed {border}; border-radius: 12px; color: {muted}; }}
            QLabel#error {{ background: {'#603544' if self.night else '#F9E4E7'}; color: {'#FFFFFF' if self.night else '#822D48'}; padding: 9px; border-radius: 8px; }}
            QPushButton {{ background: {button}; border: 1px solid {border}; border-radius: 9px; padding: 9px 12px; }}
            QPushButton:hover {{ border: 1px solid {text}; }}
            QPushButton:disabled {{ color: {muted}; background: {bg}; }}
            QPushButton#primary {{ background: #79546D; color: #FFFFFF; border: none; font-weight: 600; }}
            QPushButton#primary:disabled {{ background: {button}; color: {muted}; }}
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QPlainTextEdit {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; padding: 7px; }}
            QProgressBar {{ background: {panel}; border: 1px solid {border}; border-radius: 5px; min-height: 10px; }}
            QProgressBar::chunk {{ background: {border}; border-radius: 4px; }}
            QComboBox QAbstractItemView {{ background: {panel}; color: {text}; selection-background-color: {button}; }}
            QTabWidget::pane {{ background: {panel}; border: 1px solid {border}; }}
            QTabBar::tab {{ background: {panel}; color: {text}; padding: 8px 12px; }}
            QTabBar::tab:selected {{ background: {button}; }}
            QScrollArea, QWidget#messages {{ background: {panel}; border: none; }}
            QScrollBar:vertical {{ width: 9px; background: {panel}; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 26px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
            QSplitter::handle {{ background: transparent; width: 14px; }}
        """)
        for card in self.cards:
            card.apply_theme(self.night)

    def toggle_topmost(self, enabled):
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        self.show()

    def refresh_windows(self):
        if self.state != MonitorState.STOPPED:
            return
        current = self.target
        try:
            windows = self.backend.list_windows()
        except Exception as exc:
            self.show_error(f"無法整理視窗清單：{exc}")
            return
        self.window_combo.blockSignals(True)
        self.window_combo.clear()
        self.window_combo.addItem("請選擇一個視窗", None)
        selected = 0
        for window in windows:
            self.window_combo.addItem(window.title, window)
            if current and window.handle == current.handle and window.pid == current.pid:
                selected = self.window_combo.count() - 1
        self.window_combo.setCurrentIndex(selected)
        self.window_combo.blockSignals(False)
        self.select_window()
        if not windows:
            self.show_error("目前沒有可選視窗。" if self.backend.label.startswith("Windows ·") else "目前系統不是 Windows；可用 --demo 驗證介面，展示模式不會擷取真實視窗。")

    def select_window(self, *_):
        if self.state != MonitorState.STOPPED:
            return
        self.target = self.window_combo.currentData()
        self.window_combo.setToolTip(self.target.title if self.target else "請先選定監看對象")
        self.target_label.setText(f"監看：{self.target.title}\nPID {self.target.pid}" if self.target else "尚未選定監看視窗")
        self.clear_preview()
        self.error_label.hide()
        self.update_controls()

    def start_monitoring(self):
        if not self.target or self.state == MonitorState.RUNNING:
            return
        self.generation += 1
        self.state = MonitorState.RUNNING
        self.error_label.hide()
        self.capture_status.setText("◌ 正在取得畫面" + (" · 展示" if self.backend.is_demo else ""))
        self.update_controls()
        self.timer.start(self.interval.value() * 1000)
        self.request_capture()

    def pause_monitoring(self):
        if self.state != MonitorState.RUNNING:
            return
        self.state = MonitorState.PAUSED
        self.cancel_capture()
        self.capture_status.setText("Ⅱ 已暫停 · 不再擷取")
        self.clear_preview("監看已暫停\n預覽已清除")
        self.update_controls()

    def stop_monitoring(self):
        self.state = MonitorState.STOPPED
        self.cancel_capture()
        self.capture_status.setText("○ 已停止")
        self.clear_preview()
        self.update_controls()

    def cancel_capture(self):
        if self.ai:
            self.ai.pause()
            self.ai.gate = ChangeGate()
        self.timer.stop()
        self.generation += 1
        if self.worker:
            self.worker.requestInterruption()

    def update_controls(self):
        stopped = self.state == MonitorState.STOPPED
        self.window_combo.setEnabled(stopped)
        self.refresh_button.setEnabled(stopped)
        self.start_button.setEnabled(bool(self.target) and self.state != MonitorState.RUNNING)
        self.start_button.setText("▷ 恢復陪伴" if self.state == MonitorState.PAUSED else "▷ 開始陪伴")
        self.pause_button.setEnabled(self.state == MonitorState.RUNNING)
        self.stop_button.setEnabled(not stopped)

    def update_interval(self, value):
        if self.state == MonitorState.RUNNING:
            self.timer.setInterval(value * 1000)

    def request_capture(self):
        if self.state != MonitorState.RUNNING or self.worker is not None:
            return
        self.worker = CaptureTask(self.backend, self.target, self.generation, self)
        self.worker.captured.connect(self.receive_frame)
        self.worker.failed.connect(self.capture_failed)
        self.worker.finished.connect(self.capture_finished)
        self.worker.start()

    def capture_finished(self):
        if self.worker:
            obsolete = self.worker.generation != self.generation
            self.worker.deleteLater()
            self.worker = None
            if obsolete and self.state == MonitorState.RUNNING:
                self.request_capture()

    def receive_frame(self, frame, generation):
        if generation != self.generation or self.state != MonitorState.RUNNING:
            return
        if frame.width <= 0 or frame.height <= 0 or frame.width * frame.height > 16_777_216 or len(frame.pixels) != frame.width * frame.height * 4:
            self.capture_failed("擷取資料尺寸不正確。", generation)
            return
        self.frame_image = QImage(frame.pixels, frame.width, frame.height, frame.width * 4, QImage.Format.Format_RGB32).copy()
        self.render_preview()
        self.preview_info.setText(f"{frame.width} × {frame.height} · {taipei_now():%H:%M:%S}\n" + ("合成測試畫面 · 非真實視窗" if self.backend.is_demo else "畫面僅存在記憶體中"))
        self.capture_status.setText("● 擷取中" + (" · 展示" if self.backend.is_demo else ""))
        if self.ai:
            self.ai.frame(self.frame_image)

    def render_preview(self):
        if self.frame_image is not None:
            pixmap = QPixmap.fromImage(self.frame_image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(pixmap)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            self.render_preview()

    def clear_preview(self, text="還沒有畫面\n開始後，只擷取選定視窗"):
        self.frame_image = None
        self.preview.clear()
        self.preview.setText(text)
        self.preview_info.setText("預覽不會寫入磁碟")

    def show_error(self, error):
        self.error_label.setText(error)
        self.error_label.show()

    def capture_failed(self, error, generation):
        if generation != self.generation or self.state != MonitorState.RUNNING:
            return
        self.stop_monitoring()
        self.capture_status.setText("! 擷取失敗 · 已停止")
        self.show_error(error + "\n不會改擷取桌面；請檢查目標後手動重試。")

    def add_message(self, name, text, translation="", character=None, user=False, system=False):
        try:
            record = self.store.append_message(
                speaker_id=character.id if character else ("user" if user else "system"),
                speaker_name=name, text=text, translation=translation, worldline=self.worldline,
                source_kind="test" if character else ("user" if user else "system"),
            )
        except Exception as exc:
            self.show_error(f"訊息未儲存，請保留輸入內容並檢查資料位置：{exc}")
            return False
        self.render_message(record)
        return True

    def render_message(self, record):
        character = next((c for c in self.characters if c.id == record["speaker_id"]), None)
        user = record["speaker_id"] == "user"
        bar = self.scroll.verticalScrollBar()
        was_at_bottom = bar.maximum() - bar.value() <= 24
        self.follow_latest = was_at_bottom
        recipients = json.loads(record.get("recipient_ids_json", "[]"))
        directed_name = record["speaker_name"] + (" → " + "、".join(next((c.name for c in self.characters if c.id == ident), "月鹿") for ident in recipients) if recipients else "")
        card = MessageCard(directed_name, record["text"], record["translation"], record["created_at"][11:16], character, user)
        relation = " · 接話對象：" + "、".join(next((f"{c.icon}{c.name}" for c in self.characters if c.id == ident), "月鹿") for ident in recipients) if recipients else ""
        card.setToolTip(record["created_at"] + relation + (f" · 回覆訊息 #{record['reply_to']}" if record.get("reply_to") else ""))
        card.apply_theme(self.night)
        row = QHBoxLayout()
        if user:
            row.addStretch(1)
        row.addWidget(card, 8)
        if not user:
            row.addStretch(1)
        self.message_layout.insertLayout(self.message_layout.count() - 1, row)
        self.cards.append(card)
        if character:
            self.roster_labels[character.id].setText("○ 已回覆")
        if was_at_bottom:
            QTimer.singleShot(0, self.scroll_to_bottom)

    def update_jump_button(self):
        bar = self.scroll.verticalScrollBar()
        self.jump_button.setVisible(bar.maximum() - bar.value() > 24)

    def scroll_position_changed(self, *_):
        if not self.scrolling_programmatically:
            bar = self.scroll.verticalScrollBar()
            self.follow_latest = bar.maximum() - bar.value() <= 24
        self.update_jump_button()

    def scroll_range_changed(self, *_):
        # Wrapped labels may settle across several Qt layout passes. Follow the
        # changing range only when the reader was already at the bottom.
        if self.follow_latest:
            self.scroll_to_bottom()
        else:
            self.update_jump_button()

    def scroll_to_bottom(self):
        bar = self.scroll.verticalScrollBar()
        self.follow_latest = True
        self.scrolling_programmatically = True
        bar.setValue(bar.maximum())
        self.scrolling_programmatically = False
        self.jump_button.hide()

    def send_message(self):
        text = self.chat_input.toPlainText().strip()
        if not text:
            return
        if len(text) > 4000:
            self.show_error("單則訊息上限為 4000 字；請縮短後再送出。")
            return
        if self.add_message("☾ 月鹿", text, user=True):
            self.chat_input.clear()
            if self.ai:
                self.ai.user_sent()

    def add_test_message(self):
        character = self.character_combo.currentData()
        if character.language == "en":
            text, translation = "This is a display test message.", "這是一則介面測試訊息。"
        elif character.language.startswith("ja"):
            # Language sample only; no invented persona or Kansai dialogue.
            text, translation = "これは表示テストのメッセージです。", "這是一則介面測試訊息。"
        else:
            text, translation = "這是一則介面測試訊息。今天，也一起待著。", ""
        self.add_message(f"{character.icon} {character.name} · 測試", text, translation, character)

    def inspect_character(self):
        character = self.character_combo.currentData()
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{character.icon} {character.name} · 已匯入設定")
        dialog.resize(680, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(f"{character.speech_label or character.name}  語言：{character.language}", "sectionTitle"))
        tabs = QTabWidget()
        layout.addWidget(tabs)
        sections = [
            ("原始角色內容", character.source_text or "尚未提供。"),
            ("外貌與身分", json.dumps({"identity": character.identity, "appearance": character.appearance}, ensure_ascii=False, indent=2)),
            ("關係／世界線", json.dumps({"active_worldline": self.worldline, "relationships": character.relationships}, ensure_ascii=False, indent=2)),
            ("待補欄位", "\n".join(character.pending_details) or "本次未列出特定待補項目；未提供的資料仍保留未知。"),
        ]
        for title, text in sections:
            editor = QPlainTextEdit()
            editor.setReadOnly(True)
            editor.setPlainText(text)
            tabs.addTab(editor, title)
        close = QPushButton("關閉")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def clear_chat(self):
        answer = QMessageBox.question(self, "刪除聊天紀錄？", "這會永久刪除目前世界線的聊天紀錄。\n角色設定、關係、事件與長期記憶會保留。\n建議先建立備份。是否刪除？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.ai:
            self.ai.pause()
        try:
            self.store.clear_messages(self.worldline, confirmed=True)
        except Exception as exc:
            self.show_error(f"刪除失敗：{exc}")
            return
        self.clear_chat_widgets()

    def load_history(self):
        self.clear_chat_widgets()
        for record in self.store.messages(self.worldline):
            self.render_message(record)

    def clear_chat_widgets(self):
        while self.message_layout.count() > 1:
            row = self.message_layout.takeAt(0).layout()
            while row.count():
                item = row.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            row.deleteLater()
        self.cards.clear()
        for state in self.roster_labels.values():
            state.setText("· 未出場")

    def manage_memory(self):
        from .memory_ui import MemoryDialog
        dialog = MemoryDialog(self.store, self)
        dialog.exec()
        if dialog.restored:
            self.characters = self.store.load_characters()
            self.worldline = self.store.settings()["active_worldline"]
            theme = self.theme_combo.currentIndex()
            self._build_ui()
            self.theme_combo.setCurrentIndex(theme)
            self.refresh_windows()
            self.load_history()
            self.apply_theme()
            if self.ai:
                self.ai.refresh()
            if self.voice:
                from .speech_ui import load_voice_config
                self.voice.config = load_voice_config(self.store)
                self.voice.refresh_devices()
                self.voice.refresh()

    def closeEvent(self, event):
        if self.voice:
            self.voice.close()
        self.stop_monitoring()
        self.theme_timer.stop()
        if self.ai:
            self.ai.close()
        if self.worker:
            # Native backend observes cancellation every 50 ms and kills its child.
            self.worker.wait()
        super().closeEvent(event)
