"""User-reported dialect and work identity regressions; no paid requests."""
from dataclasses import asdict,replace
import copy
import json
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from our_little_days.ai_cost import AIConfig
from our_little_days.ai_engine import VoiceContractError,build_payload,save_turn
from our_little_days.capture import UnsupportedBackend
from our_little_days.storage import SQLiteStore,StorageError
from our_little_days.ui import MainWindow
from our_little_days.works import WorkIdentityError,check_work_identity,match_work,work_context


def haru_turn(native,kind='speech'):
    return {'meaningful':False,'event_key':'','summary':'','important':False,'read_character_ids':[],
        'messages':[{'speaker_id':'haru','recipient_ids':['user'],'reply_to_index':None,'reply_to_message_id':None,
            'dialect':'standard_japanese','segments':[{'kind':kind,'text':native,'translation':'離線測試翻譯'}]}]}


@pytest.mark.parametrize('native',['アニメ？それは楽しみやな！月先輩は何が好きなんや？',
    'ほんまに楽しみ。','そうなんや。','月先輩、僕も行かへん。'])
@pytest.mark.parametrize('kind',['speech','narration'])
def test_haru_known_kansai_does_not_write_current_batch_but_order_can(native,kind,store):
    profiles=store.load_characters();value=haru_turn(native,kind)
    with pytest.raises(VoiceContractError) as caught:save_turn(store,'main',value,profiles,'user_reply')
    assert caught.value.character_id=='haru' and 'HARU' in str(caught.value)
    assert not store.messages() and not store.events()
    value['messages'][0].update(speaker_id='order',recipient_ids=['haru'])
    assert len(save_turn(store,'main',value,profiles,'quiet_chat'))==1


@pytest.mark.parametrize('native',['アニメ？楽しみだね！月先輩は何が好き？',
    'めっちゃ楽しみだね。','たいへんだったね。','やなぎさんの話だね。',
    '序は「ほんま、楽しみやな」と言った。'])
def test_haru_plain_bright_voice_quotes_and_standard_words_are_not_rewritten(native,store):
    profiles=store.load_characters();value=haru_turn(native);before=copy.deepcopy(value)
    record=save_turn(store,'main',value,profiles,'user_reply')[0]
    assert value==before and record['text'].startswith(native)


def test_haru_old_database_upgrade_only_changes_approved_rules_once_with_backup(store):
    current=next(c for c in store.load_characters() if c.id=='haru')
    old_rules={**current.language_rules,'register':'依角色語氣','custom_rule':'保留自訂'};old_rules.pop('dialect')
    legacy=replace(current,language_rules=old_rules);store.save_character(legacy)
    before=[asdict(c) for c in store.load_characters()];record=store.append_message(speaker_id='user',speaker_name='月',text='原聊天')
    store.add_memory('原記憶',character_id='haru');store.add_event('原事件')
    backup=store.apply_confirmed_haru_dialect_update();assert backup and backup.exists()
    after=[asdict(c) for c in store.load_characters()]
    for prior,new in zip(before,after):
        if prior['id']=='haru':
            assert {k:v for k,v in prior.items() if k!='language_rules'}=={k:v for k,v in new.items() if k!='language_rules'}
            assert new['language_rules']['dialect']=='standard' and new['language_rules']['custom_rule']=='保留自訂'
        else:assert prior==new
    prior=SQLiteStore(backup)
    try:assert asdict(next(c for c in prior.load_characters() if c.id=='haru'))==asdict(legacy)
    finally:prior.close()
    assert store.messages()[0]==record and store.memories()[0]['content']=='原記憶' and store.events()[0]['summary']=='原事件'
    custom=replace(next(c for c in store.load_characters() if c.id=='haru'),tone='後續自訂',language_rules={**current.language_rules,'dialect':'custom'})
    store.save_character(custom);assert store.apply_confirmed_haru_dialect_update() is None
    reopened=SQLiteStore(store.path)
    try:assert next(c for c in reopened.load_characters() if c.id=='haru')==custom
    finally:reopened.close()


@pytest.mark.parametrize('title',['螢火蟲的出嫁!','是螢火蟲的出嫁','萤火虫的出嫁','《ホタルの嫁入り》','螢火之嫁'])
def test_exact_aliases_recognized_locally_with_basic_identity_only(title):
    work,_=match_work(title)
    assert work['title_ja']=='ホタルの嫁入り' and work['medium']=='manga' and work['author']=='橘オレコ'
    assert work['id']=='hotaru-no-yomeiri' and work['unverified_details']
    assert 'plot' not in work


@pytest.mark.parametrize('title',['螢火蟲之墓','火垂るの墓','未知的作品','不是螢火蟲的出嫁','ホタルの嫁入りではなく別の作品'])
def test_no_fuzzy_relabel_or_negated_work_selection(title):
    assert match_work(title) is None


@pytest.mark.parametrize('native',['「蛍火蟲の墓」か。あの作品は重いね。',
    '月，那部《螢火蟲之墓》很悲傷。','君が創造した独自の表現かもしれないね。'])
def test_reported_work_substitution_and_invention_do_not_write_dialogue(native,store):
    context=work_context(store,'main',{'text':'螢火蟲的出嫁','saved':True})
    value=haru_turn(native);value['work_id']='hotaru-no-yomeiri'
    with pytest.raises(WorkIdentityError):save_turn(store,'main',value,store.load_characters(),'user_reply',named_work=context)
    assert not store.messages() and not store.events() and not store.memories()


def test_work_context_full_profiles_aliases_history_and_output_share_one_payload(store):
    wrong=store.append_message(speaker_id='shogo',speaker_name='聖護',text='「蛍火蟲の墓」か。',source_kind='ai')
    user=store.append_message(speaker_id='user',speaker_name='月',text='是螢火蟲的出嫁')
    profiles=store.load_characters();payload,upper=build_payload(AIConfig(),profiles,store,'main','user_reply')
    context=json.loads(payload['messages'][1]['content'][0]['text']);focus=json.loads(payload['messages'][-1]['content'])
    assert context['work_context']['scope']=='current_user_named_work'
    assert context['work_context']['work']['title_ja']=='ホタルの嫁入り'
    assert context['characters']==json.loads(json.dumps([asdict(c) for c in profiles]))
    assert context['recent_chat'][0]['text']==wrong['text'] and context['recent_chat'][0]['work_note']['not_current_work_evidence']
    assert 'work_note' not in store.messages()[0]
    assert focus['current_user_input']['text']==user['text'] and focus['work_context']==context['work_context']
    root=payload['response_format']['json_schema']['schema']
    assert list(root['properties'])[0]=='work_id' and root['properties']['work_id']['enum']==['hotaru-no-yomeiri']
    assert payload['max_completion_tokens']==2000 and len(payload['messages'])==4
    assert upper==len(json.dumps(payload,ensure_ascii=False).encode())+2048


def test_work_focus_reopen_backup_worldline_isolation_and_unsaved_voice_privacy(store,tmp_path):
    row=store.append_message(speaker_id='user',speaker_name='月',text='螢火蟲的出嫁')
    work,alias=match_work(row['text']);store.remember_named_work('main',work,alias,row['id'])
    assert work_context(store,'main')['scope']=='previous_user_named_work' and work_context(store,'glasshouse') is None
    assert work_context(store,'main',{'text':'這部的主角呢？','saved':True})['scope']=='current_work_followup'
    assert work_context(store,'main',{'text':'《另一部作品》的主角呢？','saved':True})['scope']=='previous_user_named_work'
    assert work_context(store,'main',{'text':'螢火蟲之墓的主角呢？','saved':True})['scope']=='previous_user_named_work'
    context=work_context(store,'main',{'text':'ホタルの嫁入り','saved':False})
    assert not context['persist_allowed'] and not store.events() and not store.memories()
    with pytest.raises(StorageError,match='本世界線'):store.remember_named_work('glasshouse',work,alias,row['id'])
    backup=store.backup(tmp_path/'works-backup.sqlite3');copy_store=SQLiteStore(backup)
    try:assert copy_store.work_focus('main')==store.work_focus('main')
    finally:copy_store.close()
    reopened=SQLiteStore(store.path)
    try:assert reopened.work_focus('main')==store.work_focus('main')
    finally:reopened.close()
    store.clear_messages('main',confirmed=True);assert store.work_focus('main') is None


def test_work_comparison_is_allowed_and_missing_identity_is_rejected(store):
    context=work_context(store,'main',{'text':'螢火蟲的出嫁','saved':True})
    value=haru_turn('「ホタルの嫁入り」のことだね。「火垂るの墓」とは違う作品だよ。');value['work_id']='hotaru-no-yomeiri'
    check_work_identity(value,context)
    del value['work_id']
    with pytest.raises(WorkIdentityError):check_work_identity(value,context)


def wait(predicate):
    end=time.monotonic()+5
    while not predicate() and time.monotonic()<end:QTest.qWait(10)
    assert predicate()


def test_free_work_viewer_reads_persistent_reference_without_keys_or_api(app,store,monkeypatch):
    from PySide6.QtWidgets import QDialog,QTabWidget
    import our_little_days.ai_ui as ui
    store.append_message(speaker_id='user',speaker_name='月',text='螢火蟲的出嫁')
    w=MainWindow(UnsupportedBackend(),store.load_characters(),store);seen=[]
    def forbidden(*a):raise AssertionError('Free viewer must not read keys or call API')
    monkeypatch.setattr(ui,'request_api',forbidden);monkeypatch.setattr(w.ai.keys,'read',forbidden)
    def inspect(dialog):
        tabs=dialog.findChild(QTabWidget);assert tabs.tabText(8)=='作品對照（與人格分開）'
        text=tabs.widget(8).toPlainText()
        assert 'ホタルの嫁入り' in text and '橘オレコ' in text and '原作類型：漫畫' in text
        assert '不呼叫 API' in text and '不是目前視窗畫面' in text
        seen.append(True);return QDialog.DialogCode.Accepted
    monkeypatch.setattr(QDialog,'exec',inspect)
    try:w.inspect_character();assert seen and not w.ai.ledger.totals()['month']['requests']
    finally:w.close();app.processEvents()


@pytest.mark.parametrize('wrong_kind',['dialect','work'])
def test_real_user_send_settles_rejected_batch_preserves_modes_and_focus_without_retry(app,store,monkeypatch,wrong_kind):
    import our_little_days.ai_ui as ui
    w=MainWindow(UnsupportedBackend(),store.load_characters(),store);c=w.ai;calls=[]
    monkeypatch.setattr(c.keys,'read',lambda:'mock-only');monkeypatch.setattr(c.keys,'exists',lambda:True)
    monkeypatch.setattr(QMessageBox,'information',lambda *a:None)
    c.config=replace(c.config,transmission_approved=True)
    for kind in ('reply_test','screen_test'):
        rid,_=c.ledger.reserve(c.config,kind,100);c.ledger.settle(rid,{'prompt_tokens':100,'completion_tokens':100},'mock');c.ledger.mark_test(c.config,kind,rid)
    def fake(key,payload,cancelled):
        calls.append(payload);context=json.loads(payload['messages'][1]['content'][0]['text'])
        assert context['work_context']['work']['id']=='hotaru-no-yomeiri'
        value=haru_turn('楽しみやな！' if wrong_kind=='dialect' else '「蛍火蟲の墓」は重い話だね。')
        value['work_id']='hotaru-no-yomeiri'
        return {'id':'offline-only','model':'mock-mini','usage':{'prompt_tokens':100,'completion_tokens':120},
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    monkeypatch.setattr(ui,'request_api',fake)
    try:
        c.enable(True);w.chat_input.setPlainText('螢火蟲的出嫁');w.send_message();wait(lambda:c.worker is None)
        assert len(calls)==1 and c.enabled and not c.failed_request
        assert not any(r['source_kind']=='ai' for r in store.messages())
        result=store.api_result('main');assert result['status']==('voice_rejected' if wrong_kind=='dialect' else 'work_rejected')
        assert c.ledger.totals()['month']['requests']==3 and c.ledger.totals()['month']['uncertain_twd']==0
        assert store.work_focus('main')['work_id']=='hotaru-no-yomeiri'
        if wrong_kind=='dialect':
            assert store.reply_diagnostic('main','haru')['warnings'][0]['character_id']=='haru'
            assert not store.reply_diagnostic('main','shogo')
    finally:w.close();app.processEvents()
