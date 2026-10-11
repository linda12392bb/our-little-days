"""Current-turn voice gating; no live API, rewriting or inferred personality."""
from dataclasses import asdict, replace
import json
import threading
import time

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from our_little_days.ai_engine import (SHOGO_DELIVERIES, VoiceContractError,
    build_payload, check_current_voice, save_turn)
from our_little_days.ai_cost import AIConfig
from our_little_days.capture import UnsupportedBackend
from our_little_days.storage import SQLiteStore
from our_little_days.ui import MainWindow


def turn(native, *, speaker='shogo', delivery='question_plain', kind='speech'):
    return {'meaningful':False,'event_key':'','summary':'','important':False,'messages':[
        {'speaker_id':speaker,'recipient_ids':['user'],'reply_to_index':None,'reply_to_message_id':None,
         'delivery':delivery,'segments':[{'kind':kind,'text':native,'translation':'離線測試翻譯'}]}]}


@pytest.mark.parametrize('native', [
    'それは、フィギュアスケートの羽生結弦のことを指していますか？',
    'この画面のログが見えますね。', '文字は読みにくいですが、見える。',
    '僕も気になりました。', 'まだ来ていませんから、待とうか。',
    '月、見えております。', 'はい、小鳥ちゃん、今日はどうする？'])
@pytest.mark.parametrize('segment_kind',['speech','narration'])
def test_known_wrong_voice_is_blocked_before_new_dialogue(native,segment_kind,store):
    profiles=store.load_characters();before=[asdict(c) for c in profiles]
    with pytest.raises(VoiceContractError):
        check_current_voice(turn(native,kind=segment_kind),profiles)
    with pytest.raises(VoiceContractError):
        save_turn(store,'main',turn(native,kind=segment_kind),profiles,'user_reply')
    assert not store.messages() and not store.events() and not store.memories()
    assert [asdict(c) for c in store.load_characters()]==before
    check_current_voice(turn(native,speaker='reo'),profiles)  # REO keeps polite speech.


@pytest.mark.parametrize('native', ['月、僕はここにいるよ。', '羽生結弦のことだね。',
    '既読無視か。別に、返事をしないことにも理由はあるだろうね。君はどう思う？',
    'ますます面白いね。', 'それでしか表せないものもある。',
    'REOは「はい、小鳥ちゃん、ご安心ください」と言った。'])
def test_plain_original_voice_and_quoted_other_person_are_not_rewritten(native,store):
    value=turn(native);before=json.dumps(value,ensure_ascii=False)
    check_current_voice(value,store.load_characters())
    assert json.dumps(value,ensure_ascii=False)==before


def test_generation_chooses_own_delivery_before_native_text_without_bad_examples(store):
    profiles=store.load_characters()
    old=store.append_message(speaker_id='shogo',speaker_name='聖護',text='指していますか？',source_kind='ai')
    payload,upper=build_payload(AIConfig(),profiles,store,'main','user_reply',current_input={'kind':'typed_text','text':'這是羽生結弦','saved':True})
    root=payload['response_format']['json_schema']['schema']
    assert root['$defs']['shogo_delivery']['enum']==list(SHOGO_DELIVERIES)
    for array in root['properties']['messages']['anyOf']:
        for branch in array['items']['anyOf']:
            fields=branch['properties']
            if fields['speaker_id'].get('enum')==['shogo']:
                assert list(fields).index('delivery')<list(fields).index('segments')
                assert 'delivery' in branch['required']
                description=root['$defs'][fields['segments']['$ref'].split('/')[-1]]['items']['properties']['text']['description']
                assert 'していますよ' not in description
            else:assert 'delivery' not in fields
    context=json.loads(payload['messages'][1]['content'][0]['text'])
    assert context['characters']==json.loads(json.dumps([asdict(c) for c in profiles]))
    assert context['recent_chat'][0]['id']==old['id'] and context['recent_chat'][0]['text']==old['text']
    assert context['recent_chat'][0]['voice_note']['do_not_imitate_voice']
    assert '最初から常体の文を作る' in payload['messages'][2]['content']
    assert payload['max_completion_tokens']==2000 and len(payload['messages'])==4
    assert upper==len(json.dumps(payload,ensure_ascii=False).encode())+2048
    for bad in (None,'polite_question'):
        with pytest.raises(VoiceContractError):check_current_voice(turn('羽生結弦だね。',delivery=bad),profiles)


def wait(predicate):
    deadline=time.monotonic()+5
    while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
    assert predicate()


def test_current_api_voice_failure_keeps_queued_user_and_original_memory_with_no_retry(app,store,monkeypatch):
    import our_little_days.ai_ui as ui
    w=MainWindow(UnsupportedBackend(),store.load_characters(),store);c=w.ai
    entered,release=threading.Event(),threading.Event();calls=[]
    old=store.append_message(speaker_id='shogo',speaker_name='聖護',text='過去の敬語です。',source_kind='ai')
    store.add_memory('既有記憶',character_id='shogo');profiles=store.load_characters()
    monkeypatch.setattr(c.keys,'read',lambda:'mock-not-a-real-key');monkeypatch.setattr(c.keys,'exists',lambda:True)
    monkeypatch.setattr(QMessageBox,'information',lambda *a:None)
    c.config=replace(c.config,transmission_approved=True)
    for kind in ('reply_test','screen_test'):
        rid,_=c.ledger.reserve(c.config,kind,100);c.ledger.settle(rid,{'prompt_tokens':100,'completion_tokens':100},'mock');c.ledger.mark_test(c.config,kind,rid)
    def fake(key,payload,cancelled):
        calls.append(payload)
        if len(calls)==1:
            entered.set()
            while not release.wait(.01):
                if cancelled():return {}
            value=turn('それは羽生結弦を指していますか？')
        else:
            assert len(calls)==2
            context=json.loads(payload['messages'][1]['content'][0]['text'])
            assert context['current_user_input']['text']=='還在嗎'
            value=turn('月、僕はここにいるよ。',delivery='everyday_plain')
        return {'id':'local-voice-probe','model':'mock-mini','usage':{'prompt_tokens':100,'completion_tokens':120},
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    monkeypatch.setattr(ui,'request_api',fake)
    try:
        c.enable(True)
        w.chat_input.setPlainText('這是羽生結弦');w.send_message();wait(entered.is_set)
        w.chat_input.setPlainText('還在嗎');w.send_message();assert c.pending_user
        release.set();wait(lambda:len(calls)==2 and c.worker is None)
        assert c.enabled and not c.failed_request and len(calls)==2
        records=store.messages();assert next(r for r in records if r['id']==old['id'])['text']==old['text']
        new=[r for r in records if r['source_kind']=='ai' and r['id']>old['id']]
        assert len(new)==1 and new[0]['text'].startswith('月、僕はここにいるよ。')
        assert new[0]['reply_to']==store.roster_status()['user_message_id']
        assert c.ledger.totals()['month']['requests']==4 and c.ledger.totals()['month']['uncertain_twd']==0
        assert store.memories()[0]['content']=='既有記憶' and store.load_characters()==profiles
        reopened=SQLiteStore(store.path)
        try:assert reopened.messages()==records and not reopened.reply_diagnostic('glasshouse')
        finally:reopened.close()
    finally:
        release.set();w.close();app.processEvents()
