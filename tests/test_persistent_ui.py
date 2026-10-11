from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QFileDialog

from our_little_days.capture import DemoBackend
from our_little_days.memory_ui import MemoryDialog
from our_little_days.storage import SQLiteStore
from our_little_days.ui import MainWindow


def test_window_close_reopen_restores_history_without_duplicate_welcome(app, store):
    first = MainWindow(DemoBackend(), store.load_characters(), store)
    first.show()
    first.chat_input.setPlainText("重新開啟後仍在")
    first.send_message()
    first.character_combo.setCurrentIndex(11)
    first.add_test_message()
    original = store.messages()
    first.close()
    app.processEvents()
    path = store.path
    store.close()
    reopened = SQLiteStore(path)
    second = MainWindow(DemoBackend(), reopened.load_characters(), reopened)
    try:
        assert len(second.cards) == len(original) == 4
        assert reopened.messages() == original
        assert second.roster_labels["shogo"].text() == "· 未出場"  # Test bubbles do not count.
        assert second.frame_image is None
        assert not second.timer.isActive()
    finally:
        second.close()
        app.processEvents()
        reopened.close()


def test_cancel_clear_keeps_history_relationships_and_memories(app, store, monkeypatch):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    store.add_memory("永遠保留的測試記憶")
    count = len(window.cards)
    before = store.load_characters()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    try:
        window.clear_chat()
        assert len(window.cards) == len(store.messages()) == count
        window.pause_monitoring()
        window.stop_monitoring()
        assert store.memories()[0]["content"] == "永遠保留的測試記憶"
        assert store.load_characters() == before
    finally:
        window.close()


def test_storage_failure_does_not_erase_user_input(app, store, monkeypatch):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    initial = len(window.cards)
    def fail(**kwargs):
        raise OSError("磁碟不可寫入")
    monkeypatch.setattr(store, "append_message", fail)
    try:
        window.chat_input.setPlainText("請保留這段文字")
        window.send_message()
        assert window.chat_input.toPlainText() == "請保留這段文字"
        assert len(window.cards) == initial
        assert "訊息未儲存" in window.error_label.text()
    finally:
        window.close()


def test_memory_dialog_saves_manual_entries_with_worldline_scope(app, store):
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    dialog = MemoryDialog(store, window)
    try:
        dialog.event_input.setPlainText("已確認的手動事件")
        dialog.save_event()
        dialog.character_combo.setCurrentIndex(dialog.character_combo.findData("shogo"))
        dialog.memory_input.setPlainText("已確認的手動記憶")
        dialog.save_memory()
        assert store.events()[0]["summary"] == "已確認的手動事件"
        assert store.memories()[0]["character_id"] == "shogo"
        dialog.world_combo.setCurrentIndex(dialog.world_combo.findData("glasshouse"))
        assert "還沒有長期記憶" in dialog.memory_list.toPlainText()
        dialog.memory_input.setPlainText("AU 手動記憶")
        dialog.save_memory()
        assert len(store.memories()) == len(store.memories("glasshouse")) == 1
    finally:
        dialog.close()
        window.close()


def test_confirmed_restore_reloads_visible_history_and_stops_capture(app, store, tmp_path, monkeypatch):
    store.append_message(speaker_id="user", speaker_name="月鹿", text="備份內的訊息")
    snapshot = store.backup(tmp_path / "restore-ui.sqlite3")
    store.append_message(speaker_id="user", speaker_name="月鹿", text="備份之後的訊息")
    window = MainWindow(DemoBackend(), store.load_characters(), store)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(snapshot), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(MemoryDialog, "exec", lambda dialog: dialog.restore())
    try:
        window.window_combo.setCurrentIndex(1)
        window.start_monitoring()
        window.manage_memory()
        assert len(window.cards) == 1
        assert store.messages()[0]["text"] == "備份內的訊息"
        assert not window.timer.isActive()
        assert window.frame_image is None
    finally:
        window.close()
        app.processEvents()


def test_inspect_current_profile_and_explicit_repair_without_paid_request(app,store,monkeypatch):
    from dataclasses import replace,asdict
    from PySide6.QtWidgets import QDialog,QTabWidget,QPushButton
    window=MainWindow(DemoBackend(),store.load_characters(),store)
    try:
        window.character_combo.setCurrentIndex(11)
        original=next(c for c in store.load_characters() if c.id=='shogo')
        store.apply_confirmed_shogo_address_update()
        store.save_character(replace(original,user_address='本機舊稱呼',tone='舊語氣',language_rules={**original.language_rules,'register':'全敬語'}))
        before=[c for c in store.load_characters() if c.id!='shogo']
        store.append_message(speaker_id='shogo',speaker_name='聖護',text='小鳥ちゃん',source_kind='ai')
        messages=store.messages()
        checks=[]
        monkeypatch.setattr(QMessageBox,'information',lambda *args:None)
        def inspect(dialog):
            tabs=dialog.findChild(QTabWidget)
            assert tabs.tabText(0)=='目前套用設定'
            assert '平常稱呼：本機舊稱呼' in tabs.widget(0).toPlainText()  # Fresh SQLite, not cached combo.
            assert 'かみそりちゃん' in tabs.widget(2).toPlainText()
            assert '保留舊來源' in tabs.widget(0).toPlainText()
            assert '尚無新版' in tabs.widget(1).toPlainText()
            repair=next(b for b in dialog.findChildren(QPushButton) if b.text().startswith('套用聖護'))
            QTest.mouseClick(repair,Qt.MouseButton.LeftButton)
            assert '平常稱呼：月' in tabs.widget(0).toPlainText()
            assert '不使用敬語' in tabs.widget(0).toPlainText() and '自稱：僕' in tabs.widget(0).toPlainText() and '真的表現不喜歡' in tabs.widget(0).toPlainText()
            checks.append(True)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(QDialog,'exec',inspect)
        window.inspect_character()
        assert checks and window.ai.ledger.totals()['month']['requests']==0
        updated=next(c for c in store.load_characters() if c.id=='shogo')
        assert updated.user_address=='月' and updated.source_text==original.source_text
        assert updated.relationships==original.relationships and store.messages()==messages
        assert [c for c in store.load_characters() if c.id!='shogo']==before
        assert list((store.path.parent/'backups').glob('before-shogo-address-*.sqlite3'))
    finally:
        window.close();app.processEvents()


def test_roster_tracks_latest_input_with_explicit_read_and_reopen(app,store):
    import json
    from our_little_days.ai_engine import save_turn
    window=MainWindow(DemoBackend(),store.load_characters(),store)
    try:
        # Reproduce the screenshot: all thirteen historical display samples.
        for i in range(13):
            window.character_combo.setCurrentIndex(i)
            window.add_test_message()
        old=store.append_message(speaker_id='user',speaker_name='月',text='舊訊息')
        for c in store.load_characters():
            store.append_message(speaker_id=c.id,speaker_name=c.name,text='舊回覆',source_kind='ai',reply_to=old['id'])
        current=store.append_message(speaker_id='user',speaker_name='月',text='本輪只有聖護回覆')
        window.render_message(current)
        assert all(v.text()=='· 未出場' for v in window.roster_labels.values())
        turn={'read_character_ids':['reo'], 'messages':[{'speaker_id':'shogo','delivery':'everyday_plain','recipient_ids':['user'], 'reply_to_index':None,'reply_to_message_id':None,'segments':[{'text':'月、僕はここにいるよ。','translation':'月，我就在這裡。'}]}]}
        record=save_turn(store,'main',turn,store.load_characters(),'user_reply',current['id'])[0]
        window.render_message(record)
        assert window.roster_labels['shogo'].text()=='○ 已回覆'
        assert window.roster_labels['reo'].text()=='✓ 已讀未回'
        assert all(v.text()=='· 未出場' for k,v in window.roster_labels.items() if k not in {'shogo','reo'})
        assert store.roster_status('glasshouse')['replied']==[] and store.roster_status('glasshouse')['read']==[]
        reopened=SQLiteStore(store.path)
        try:assert reopened.roster_status()==store.roster_status()
        finally:reopened.close()
        newest=store.append_message(speaker_id='user',speaker_name='月',text='新的一輪')
        # A slow reply to the previous input must not mark the new round replied/read.
        save_turn(store,'main',turn,store.load_characters(),'user_reply',current['id'])
        window.render_message(newest)
        assert all(v.text()=='· 未出場' for v in window.roster_labels.values())
        assert store.messages()[0]['source_kind']=='system'  # No history reset.
    finally:
        window.close();app.processEvents()


def test_translation_display_hides_only_exact_adjacent_repeats_without_editing_sqlite(app,store):
    from PySide6.QtWidgets import QLabel
    from our_little_days.ui import display_message_text, MessageCard
    text='月、僕はここにいるよ。\n（ 月，我就在這裡。 ）\n（月，我就在這裡。）'
    record=store.append_message(speaker_id='shogo',speaker_name='聖護',text=text,source_kind='ai')
    window=MainWindow(DemoBackend(),store.load_characters(),store)
    try:
        shown=next(v.text() for v in window.cards[-1].findChildren(QLabel) if v.objectName()=='messageText')
        assert shown.count('我就在這裡')==1
        assert store.messages()[-1]['text']==text
        assert display_message_text('うん。\n（嗯。）\nうん。\n（嗯。）')[0].count('（嗯。）')==2
        assert display_message_text('日文\n（意思不同。）\n（另一種翻譯。）')[0].count('（')==2
        assert display_message_text('（そうだね。）\n（そうだね。）')[0].count('そうだね')==2
        assert display_message_text('うん。\n（嗯。）','嗯。')[1]==''
        user_card=MessageCard('月',text,'','10:00',user=True)
        try:
            assert next(v.text() for v in user_card.findChildren(QLabel) if v.objectName()=='messageText')==text
        finally:user_card.deleteLater()
    finally:
        window.close();app.processEvents()
