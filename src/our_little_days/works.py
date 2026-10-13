"""Local exact title aliases, separate from personas and autobiographical memory."""
from importlib.resources import files
import json
import re
import unicodedata

from .ai_cost import AIError


class WorkIdentityError(AIError):
    def __init__(self, reason):
        self.reason=reason
        super().__init__('作品辨識未通過：'+reason+'。整批未寫入聊天，不改寫台詞、不自動重試；費用已計入。')


def catalog():
    return json.loads(files('our_little_days').joinpath('data/work_catalog.json').read_text(encoding='utf-8'))['works']


def normalize_title(text):
    from opencc import OpenCC
    text=OpenCC('t2s').convert(unicodedata.normalize('NFKC',text))
    return re.sub(r'[\s《》「」『』【】!！?？、，,.。:：;；·・]', '',text).casefold()


def match_work(text):
    """Match complete supplied aliases only, never nearest-famous-title guessing."""
    normalized=normalize_title(text)
    matches=[]
    for work in catalog():
        aliases=[]
        for alias in work['aliases']:
            term=normalize_title(alias)
            start=normalized.find(term)
            if start<0:
                continue
            if normalized[:start].endswith(('不是','並非','并非','不是指','not')) or normalized[start+len(term):].startswith(('ではなく','じゃなく','ではない')):
                continue
            aliases.append(alias)
        if aliases:
            matches.append((work, max(aliases,key=len)))
    return matches[0] if len(matches)==1 else None


def work_context(store,worldline,current_input=None):
    text=current_input.get('text','') if current_input else ''
    match=match_work(text)
    focus=store.work_focus(worldline)
    if match:
        work,alias=match
        return {'work':work,'matched_alias':alias,'scope':'current_user_named_work',
                'persist_allowed':bool(current_input.get('saved'))}
    if focus:
        work=next((w for w in catalog() if w['id']==focus['work_id']),None)
        if work:
            followup=bool(text and re.search(r'這部|那部|这部|この作品|その作品|主角|主人公|作者|劇情|剧情',text))
            different=bool(re.search(r'《|「|『|遊戲|游戏|game|另一部|別の',text,re.I)) or any(normalize_title(alias) in normalize_title(text) for alias in work.get('distinct_from',[]))
            return {'work':work,'matched_alias':focus['matched_alias'],'scope':'current_work_followup' if followup and not different else 'previous_user_named_work',
                    'persist_allowed':False}
    return None


def check_work_identity(turn,context):
    if not context or context['scope'] not in {'current_user_named_work','current_work_followup'}:
        return
    work=context['work']
    if turn.get('work_id')!=work['id']:
        raise WorkIdentityError('未使用這一輪使用者提供的作品對照')
    for message in turn['messages']:
        for segment in message['segments']:
            for field in ('text','translation'):
                text=segment[field]
                # Permit explicit distinctions and comparisons; reject replacing
                # the current work with a different indexed identity.
                sentences=re.split(r'[。！？!?\n]',text)
                for sentence in sentences:
                    if any(normalize_title(alias) in normalize_title(sentence) for alias in work.get('distinct_from',[])):
                        if not re.search(r'不是|並非|并非|不同|比較|比较|比起|像|誤認|误认|認錯|认错|勘違い|間違|取り違|ではな|じゃな|違う|別の|と比|比べ|異なる|みたい|よう|not\b',sentence,re.I):
                            raise WorkIdentityError('把已辨識作品替換成另一部作品')
                    if re.search(r'(?:自創|自创|編造|编造).{0,12}(?:名稱|名字|表達|表达|作品|标题|標題)|(?:作品|名稱|名字|标题|標題).{0,12}(?:自創|自创|編造|编造)|君が創造した独自の表現|君が作った.*(?:題名|タイトル)',sentence):
                        if not re.search(r'不是|並非|并非|ではな|じゃな',sentence):
                            raise WorkIdentityError('把本機已有對照的作品當成使用者自創名稱')
