"""Local AI controls; all tasks pass one persistent budget reservation gate."""
from dataclasses import replace
import json
import uuid

from PySide6.QtCore import QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from .ai_cost import AIConfig, AIError, UsageLedger
from .ai_engine import ChangeGate, build_payload, save_turn, validate_turn
from .ai_provider import WindowsKeyStore, request_api


class AITask(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, key, payload, parent):
        super().__init__(parent)
        self.key, self.payload = key, payload

    def run(self):
        try:
            value = request_api(self.key, self.payload, self.isInterruptionRequested)
            self.succeeded.emit(value)
        except Exception as exc:
            self.failed.emit(str(exc) if isinstance(exc, AIError) else 'AI 工作失敗；保留可能費用，不自動重試。')
        finally:
            self.key = None
            self.payload = None


class AIController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.ledger = UsageLedger(window.store.path.parent / 'api-usage.sqlite3')
        self.keys = WindowsKeyStore(window.store.path.parent)
        self.config = self.ledger.config()
        self.enabled = False
        self.autonomous = False
        self.worker = None
        self.request_id = None
        self.generation = 0
        self.pending_user = False
        self.gate = ChangeGate()
        self.last_note = 'AI 關閉 · 不會呼叫 API'
        self.failed_request = False
        self.closed = False

    def refresh(self):
        if self.closed:
            return
        w = self.window
        totals = self.ledger.totals()
        month, day = totals['month'], totals['day']
        remaining = max(0, self.config.monthly_budget_twd - month['twd'])
        w.ai_usage.setText(f"本月剩餘預算 NT${remaining:.2f}\n月預算 NT${self.config.monthly_budget_twd:.2f}\n本月估算 NT${month['twd']:.4f} · {month['requests']} 次\n今日估算 NT${day['twd']:.4f} · {day['requests']} 次\n待確認／預留 NT${month['uncertain_twd']:.4f}\n本月 token：輸入 {month['input_tokens']}／輸出 {month['output_tokens']}\n平台實際帳單：請至 OpenAI 帳務查看")
        w.api_status.setText(f'API {self.config.model} · ' + self.last_note if self.keys.exists() else 'API 離線 · 尚未串接')
        w.ai_note.setText(self.last_note)
        passed = self.ledger.tests_passed(self.config)
        w.ai_enable.blockSignals(True)
        w.ai_enable.setChecked(self.enabled)
        w.ai_enable.blockSignals(False)
        w.ai_auto.blockSignals(True)
        w.ai_auto.setChecked(self.autonomous)
        w.ai_auto.blockSignals(False)
        w.ai_auto.setEnabled(passed and self.enabled)
        w.ai_enable.setEnabled(passed or self.enabled)
        w.ai_screen_test.setEnabled(self.worker is None)
        w.ai_reply_test.setEnabled(self.worker is None)

    def settings(self):
        self.pause()
        dialog = AISettingsDialog(self)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        self.config = self.ledger.config()
        if accepted:
            self.last_note = '設定已保存 · 請先完成兩項單次成本測試'
        self.refresh()

    def pause(self):
        self.enabled = self.autonomous = False
        self.pending_user = False
        self.generation += 1
        if self.worker:
            self.worker.requestInterruption()
        self.last_note = 'AI 已暫停 · 擷取與記憶可繼續保留'
        self.refresh()

    def enable(self, checked):
        if checked and (not self.ledger.tests_passed(self.config) or not self.config.transmission_approved or not self.keys.exists()):
            self.last_note = '請設定金鑰，並完成畫面與角色兩項單次成本測試'
            self.enabled = False
        elif checked:
            self.enabled = True
            self.last_note = '文字 AI 已啟用 · 自主畫面分析需另勾選'
        else:
            self.pause()
        self.refresh()

    def auto(self, checked):
        self.autonomous = bool(checked and self.enabled and self.ledger.tests_passed(self.config))
        if self.autonomous:
            self.last_note = '自主 AI 已啟用 · 僅有明顯新畫面才分析'
        else:
            # Stop paid work when user turns off autonomous observation.
            self.pause()
        self.refresh()

    def frame(self, image):
        if not self.autonomous or self.worker or self.window.backend.is_demo:
            return
        if self.gate.ready(image, self.config.minimum_interval_seconds):
            self.start('screen_event', image=image)

    def user_sent(self):
        if not self.enabled:
            return
        if self.worker:
            self.pending_user = True
        else:
            self.start('user_reply')

    def start(self, kind, image=None):
        if self.worker:
            self.last_note = '已有 AI 請求進行中；不會同時再送出'
            self.refresh()
            return False
        if kind not in {'screen_test', 'reply_test', 'screen_event', 'user_reply'}:
            raise AIError('不支援的 AI 工作。')
        if kind == 'screen_event' and not self.autonomous or kind == 'user_reply' and not self.enabled:
            return False
        if kind in {'screen_test', 'screen_event'} and self.window.backend.is_demo:
            self.last_note = '合成展示畫面不會送至付費 API'
            self.refresh()
            return False
        try:
            if not self.config.transmission_approved:
                raise AIError('請先在本機 API 設定同意傳送所需資料並核對價格。')
            key = self.keys.read()
            worldline = self.window.worldline
            characters = self.window.store.load_characters()
            selected = self.window.character_combo.currentData().id if kind == 'reply_test' else None
            if image is None and kind == 'screen_test':
                image = self.window.frame_image
            payload, upper = build_payload(self.config, characters, self.window.store, worldline, kind, image, selected)
            request_id, reserve = self.ledger.reserve(self.config, kind, upper)
        except Exception as exc:
            self.enabled = self.autonomous = False
            self.last_note = str(exc) if isinstance(exc, AIError) else '本機 AI 資料／預算無法讀取；已停止呼叫，請保留資料。'
            self.refresh()
            return False
        self.request_id = request_id
        self.kind, self.selected = kind, selected
        self.request_characters, self.request_worldline = characters, worldline
        self.request_config = self.config
        self.request_generation = self.generation
        self.failed_request = False
        if image is not None:
            self.gate.acknowledge(image)
        self.worker = AITask(key, payload, self)
        self.worker.succeeded.connect(self.success)
        self.worker.failed.connect(self.failure)
        self.worker.finished.connect(self.finished)
        self.last_note = f'{kind} 進行中 · 已預留 NT${reserve:.4f}'
        self.refresh()
        self.worker.start()
        return True

    def success(self, response):
        if self.closed:
            return
        try:
            actual = self.ledger.settle(self.request_id, response.get('usage'), response.get('id'))
            if self.request_generation != self.generation:
                self.last_note = f'已取消顯示 · 已回報估算費用 NT${actual:.4f}'
                return
            turn = validate_turn(response, self.request_characters, self.kind, self.selected)
            if self.window.worldline != self.request_worldline:
                raise AIError('世界線已變更，忽略過時 AI 結果。')
            records = save_turn(self.window.store, self.request_worldline, turn, self.request_characters, self.kind)
            for record in records:
                self.window.render_message(record)
            if self.kind in {'screen_test', 'reply_test'}:
                self.ledger.mark_test(self.request_config, self.kind, self.request_id)
                detail = turn['summary'] if self.kind == 'screen_test' else '角色回覆已保存到聊天'
                QMessageBox.information(self.window, '單次實際用量測試', f"模型：{self.request_config.model}\nAPI 回報輸入 {response['usage']['prompt_tokens']} token／輸出 {response['usage']['completion_tokens']} token\n本機估算 NT${actual:.4f}\n{detail}\n\n這是實際請求的用量換算，平台實際帳單以 OpenAI 為準。")
            self.last_note = f'本次估算 NT${actual:.4f} · ' + ('沒有新事件／未發言' if not records and self.kind == 'screen_event' else '已完成')
        except Exception as exc:
            self.failure(str(exc) if isinstance(exc, AIError) else 'AI 結果儲存失敗，已停止 AI；保留用量與既有資料。')
        finally:
            self.refresh()

    def failure(self, error):
        if self.closed:
            return
        self.ledger.abandon(self.request_id)
        self.enabled = self.autonomous = False
        self.failed_request = True
        self.pending_user = False
        self.last_note = error
        self.refresh()

    def finished(self):
        if self.closed:
            return
        task = self.worker
        self.worker = None
        if task:
            task.deleteLater()
        self.refresh()
        if self.pending_user and self.enabled and not self.failed_request:
            self.pending_user = False
            QTimer.singleShot(0, self.user_sent)

    def close(self):
        if self.closed:
            return
        self.pause()
        self.closed = True
        if self.worker:
            self.worker.succeeded.disconnect(self.success)
            self.worker.failed.disconnect(self.failure)
            self.worker.finished.disconnect(self.finished)
            self.worker.wait()
            # Signal delivery may not run during shutdown: retain its reservation.
            self.ledger.abandon(self.request_id)
            self.worker.deleteLater()
            self.worker = None
        self.ledger.close()


class AISettingsDialog(QDialog):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.c = controller
        self.setWindowTitle('API 安全設定、預算與估算價格')
        self.resize(610, 660)
        layout = QVBoxLayout(self)
        note = QLabel('金鑰僅由 Windows DPAPI 加密存於本機帳號，不寫入程式或 GitHub。\nOpenAI API 計費與 ChatGPT 訂閱分開。\n所有畫面分析、角色回覆與成本測試共用月預算；不自動重試。')
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('已保存金鑰；留白保留' if controller.keys.exists() else '在自己電腦輸入 OpenAI API Key')
        form.addRow('API Key', self.key)
        self.model = QComboBox()
        self.model.addItems(['gpt-4.1-mini', 'gpt-4o-mini', 'gpt-4.1'])
        self.model.setCurrentText(controller.config.model)
        form.addRow('模型', self.model)
        self.budget = self.number(controller.config.monthly_budget_twd, 1, 1_000_000, 2)
        form.addRow('每月預算 NT$', self.budget)
        self.input_price = self.number(controller.config.input_usd_per_million, .0001, 1000, 4)
        self.output_price = self.number(controller.config.output_usd_per_million, .0001, 1000, 4)
        form.addRow('輸入 USD／百萬 token', self.input_price)
        form.addRow('輸出 USD／百萬 token', self.output_price)
        self.fx = self.number(controller.config.twd_per_usd, 1, 1000, 4)
        form.addRow('估算匯率 NT$／USD', self.fx)
        self.interval = QSpinBox()
        self.interval.setRange(60, 3600)
        self.interval.setValue(controller.config.minimum_interval_seconds)
        self.interval.setSuffix(' 秒')
        form.addRow('AI 最短間隔（非定時呼叫）', self.interval)
        self.output_limit = QSpinBox()
        self.output_limit.setRange(256, 6000)
        self.output_limit.setValue(controller.config.max_output_tokens)
        form.addRow('每次最大輸出 token', self.output_limit)
        self.model.currentTextChanged.connect(self.price_for_model)
        price_note = QLabel('預填價格為模型公開參考值，未即時核對；匯率 33 是估算值。\n請自行核對 https://openai.com/api/pricing/\n實際帳單：https://platform.openai.com/settings/organization/billing/overview\n剩餘預算＝月預算－程式估算費用－可能已計費的待確認金額。\n平台其他程式用量、稅金及匯差不在本機估算內。')
        price_note.setWordWrap(True)
        layout.addWidget(price_note)
        self.approved = QCheckBox('已核對估算價格；同意 AI 使用完整人格、目前世界線聊天／記憶及選定視窗圖片')
        self.approved.setChecked(controller.config.transmission_approved)
        layout.addWidget(self.approved)
        backup = QPushButton('另行備份 API 用量紀錄…')
        backup.clicked.connect(self.backup)
        layout.addWidget(backup)
        restore_usage = QPushButton('合併還原 API 用量備份…')
        restore_usage.clicked.connect(self.restore_usage)
        layout.addWidget(restore_usage)
        info = QLabel('用量紀錄與聊天備份分開，還原聊天不會倒退本月費用。\n每次啟動 AI 都先關閉；兩項成本測試成功後可手動啟用。\n暫停／取消已送出的請求仍可能被平台計費。')
        info.setWordWrap(True)
        layout.addWidget(info)
        save = QPushButton('保存設定（不會呼叫 API）')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        close = QPushButton('取消')
        close.clicked.connect(self.reject)
        layout.addWidget(close)

    @staticmethod
    def number(value, low, high, decimals):
        widget = QDoubleSpinBox()
        widget.setRange(low, high)
        widget.setDecimals(decimals)
        widget.setValue(value)
        return widget

    def price_for_model(self, model):
        prices = {'gpt-4.1-mini': (.40, 1.60), 'gpt-4o-mini': (.15, .60), 'gpt-4.1': (2.0, 8.0)}
        self.input_price.setValue(prices[model][0])
        self.output_price.setValue(prices[model][1])
        self.approved.setChecked(False)

    def save(self):
        try:
            new_key = self.key.text().strip()
            value = AIConfig(model=self.model.currentText(), input_usd_per_million=self.input_price.value(), output_usd_per_million=self.output_price.value(), twd_per_usd=self.fx.value(), monthly_budget_twd=self.budget.value(), max_output_tokens=self.output_limit.value(), minimum_interval_seconds=self.interval.value(), transmission_approved=self.approved.isChecked(), key_revision=uuid.uuid4().hex if new_key else self.c.config.key_revision)
            value.validate()
            # Invalidate prior cost tests before rotating the credential, even if
            # saving the key or config later fails. No partially rotated key can
            # silently retain the old successful test markers.
            self.c.ledger.save_config(value)
            if new_key:
                self.c.keys.save(new_key)
                self.key.clear()
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, '設定未完成', str(exc) if isinstance(exc, AIError) else '本機設定無法保存；請保留資料並檢查磁碟。')

    def backup(self):
        destination, _ = QFileDialog.getSaveFileName(self, 'API 用量備份（選擇新檔名）', 'API用量備份.sqlite3', 'SQLite (*.sqlite3)')
        if not destination:
            return
        try:
            self.c.ledger.backup(destination)
            QMessageBox.information(self, '已備份用量', '此檔不包含 API Key。還原採合併方式，避免倒退預算。')
        except Exception:
            QMessageBox.warning(self, '備份未完成', '請選擇尚未存在的新檔名，並確認磁碟可寫入。')

    def restore_usage(self):
        source, _ = QFileDialog.getOpenFileName(self, '選擇 API 用量備份（合併，不清空現有紀錄）', '', 'SQLite (*.sqlite3)')
        if not source:
            return
        try:
            changed = self.c.ledger.merge_backup(source)
            self.c.config = self.c.ledger.config()
            self.c.refresh()
            QMessageBox.information(self, '用量合併完成', f'已合併 {changed} 筆用量；現有紀錄保留，不重複計算同一請求。請重新開啟設定核對預算。')
            self.reject()
        except Exception as exc:
            QMessageBox.warning(self, '用量合併失敗', str(exc) if isinstance(exc, AIError) else '備份不相容；原紀錄保留。')
